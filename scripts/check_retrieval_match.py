"""
Prints the full (untruncated) text of retrieved chunks for a given
patient + query, plus whether a specific term actually appears in them —
used to verify a retrieval match is genuinely about what it claims to be,
not just topically/structurally similar.

Usage:
    python3 scripts/check_retrieval_match.py <patient_id> "<query>" "<term to verify>"
"""

import sys

sys.path.insert(0, ".")
from src.retrieval.query_store import get_collection, retrieve_relevant_context


def main():
    if len(sys.argv) < 4:
        print('Usage: python3 scripts/check_retrieval_match.py <patient_id> "<query>" "<term to verify>"')
        sys.exit(1)

    patient_id, query, verify_term = sys.argv[1], sys.argv[2], sys.argv[3]

    collection = get_collection()
    result = retrieve_relevant_context(collection, patient_id, query)

    if result is None:
        print(f'No relevant chart history found for patient {patient_id[:12]} matching: "{query}"')
        return

    for r in result:
        print(f"[{r['section']}] distance={r['distance']:.4f}")
        print(r["text"])
        print()
        print(f"Contains '{verify_term}':", verify_term.lower() in r["text"].lower())
        print("-" * 60)


if __name__ == "__main__":
    main()