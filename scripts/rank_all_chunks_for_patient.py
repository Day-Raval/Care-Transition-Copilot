"""
Shows EVERY chunk for one patient, ranked by distance to a query — not
just the top-N that made the cut. Used to answer: does a chunk we KNOW
contains a specific term exist for this patient, and if so, where does
it actually rank (and why did/didn't it surface)?
"""

import sys

sys.path.insert(0, ".")
from src.retrieval.query_store import get_collection

def main():
    if len(sys.argv) < 4:
        print('Usage: python3 scripts/rank_all_chunks_for_patient.py <patient_id> "<query>" "<term to find>"')
        sys.exit(1)

    patient_id, query, term = sys.argv[1], sys.argv[2], sys.argv[3]
    collection = get_collection()

    # n_results set high enough to cover ALL of this patient's chunks
    results = collection.query(
        query_texts=[query],
        n_results=200,
        where={"patient_id": patient_id},
    )

    print(f"Total chunks for this patient returned: {len(results['documents'][0])}\n")
    for rank, (doc, meta, dist) in enumerate(
        zip(results["documents"][0], results["metadatas"][0], results["distances"][0]), 1
    ):
        has_term = term.lower() in doc.lower()
        marker = f"  <-- CONTAINS '{term}'" if has_term else ""
        print(f"#{rank:>3}  distance={dist:.4f}  [{meta['section_name']}]{marker}")
        if has_term:
            print(f"       {doc[:200]}")

if __name__ == "__main__":
    main()