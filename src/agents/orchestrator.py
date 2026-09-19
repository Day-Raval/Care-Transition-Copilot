"""
Orchestrator — the LangGraph skeleton wiring the agent pipeline together.

retrieval (real) -> reasoning (real, Groq) -> critique (real, Groq, different model).

Each node fails loudly with a clear setup message rather than
silently falling back to placeholder output — a graph run should never
be mistaken for a real result if a real result wasn't actually produced.
"""

import sys
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

sys.path.insert(0, ".")
from src.agents.critique_agent import critique_plan
from src.agents.reasoning_agent import draft_care_plan
from src.agents.retrieval_agent import retrieve_patient_context


class PipelineState(TypedDict):
    patient_id: str
    patient_context_summary: str
    categories_with_no_match: list[str]
    draft_plan: str
    critique_notes: str


def retrieval_node(state: PipelineState) -> dict:
    context = retrieve_patient_context(state["patient_id"])
    return {
        "patient_context_summary": context.summary_text(),
        "categories_with_no_match": context.categories_with_no_match,
    }


def reasoning_node(state: PipelineState) -> dict:
    plan = draft_care_plan(state["patient_context_summary"])
    return {"draft_plan": plan}


def critique_node(state: PipelineState) -> dict:
    notes = critique_plan(state["patient_context_summary"], state["draft_plan"])
    return {"critique_notes": notes}


def build_graph():
    graph = StateGraph(PipelineState)

    graph.add_node("retrieval", retrieval_node)
    graph.add_node("reasoning", reasoning_node)
    graph.add_node("critique", critique_node)

    graph.add_edge(START, "retrieval")
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
    print("RETRIEVAL")
    print("=" * 70)
    print(result["patient_context_summary"])
    if result["categories_with_no_match"]:
        print(f"No documented match: {result['categories_with_no_match']}")

    print("=" * 70)
    print("REASONING (Groq)")
    print("=" * 70)
    print(result["draft_plan"])
    print()

    print("=" * 70)
    print("CRITIQUE (Groq, different model)")
    print("=" * 70)
    print(result["critique_notes"])