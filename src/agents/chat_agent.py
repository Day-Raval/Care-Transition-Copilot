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
from src.utils.runtime import LLM_TIMEOUT_SECONDS

CHAT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
MAX_TOOL_ROUNDS = 6

CHAT_SYSTEM_PROMPT = """You are a clinical care-coordination assistant. You can call tools to look up a patient's readmission risk assessment or search their discharge chart, but only when the question actually requires it — don't call a tool just because it's available.

If the user refers to a patient by NAME rather than by patient_id, call find_patient_by_name FIRST to resolve it to a patient_id before calling any other tool. If that returns multiple matches, ask the user which patient they mean rather than guessing.

For open-ended questions about a patient's overall situation, whether concern is warranted, or anything requiring a full picture: ALWAYS call BOTH assess_readmission_risk AND search_patient_chart. Risk score alone doesn't tell you what's documented, and chart content alone doesn't tell you the model's actual risk assessment — a "should I be worried" answer built on only one of the two is incomplete and can be misleading. When searching the chart for such questions, call search_patient_chart MULTIPLE times with different specific queries (e.g. once for medications, once for comorbidities, once for procedures) rather than once with a vague query.

When you do use tool results, ground your answer in exactly what the tools returned. If a tool genuinely returns "no relevant documentation found" after a well-targeted query, say that plainly rather than guessing. If you're asked something the tools can't answer, say so rather than speculating.

Format the final answer as a clean documented note, not a raw dump. Use short section labels such as "Answer", "Evidence", "Gaps", and "Next steps" when they fit the question. Use hyphen bullets for lists, avoid decorative asterisks, and keep paragraphs concise."""
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
        response = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.2,
        )
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
