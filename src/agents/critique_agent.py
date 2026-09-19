"""
Critique Agent — a SECOND model reviews the draft plan before it reaches
a clinician. Runs on the SAME Groq API as the reasoning agent, but a
DIFFERENT model — see reasoning_agent.py's docstring for the honest
caveat that both current defaults are OpenAI open-weight models, just
different sizes, not genuinely different companies. Swap in a real
cross-company model on either side if one becomes available and
confirmed working on this account.

CRITIQUE_MODEL_NAME: openai/gpt-oss-20b — confirmed; 
Check https://console.groq.com/docs/models for the current list
before changing it, since Groq's catalog changes over time.
"""

import os

from groq import Groq

CRITIQUE_MODEL = os.getenv("CRITIQUE_MODEL_NAME", "openai/gpt-oss-20b")

CRITIQUE_SYSTEM_PROMPT = """You are reviewing a draft care coordination plan before it reaches a clinician. You will be given the ORIGINAL retrieved chart context and the DRAFT PLAN generated from it by a different model.

CRITICAL DISTINCTION — read carefully before flagging anything:
- HALLUCINATION means the draft asserts something as an ALREADY-DOCUMENTED FACT that does not appear in the original context (e.g. "patient has diabetes" when diabetes is never mentioned).
- It is NOT hallucination for the draft to RECOMMEND A FUTURE ACTION based on something that IS documented (e.g. recommending "schedule a PCP visit" is a normal, expected care-coordination recommendation, not a hallucination, even though "schedule a PCP visit" itself doesn't appear in the chart — recommending next steps is the reasoning agent's entire job).
- Example of what NOT to flag: chart says "tingling in hands and feet" -> draft recommends "refer to neurology to evaluate the tingling." This is a reasonable recommendation grounded in a real documented symptom. Do not flag this.
- Example of what TO flag: chart says "tingling in hands and feet" -> draft states "patient has peripheral neuropathy" (asserting an undocumented diagnosis as fact) or recommends "start gabapentin 300mg" (a specific dosing decision beyond care-coordination scope).

Check specifically for:
1. HALLUCINATION (per the definition above — an undocumented fact asserted as true, NOT a reasonable recommendation)
2. OVERREACH — a SPECIFIC clinical decision (e.g. a drug name + dosage, a diagnosis asserted as confirmed) beyond general care-coordination actions like "schedule a visit," "refer to specialist," "reconcile medications"
3. GLOSSED-OVER GAPS — did the original context have a category marked "(no relevant documentation found)" that the draft failed to mention as a gap?

Respond in exactly this format:
STATUS: PASS or FLAGGED
ISSUES: (list each specific issue found, quoting the problematic text — or "None" if PASS)
SUMMARY: (one plain-language sentence for the clinician)"""


def critique_plan(patient_context_summary: str, draft_plan: str) -> str:
    """
    Raises RuntimeError with clear setup instructions if the critique
    model's API key is missing — same fail-loud pattern as the reasoning
    agent. Checks CRITIQUE_MODEL_API_KEY first, falling back to
    GROQ_API_KEY since both models live on the same Groq account.
    """
    api_key = os.getenv("CRITIQUE_MODEL_API_KEY") or os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "CRITIQUE_MODEL_API_KEY not set. Add it to your .env file as:\n"
            "  CRITIQUE_MODEL_API_KEY=your_key_here\n"
            "(Can reuse your GROQ_API_KEY since both models are on Groq.)"
        )

    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=CRITIQUE_MODEL,
        messages=[
            {"role": "system", "content": CRITIQUE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"ORIGINAL CONTEXT:\n{patient_context_summary}\n\nDRAFT PLAN:\n{draft_plan}",
            },
        ],
        temperature=0.1,  # even lower than reasoning — this is a checking task, not drafting
    )
    return response.choices[0].message.content


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv
    load_dotenv()  # standalone runs need this explicitly

    sys.path.insert(0, ".")
    from src.agents.reasoning_agent import draft_care_plan
    from src.agents.retrieval_agent import retrieve_patient_context

    if len(sys.argv) < 2:
        print("Usage: python3 -m src.agents.critique_agent <patient_id>")
        sys.exit(1)

    context = retrieve_patient_context(sys.argv[1])
    plan = draft_care_plan(context.summary_text())
    print("=== DRAFT PLAN ===")
    print(plan)
    print()
    critique = critique_plan(context.summary_text(), plan)
    print("=== CRITIQUE ===")
    print(critique)