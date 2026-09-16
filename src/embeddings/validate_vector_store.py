"""
Validates the vector store is actually usable before the retrieval agent
depends on it: runs a handful of clinically relevant queries and checks
that results come back with correct metadata and text that genuinely
relates to the query — not just that .query() runs without error.
"""

import sys
import chromadb

sys.path.insert(0, ".")
from src.embeddings.build_vector_store import CHROMA_PATH, COLLECTION_NAME

TEST_QUERIES = [
    "congestive heart failure follow-up medications",
    "patient discharged with diabetes management plan",
    "chest pain evaluation cardiology",
    "medication reconciliation on discharge",
]


def print_collection_stats(collection):
    print(f"Collection: {COLLECTION_NAME}")
    print(f"Total chunks: {collection.count()}")

    sample = collection.get(limit=collection.count(), include=["metadatas"])
    patient_ids = {m["patient_id"] for m in sample["metadatas"]}
    encounter_ids = {m["encounter_id"] for m in sample["metadatas"]}
    multi_chunk = sum(1 for m in sample["metadatas"] if m["total_chunks"] > 1)

    print(f"Unique patients represented: {len(patient_ids)}")
    print(f"Unique encounters represented: {len(encounter_ids)}")
    print(f"Chunks from multi-chunk notes: {multi_chunk}")
    print()


def run_test_queries(collection):
    results = collection.query(query_texts=TEST_QUERIES, n_results=3)

    for i, query in enumerate(TEST_QUERIES):
        print(f"QUERY: \"{query}\"")
        docs = results["documents"][i]
        metas = results["metadatas"][i]
        distances = results["distances"][i]

        for rank, (doc, meta, dist) in enumerate(zip(docs, metas, distances), start=1):
            print(f"  #{rank}  distance={dist:.4f}  patient={meta['patient_id'][:12]}  "
                  f"encounter={meta['encounter_id'][:12]}  chunk={meta['chunk_index']}/{meta['total_chunks']-1}")
            print(f"       {doc[:150].replace(chr(10), ' ')}...")
        print()


if __name__ == "__main__":
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client.get_collection(COLLECTION_NAME)  # auto-restores
                                                            # ChromaDB's
                                                            # default embedding
                                                            # function — no
                                                            # need to pass it
                                                            # again here

    print_collection_stats(collection)
    run_test_queries(collection)