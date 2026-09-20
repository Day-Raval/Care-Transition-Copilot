"""
Orchestrator — the LangGraph pipeline wiring the risk model and the
agent pipeline together for the first time.

risk_assessment (real, calls the Model Serving API)
    -> [conditional] low risk: short summary, skip the rest
    -> [conditional] medium/high risk: retrieval (dynamic, informed by
       admission_reason + risk) -> reasoning (Groq) -> critique (Groq,
       different model)

The conditional routing is the concrete form of "the agent reasons about
when to call the model / do more work" — a low-risk patient doesn't need
a full chart review and drafted care plan, so the pipeline genuinely
skips that work rather than always running everything regardless of need.

Both reasoning and critique receive the same risk-assessment context
line — confirmed via testing that omitting it from critique caused a
real false positive (critique flagging the risk percentile itself as
"undocumented," since it had no way to know the number came from an
already-validated model rather than being invented by reasoning).

Each node fails loudly with a clear setup message if something required
is missing (API not running, API key not set), rather than silently
falling back to placeholder output.
"""

import sys
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

sys.path.insert(0, ".")
from src.agents.critique_agent import critique_plan
from src.agents.reasoning_agent import draft_care_plan
from src.agents.retrieval_agent import generate_dynamic_categories, retrieve_patient_context
from src.agents.risk_tool import assess_risk

LOW_RISK_CATEGORY = "low"  # matches src/api/main.py's _categorize()


class PipelineState(TypedDict):
    patient_id: str
    risk_score: float
    risk_percentile: float
    risk_category: str
    admission_reason: str
    patient_context_summary: str
    categories_with_no_match: list[str]
    draft_plan: str
    critique_notes: str
    final_summary: str


def _risk_context_line(state: PipelineState) -> str:
    """Shared by both reasoning_node and critique_node so they always
    see identical risk framing — no risk of the two drifting apart."""
    return (
        f"[Risk assessment: {state['risk_category'].upper()} risk, "
        f"{state['risk_percentile']:.0f}th percentile]\n\n"
    )


def risk_assessment_node(state: PipelineState) -> dict:
    result = assess_risk(state["patient_id"])
    return {
        "risk_score": result["risk_score"],
        "risk_percentile": result["risk_percentile"],
        "risk_category": result["risk_category"],
        "admission_reason": result["admission_reason"],
    }


def route_by_risk(state: PipelineState) -> str:
    """The actual decision point: skip the expensive pipeline for
    low-risk patients rather than running it unconditionally."""
    return "low_risk_summary" if state["risk_category"] == LOW_RISK_CATEGORY else "retrieval"


def low_risk_summary_node(state: PipelineState) -> dict:
    """
    Deliberately templated, not LLM-generated — this is just relaying a
    number and a threshold, not synthesizing new content, so a fixed
    template is safer and cheaper than an LLM call for this case.
    """
    summary = (
        f"Patient assessed as LOW risk (percentile {state['risk_percentile']:.0f}, "
        f"score {state['risk_score']:.3f}). Full chart review and care-plan drafting "
        f"were skipped — routine discharge follow-up is likely sufficient. "
        f"Escalate to full review if the patient's status changes."
    )
    return {"final_summary": summary}


def retrieval_node(state: PipelineState) -> dict:
    categories = generate_dynamic_categories(
        admission_reason=state["admission_reason"],
        risk_info={"risk_category": state["risk_category"]},
    )
    context = retrieve_patient_context(state["patient_id"], categories=categories)
    return {
        "patient_context_summary": context.summary_text(),
        "categories_with_no_match": context.categories_with_no_match,
    }


def reasoning_node(state: PipelineState) -> dict:
    plan = draft_care_plan(_risk_context_line(state) + state["patient_context_summary"])
    return {"draft_plan": plan}


def critique_node(state: PipelineState) -> dict:
    notes = critique_plan(
        state["patient_context_summary"],
        state["draft_plan"],
        risk_context=_risk_context_line(state),
    )
    return {"critique_notes": notes, "final_summary": state["draft_plan"]}


def build_graph():
    graph = StateGraph(PipelineState)

    graph.add_node("risk_assessment", risk_assessment_node)
    graph.add_node("low_risk_summary", low_risk_summary_node)
    graph.add_node("retrieval", retrieval_node)
    graph.add_node("reasoning", reasoning_node)
    graph.add_node("critique", critique_node)

    graph.add_edge(START, "risk_assessment")
    graph.add_conditional_edges(
        "risk_assessment",
        route_by_risk,
        {"low_risk_summary": "low_risk_summary", "retrieval": "retrieval"},
    )
    graph.add_edge("low_risk_summary", END)
    graph.add_edge("retrieval", "reasoning")
    graph.add_edge("reasoning", "critique")
    graph.add_edge("critique", END)

    return graph.compile()


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    if len(sys.argv) < 2:
        print("Usage: python3 -m src.agents.orchestrator <patient_id>")
        sys.exit(1)

    patient_id = sys.argv[1]
    app = build_graph()
    result = app.invoke({"patient_id": patient_id})

    print("=" * 70)
    print(f"RISK ASSESSMENT: {result['risk_category'].upper()} "
          f"(percentile {result['risk_percentile']:.0f}, score {result['risk_score']:.3f})")
    print("=" * 70)

    if result["risk_category"] == LOW_RISK_CATEGORY:
        print(result["final_summary"])
    else:
        print("\n" + "=" * 70)
        print("RETRIEVAL")
        print("=" * 70)
        print(result["patient_context_summary"])
        if result["categories_with_no_match"]:
            print(f"No documented match: {result['categories_with_no_match']}")

        print("\n" + "=" * 70)
        print("REASONING (Groq)")
        print("=" * 70)
        print(result["draft_plan"])

        print("\n" + "=" * 70)
        print("CRITIQUE (Groq, different model)")
        print("=" * 70)
        print(result["critique_notes"])