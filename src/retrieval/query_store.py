"""
Patient-scoped retrieval with a relevance threshold.

RELEVANCE_DISTANCE_THRESHOLD calibrated against one confirmed real true
positive: a genuine "heart failure" mention ranked #2 for a directly
relevant query at distance 1.2516 (found via
scripts/rank_all_chunks_for_patient.py). Original guess of 1.1 excluded
this real match. 1.3 keeps it while still excluding clearly weaker
matches. Still a single-data-point calibration — revisit with more
examples once the retrieval agent is in real use.
"""

import sys
import threading
import chromadb

sys.path.insert(0, ".")
from src.embeddings.build_vector_store import CHROMA_PATH, COLLECTION_NAME

RELEVANCE_DISTANCE_THRESHOLD = 1.3

_collection = None
_lock = threading.Lock()


def get_collection() -> chromadb.Collection:
    """
    Reuses a single cached PersistentClient/Collection instance across
    all calls, instead of creating a fresh one every time.

    Confirmed necessary via a real production error: creating a new
    chromadb.PersistentClient() on every call works fine for one-shot
    CLI scripts, but breaks under the FastAPI web app -- each request
    can run in a different worker thread, and creating/tearing down
    PersistentClient instances concurrently against the same path
    corrupts ChromaDB's internal shared-system registry
    ('RustBindingsAPI' object has no attribute 'bindings', followed by
    a KeyError in _create_system_if_not_exists on the next request).
    A single client, created once and reused (double-checked locking
    for thread safety), avoids this entirely. Confirmed via testing:
    20 concurrent calls (simulating FastAPI's thread pool) now construct
    PersistentClient exactly once and all return the same collection
    with zero exceptions.
    """
    global _collection
    if _collection is None:
        with _lock:
            if _collection is None:  # re-check inside the lock
                client = chromadb.PersistentClient(path=CHROMA_PATH)
                _collection = client.get_collection(COLLECTION_NAME)
    return _collection


def retrieve_relevant_context(
    collection: chromadb.Collection,
    patient_id: str,
    query: str,
    n_results: int = 3,
    distance_threshold: float = RELEVANCE_DISTANCE_THRESHOLD,
) -> list[dict] | None:
    """
    Over-fetches candidates, then skips any result whose text is highly
    similar to one already selected — regardless of encounter_id.
    """
    from difflib import SequenceMatcher

    def _too_similar(text_a: str, text_b: str, threshold: float = 0.85) -> bool:
        return SequenceMatcher(None, text_a, text_b).ratio() > threshold

    fetch_n = max(n_results * 3, 10)
    results = collection.query(query_texts=[query], n_results=fetch_n, where={"patient_id": patient_id})

    if not results["documents"][0]:
        return None

    selected = []
    for doc, meta, dist in zip(results["documents"][0], results["metadatas"][0], results["distances"][0]):
        if dist > distance_threshold:
            continue
        if any(_too_similar(doc, s["text"]) for s in selected):
            continue
        selected.append({"text": doc, "section": meta["section_name"], "distance": dist, "encounter_id": meta["encounter_id"]})
        if len(selected) >= n_results:
            break

    return selected if selected else None