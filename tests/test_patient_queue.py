from datetime import date, timedelta
import unittest
from unittest.mock import patch

import pandas as pd

from src.api import main


class PatientQueueSearchTest(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {
                "patient_id": f"patient-{index}",
                "patient_name": f"Patient {index}",
                "discharge_ts": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                "admission_reason": "Cardiac follow-up" if index == 74 else "Routine follow-up",
            }
            for index in range(75)
        ]
        self.state_patch = patch.dict(
            main._state,
            {"queue_df": pd.DataFrame(self.rows), "reference_scores": list(range(75))},
            clear=True,
        )
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)

    def test_search_finds_episode_beyond_first_page(self):
        result = main.search_patient_queue(
            main.PatientSearchRequest(search="cardiac", limit=50, offset=0)
        )

        self.assertEqual(result.total, 1)
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].patient_name, "Patient 74")

    def test_pages_are_ordered_and_do_not_repeat_episodes(self):
        first_page = main.search_patient_queue(main.PatientSearchRequest(limit=30, offset=0))
        second_page = main.search_patient_queue(main.PatientSearchRequest(limit=30, offset=30))

        first_refs = {item.patient_ref for item in first_page.items}
        second_refs = {item.patient_ref for item in second_page.items}
        self.assertEqual(first_page.total, 75)
        self.assertEqual(second_page.total, 75)
        self.assertEqual(len(first_page.items), 30)
        self.assertEqual(len(second_page.items), 30)
        self.assertTrue(first_refs.isdisjoint(second_refs))
        self.assertGreater(first_page.items[0].discharge_ts, second_page.items[0].discharge_ts)

    def test_risk_category_filter_limits_matching_episodes(self):
        result = main.search_patient_queue(
            main.PatientSearchRequest(category="high", limit=50, offset=0)
        )

        self.assertEqual(result.total, 15)
        self.assertTrue(all(item.risk_category == "high" for item in result.items))

    def test_history_fetches_only_one_extra_chunk_to_detect_next_page(self):
        patient_id = "patient-74"
        records = [
            {
                "id": "chunk-2",
                "document": "Later medication history",
                "metadata": {
                    "patient_id": patient_id,
                    "discharge_ts": "2026-04-03",
                    "encounter_id": "encounter-2",
                    "section_name": "Medications",
                    "chunk_index": 0,
                },
            },
            {
                "id": "chunk-1",
                "document": "Earlier condition history",
                "metadata": {
                    "patient_id": patient_id,
                    "discharge_ts": "2026-03-01",
                    "encounter_id": "encounter-1",
                    "section_name": "Conditions",
                    "chunk_index": 0,
                },
            },
        ]

        class FakeCollection:
            def get(self, where=None, ids=None, include=None, limit=None, offset=0):
                selected = records
                if where:
                    selected = [record for record in records if record["metadata"]["patient_id"] == where["patient_id"]]
                if ids is not None:
                    selected = [record for chunk_id in ids for record in records if record["id"] == chunk_id]
                if limit is not None:
                    selected = selected[offset:offset + limit]
                result = {"ids": [record["id"] for record in selected]}
                if "metadatas" in include:
                    result["metadatas"] = [record["metadata"] for record in selected]
                if "documents" in include:
                    result["documents"] = [record["document"] for record in selected]
                return result

        with patch("src.retrieval.query_store.get_collection", return_value=FakeCollection()):
            first_page = main.patient_history(main._patient_ref(patient_id), limit=1, offset=0)
            second_page = main.patient_history(main._patient_ref(patient_id), limit=1, offset=1)

        self.assertTrue(first_page.has_more)
        self.assertFalse(second_page.has_more)
        self.assertEqual(first_page.items[0].text, "Later medication history")
        self.assertEqual(second_page.items[0].text, "Earlier condition history")

    def test_history_replaces_synthea_first_name_with_display_name(self):
        patient_id = "patient-74"
        main._state["queue_df"].loc[
            main._state["queue_df"]["patient_id"] == patient_id,
            "patient_name",
        ] = "Duane703 Gislason620"
        records = [
            {
                "id": "chunk-1",
                "document": "Duane703 is a 62 year-old male.",
                "metadata": {
                    "patient_id": patient_id,
                    "discharge_ts": "2026-08-06",
                    "section_name": "History of Present Illness",
                },
            },
        ]

        class FakeCollection:
            def get(self, where=None, include=None, limit=None, offset=0):
                selected = [record for record in records if record["metadata"]["patient_id"] == where["patient_id"]]
                selected = selected[offset:offset + limit]
                return {
                    "ids": [record["id"] for record in selected],
                    "documents": [record["document"] for record in selected],
                    "metadatas": [record["metadata"] for record in selected],
                }

        with patch("src.retrieval.query_store.get_collection", return_value=FakeCollection()):
            result = main.patient_history(main._patient_ref(patient_id), limit=1, offset=0)

        self.assertEqual(result.items[0].text, "Duane Gislason is a 62 year-old male.")

    def test_history_removes_no_known_allergy_boilerplate_from_medications(self):
        patient_id = "patient-74"
        records = [
            {
                "id": "chunk-1",
                "document": "Medications: Allergies: No Known Allergies. - Lisinopril 10 mg oral tablet",
                "metadata": {
                    "patient_id": patient_id,
                    "discharge_ts": "2026-08-06",
                    "section_name": "Medications",
                },
            },
        ]

        class FakeCollection:
            def get(self, where=None, include=None, limit=None, offset=0):
                selected = [record for record in records if record["metadata"]["patient_id"] == where["patient_id"]]
                selected = selected[offset:offset + limit]
                return {
                    "ids": [record["id"] for record in selected],
                    "documents": [record["document"] for record in selected],
                    "metadatas": [record["metadata"] for record in selected],
                }

        with patch("src.retrieval.query_store.get_collection", return_value=FakeCollection()):
            result = main.patient_history(main._patient_ref(patient_id), limit=1, offset=0)

        self.assertEqual(result.items[0].text, "Medications: Lisinopril 10 mg oral tablet")


if __name__ == "__main__":
    unittest.main()
