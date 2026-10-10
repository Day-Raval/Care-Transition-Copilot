import unittest

from src.embeddings.chunking import chunk_note_record


class ChunkingTest(unittest.TestCase):
    def test_no_known_allergies_are_not_merged_into_medications(self):
        record = {
            "encounter_id": "encounter-1",
            "patient_id": "patient-1",
            "discharge_ts": "2026-08-06",
            "note_text": """
# Allergies
No Known Allergies.

# Medications
Lisinopril 10 mg oral tablet; Simvastatin 10 mg oral tablet
""",
        }

        chunks = chunk_note_record(record)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["section_name"], "Medications")
        self.assertEqual(chunks[0]["text"], "Medications: Lisinopril 10 mg oral tablet; Simvastatin 10 mg oral tablet")
        self.assertNotIn("Allergies", chunks[0]["text"])

    def test_chief_complaint_is_not_merged_into_hpi(self):
        record = {
            "encounter_id": "encounter-1",
            "patient_id": "patient-1",
            "discharge_ts": "2026-08-06",
            "note_text": """
# Chief Complaint
- Chest Pain
- Cough

# History of Present Illness
Jose123 is a 63 year-old nonhispanic white male. Patient has a history of stress (finding).

# Medications
No Active Medications.
""",
        }

        chunks = chunk_note_record(record)

        self.assertEqual([chunk["section_name"] for chunk in chunks], ["Chief Complaint", "History of Present Illness"])
        self.assertIn("Chest Pain", chunks[0]["text"])
        self.assertNotIn("Chief Complaint", chunks[1]["text"])
        self.assertNotIn("No Active Medications", " ".join(chunk["text"] for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
