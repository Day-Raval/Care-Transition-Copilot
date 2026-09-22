"""
Reasoning Agent — drafts a follow-up care coordination plan from the
retrieved patient context, using an LLM (Groq, per the original
architecture — fast inference, good fit for a drafting step that a
second model and a clinician both review before anything happens).

GROQ_MODEL default: openai/gpt-oss-120b — confirmed available and working
on this project's Groq account (verified via direct API call). Kept
deliberately different from the critique agent's default (openai/gpt-oss-20b)
is NOT a different company here (both are OpenAI's open-weight models,
just different sizes) — a real limitation on independence between the
two agents, worth knowing rather than pretending otherwise. Swap either
model name if a genuinely different-company model becomes available on
this account later (verify at console.groq.com/docs/models first).

This is a DRAFT generator, not a decision-maker. The system prompt is
built specifically around the two failure modes that matter most here:
1. Hallucination — inventing a medication/diagnosis/procedure not in the
   actual retrieved context. The critique agent checks for this
   independently, but the prompt should minimize it happening at all.
2. Silently overstating what the chart supports — if a topic is not
   found in the retrieved chart context, do not invent it or present it
   as documented.
"""

import os

from groq import Groq

from src.utils.runtime import LLM_TIMEOUT_SECONDS

GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

REASONING_SYSTEM_PROMPT = """You are a clinical care coordination assistant. You draft follow-up care plans for patients recently discharged from the hospital who have been flagged as at elevated risk of 30-day readmission.

You will be given retrieved chart context, organized by category. Some categories may say "(no relevant documentation found)" — this means the information genuinely is not documented, not that you should guess, infer, or fill the gap.

Draft a concise, actionable follow-up plan with exactly these three sections:

1. KEY RISK FACTORS TO MONITOR — grounded ONLY in what appears in the retrieved context below. Do not add a risk factor that isn't explicitly present in the text you were given.

2. RECOMMENDED FOLLOW-UP ACTIONS — general care-coordination actions (e.g. timing of a PCP follow-up visit, medication reconciliation, home health referral). Do NOT recommend a specific medication change, dosage, or diagnosis — that is outside your role and must be left to the clinician.

3. ADDITIONAL REVIEW NOTES — briefly mention anything the reviewer should double-check or confirm before acting. Do not include the literal phrase "no relevant documentation found"; write a plain, useful note instead, or write "No additional review notes." if nothing stands out.

This is a DRAFT. It will be reviewed by a second, independent model and then a human clinician before any action is taken. Do not present it as a final decision."""


def draft_care_plan(patient_context_summary: str) -> str:
    """
    Raises RuntimeError with clear setup instructions if GROQ_API_KEY is
    missing, rather than failing with an opaque SDK error — consistent
    with this project's "fail loudly with actionable guidance" pattern
    (see the API's production_run_id check, the fairness audit's event
    threshold, etc.).
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY not set. add it to your .env file as:\n  GROQ_API_KEY=your_key_here"
        )

    client = Groq(api_key=api_key, timeout=LLM_TIMEOUT_SECONDS)
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": REASONING_SYSTEM_PROMPT},
            {"role": "user", "content": patient_context_summary},
        ],
        temperature=0.2,  # low — this is a grounded drafting task, not creative writing
    )
    return response.choices[0].message.content


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv
    load_dotenv()  # standalone runs need this explicitly -- only

    sys.path.insert(0, ".")
    from src.agents.retrieval_agent import retrieve_patient_context

    if len(sys.argv) < 2:
        print("Usage: python3 -m src.agents.reasoning_agent <patient_id>")
        sys.exit(1)

    context = retrieve_patient_context(sys.argv[1])
    plan = draft_care_plan(context.summary_text())
    print(plan)
