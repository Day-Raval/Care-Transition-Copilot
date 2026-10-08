"""
Chat Agent — an LLM decides, via real function-calling, whether and
which tools to invoke to answer a free-form question about a patient.

This is genuinely different from orchestrator.py: instead of a fixed
sequence of nodes, the model itself chooses whether to call
assess_readmission_risk, search_patient_chart, both, or neither, based
on what the user actually asked. "What's this patient's risk level?"
only needs the risk tool. "What medications are they on?" only needs
chart search. "Should we be worried about them?" might reasonably need
both.

Auditability is kept even in this more flexible mode: every tool call
the model makes is logged (name + arguments + result) and returned
alongside the final answer, not hidden inside the model's reasoning.
Every dispatched tool call goes through the exact same functions the
deterministic orchestrator uses (see tools.py) — there's no separate,
unaudited code path just because this interface is more flexible.

Caps tool-calling rounds at MAX_TOOL_ROUNDS to prevent an unbounded
loop if the model keeps requesting tools indefinitely.
"""

import json
import os
import sys

from groq import Groq

sys.path.insert(0, ".")
from src.agents.tools import TOOLS, dispatch_tool_call
from src.api.observability import llm_call, safe_trace_inputs, trace_block
from src.utils.runtime import LLM_TIMEOUT_SECONDS

CHAT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
MAX_TOOL_ROUNDS = 6

CHAT_SYSTEM_PROMPT = """You are a clinical care-coordination assistant. You can call tools to look up a patient's readmission risk assessment or search their discharge chart, but only when the question actually requires it — don't call a tool just because it's available.

If the user asks a general clinical or workflow question that does not need a specific chart, answer generally without tools.

If the user refers to a patient by name, call find_patient_by_name FIRST to resolve it internally before calling any other tool. If the user message says the patient has already been resolved to an internal patient_id, do not call find_patient_by_name again; use that patient_id for tools. If name lookup returns multiple matches, give a brief general answer to the user's question first, then ask which patient they mean by name or non-identifying context rather than asking for an ID.

For open-ended questions about a patient's overall situation, whether concern is warranted, or anything requiring a full picture: call assess_readmission_risk and one targeted search_patient_chart query. Add a second chart search only if the first result is not enough to answer the user's actual question.

When you do use tool results, ground your answer in exactly what the tools returned. If a tool genuinely returns "no relevant documentation found" after a well-targeted query, say that plainly rather than guessing. If you're asked something the tools can't answer, say so rather than speculating.

When discussing readmission risk in chat, do not mention numeric percentiles or raw model scores. Translate the risk category into plain-language care-transition urgency.

Never ask the user for patient_id or patient_ref values. Never include patient_id or patient_ref values in the final answer. Refer to the patient by name or as "this patient" instead.

Format the final answer as a concise clinical note, not a raw dump. For simple questions, answer directly without section headings. Use headings like "Evidence" or "Gaps" only when the user asks for source detail or when missing documentation materially changes the answer. Use hyphen bullets for lists, avoid decorative asterisks, and keep paragraphs concise."""
def ask(question: str) -> dict:
    """
    Runs the tool-calling loop. Returns {"answer": str, "tool_calls": [...]}
    — the tool_calls log is the audit trail: exactly what was called,
    with what arguments, and what came back.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY not set. Add it to your .env file as:\n  GROQ_API_KEY=your_key_here"
        )

    client = Groq(api_key=api_key, timeout=LLM_TIMEOUT_SECONDS)
    messages = [
        {"role": "system", "content": CHAT_SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    tool_call_log = []

    for _ in range(MAX_TOOL_ROUNDS):
        with (
            llm_call("chat", CHAT_MODEL),
            trace_block(
                "Chat LLM",
                run_type="llm",
                inputs=safe_trace_inputs(
                    model=CHAT_MODEL,
                    round=len(tool_call_log) + 1,
                    message_count=len(messages),
                ),
            ) as trace,
        ):
            response = client.chat.completions.create(
                model=CHAT_MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.2,
            )
            if trace:
                message = response.choices[0].message
                trace.end(outputs=safe_trace_inputs(
                    answer_chars=len(message.content or ""),
                    requested_tools=len(message.tool_calls or []),
                ))
        message = response.choices[0].message

        if not message.tool_calls:
            return {"answer": message.content, "tool_calls": tool_call_log}

        messages.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in message.tool_calls
            ],
        })

        for tool_call in message.tool_calls:
            name = tool_call.function.name
            arguments = json.loads(tool_call.function.arguments)
            try:
                result = dispatch_tool_call(name, arguments)
            except Exception as e:
                result = f"Tool error: {e}"

            tool_call_log.append({"name": name, "arguments": arguments, "result": result})
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

    raise RuntimeError(f"Exceeded {MAX_TOOL_ROUNDS} tool-calling rounds without a final answer.")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    if len(sys.argv) < 2:
        print('Usage: python3 -m src.agents.chat_agent "<question>"')
        sys.exit(1)

    result = ask(sys.argv[1])

    print("=== TOOL CALLS ===")
    if not result["tool_calls"]:
        print("(none — answered directly)")
    for tc in result["tool_calls"]:
        print(f"  {tc['name']}({tc['arguments']})")
        print(f"    -> {tc['result'][:150]}")

    print()
    print("=== ANSWER ===")
    print(result["answer"])
