"""
Tests the retrieval agent's ACTUAL usage pattern: given one specific
patient, search only their own notes — not the whole 3,110-patient
corpus. The earlier validate_vector_store.py queries tested a harder,
different problem (open-ended search across everyone), which is why
generic content ("CBC panel", "discharge from hospital") could win by
looking similar to many different patients' unrelated chunks. Scoped to
one patient's own small set of notes, that competition mostly disappears.
"""
import sys
sys.path.insert(0, ".")
import chromadb
from collections import Counter
from src.embeddings.build_vector_store import CHROMA_PATH, COLLECTION_NAME

client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = client.get_collection(COLLECTION_NAME)

sample = collection.get(limit=2000, include=["metadatas"])
patient_counts = Counter(m["patient_id"] for m in sample["metadatas"])
test_patient = patient_counts.most_common(1)[0][0]
print(f"Testing with patient {test_patient} ({patient_counts[test_patient]} chunks)\n")

results = collection.query(
    query_texts=["heart failure medications follow-up"],
    n_results=3,
    where={"patient_id": test_patient},
)
for rank, (doc, meta, dist) in enumerate(zip(results["documents"][0], results["metadatas"][0], results["distances"][0]), 1):
    print(f"#{rank} distance={dist:.4f} section={meta['section_name']}")
    print(f"   {doc[:150]}")