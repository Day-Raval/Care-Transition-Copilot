"""
Retrieval Agent — the first node in the orchestration pipeline.

Given a patient_id (already risk-flagged by the Day 6 model), retrieves
structured, categorized chart context from the vector store: not one
free-text search, but several targeted searches across the categories a
clinician reviewing this patient would actually want to see, each
patient-scoped and relevance-thresholded (see src/retrieval/query_store.py).

Deliberately NOT using an LLM to generate the search queries — a fixed
set of clinically-motivated categories keeps this agent fully testable
and free of an external API dependency, consistent with the rest of this
project's MVP approach (local ChromaDB, no paid embedding API required
for this step). The reasoning agent (next node) is where an LLM actually
gets involved, working FROM this agent's structured output rather than
raw search results.

A category with no relevant match is tracked explicitly, not silently
dropped — "no documented follow-up plan found" is itself useful signal
for the reasoning agent (and eventually the clinician), not a gap to
paper over.
"""

import sys
from dataclasses import dataclass, field

sys.path.insert(0, ".")
from src.retrieval.query_store import get_collection, retrieve_relevant_context

RETRIEVAL_CATEGORIES = {
    "admission_reason": "chief complaint and reason for hospitalization",
    "comorbidities": "chronic conditions and comorbidities in patient history",
    "medications": "current medications at discharge",
    "procedures_and_plan": "procedures conducted and treatment plan during this admission",
    "follow_up": "follow-up care instructions and discharge planning",
}


@dataclass
class RetrievedItem:
    category: str
    section: str
    text: str
    distance: float
    encounter_id: str  # added this for check unique notes



@dataclass
class PatientContext:
    patient_id: str
    items: list[RetrievedItem] = field(default_factory=list)
    categories_with_no_match: list[str] = field(default_factory=list)

    def summary_text(self) -> str:
        """
        Flattened, readable text block — this is what actually gets
        handed to the reasoning agent as its input context, not the raw
        dataclass. Organized by category so the reasoning agent (and a
        human debugging this) sees the same structure every time.
        """
        lines = [f"Chart context for patient {self.patient_id}:\n"]
        by_category: dict[str, list[RetrievedItem]] = {}
        for item in self.items:
            by_category.setdefault(item.category, []).append(item)

        for category, label in RETRIEVAL_CATEGORIES.items():
            lines.append(f"## {label.capitalize()}")
            if category in by_category:
                for item in by_category[category]:
                    lines.append(f"- [{item.section}, distance={item.distance:.3f}] {item.text}")
            else:
                lines.append("(no relevant documentation found)")
            lines.append("")

        return "\n".join(lines)


def retrieve_patient_context(patient_id: str, collection=None, n_results_per_category: int = 2) -> PatientContext:
    """
    Runs every RETRIEVAL_CATEGORIES query as a separate patient-scoped
    search and assembles the results into one structured context object.
    """
    collection = collection or get_collection()
    context = PatientContext(patient_id=patient_id)

    for category, query_text in RETRIEVAL_CATEGORIES.items():
        results = retrieve_relevant_context(collection, patient_id, query_text, n_results=n_results_per_category)
        if results is None:
            context.categories_with_no_match.append(category)
            continue
        for r in results:
            context.items.append(RetrievedItem(
                category=category,
                section=r["section"],
                text=r["text"],
                distance=r["distance"],
                encounter_id=r["encounter_id"],
            ))

    return context


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 -m src.agents.retrieval_agent <patient_id>")
        sys.exit(1)

    patient_id = sys.argv[1]
    context = retrieve_patient_context(patient_id)
    print(context.summary_text())
    if context.categories_with_no_match:
        print(f"Categories with no documented match: {context.categories_with_no_match}")