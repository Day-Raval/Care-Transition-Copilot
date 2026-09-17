"""
Patient-scoped retrieval with a relevance threshold — the actual function
the retrieval agent will call, not just a raw ChromaDB query wrapper.

Why the threshold matters: ChromaDB's query() always returns the top-N
closest chunks, even when NONE of them are genuinely relevant (confirmed:
querying a patient with no heart failure history for "heart failure
medications" still returned their 3 closest chunks — unrelated oncology
content — because query() has no concept of "not relevant enough," only
"closest available"). Without a threshold, a clinician-facing agent could
end up citing unrelated chart history as if it answered the question.

RELEVANCE_DISTANCE_THRESHOLD is a starting estimate, not calibrated
against a labeled relevant/irrelevant dataset — revisit once the
retrieval agent is in real use and you can observe which distances
correlate with actually-useful vs. actually-irrelevant results.
"""

import sys
import chromadb

sys.path.insert(0, ".")
from src.embeddings.build_vector_store import CHROMA_PATH, COLLECTION_NAME

RELEVANCE_DISTANCE_THRESHOLD = 1.3


def get_collection() -> chromadb.Collection:
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    return client.get_collection(COLLECTION_NAME)


def retrieve_relevant_context(
    collection: chromadb.Collection,
    patient_id: str,
    query: str,
    n_results: int = 3,
    distance_threshold: float = RELEVANCE_DISTANCE_THRESHOLD,
) -> list[dict] | None:
    """
    Returns a list of {"text": ..., "section": ..., "distance": ...} for
    chunks that both (a) belong to this patient and (b) clear the
    relevance threshold — or None if nothing did, meaning "no relevant
    chart history found," a valid and important outcome to distinguish
    from "here's the closest thing we had, regardless of quality."
    """
    results = collection.query(
        query_texts=[query],
        n_results=n_results,
        where={"patient_id": patient_id},
    )

    if not results["documents"][0]:
        return None  # patient has no notes at all in the store

    relevant = [
        {"text": doc, "section": meta["section_name"], "distance": dist}
        for doc, meta, dist in zip(
            results["documents"][0], results["metadatas"][0], results["distances"][0]
        )
        if dist <= distance_threshold
    ]

    return relevant if relevant else None


if __name__ == "__main__":
    # Quick manual test — pass a patient_id and query as CLI args, or
    # edit these two lines directly for a one-off check.
    if len(sys.argv) < 3:
        print("Usage: python3 -m src.retrieval.query_store <patient_id> <query text>")
        sys.exit(1)

    patient_id, query = sys.argv[1], " ".join(sys.argv[2:])
    collection = get_collection()
    result = retrieve_relevant_context(collection, patient_id, query)

    if result is None:
        print(f"No relevant chart history found for patient {patient_id[:12]} matching: \"{query}\"")
    else:
        print(f"Found {len(result)} relevant chunk(s):")
        for r in result:
            print(f"  [{r['section']}] distance={r['distance']:.4f}")
            print(f"    {r['text'][:150]}")