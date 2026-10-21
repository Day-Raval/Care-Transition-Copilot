"""Small synthetic deployment for browser tests; never reads the user's runtime data."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import chromadb
from chromadb.api.types import EmbeddingFunction
from chromadb.utils.embedding_functions import register_embedding_function
import joblib
import numpy as np
import pandas as pd
from sksurv.linear_model import CoxPHSurvivalAnalysis
from src.embeddings.build_vector_store import CHROMA_PATH, COLLECTION_NAME


@register_embedding_function
class FixtureEmbedding(EmbeddingFunction):
    """Deterministic test embeddings: exercise storage/scoping, not semantic quality."""
    def __call__(self, input):
        return [[1.0, 0.0, 0.0] for _ in input]

    @staticmethod
    def name():
        return "care-transition-test-embedding"

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return FixtureEmbedding()


def prepare_browser_fixture(root: Path):
    processed = root / "data/processed"
    processed.mkdir(parents=True)
    (root / "models").mkdir()
    (root / "config.yaml").write_text("model:\n  production_run_id: browser_fixture\n", encoding="utf-8")
    ages = np.arange(150) % 90 + 18
    features = pd.DataFrame({"age_at_discharge": ages})
    outcome = np.array([(i % 4 != 0, float(130 - age + (i % 7) / 10))
                        for i, age in enumerate(ages)], dtype=[("event", bool), ("time", float)])
    model = CoxPHSurvivalAnalysis(alpha=0.1).fit(features, outcome)
    joblib.dump({"model": model, "feature_names": list(features.columns)}, root / "models/browser_fixture.joblib")
    order = np.argsort(-model.predict(features), kind="stable")
    ranks = {int(index): rank for rank, index in enumerate(order)}
    collection = chromadb.PersistentClient(path=str(root / CHROMA_PATH)).create_collection(
        COLLECTION_NAME, embedding_function=FixtureEmbedding())
    rows, documents, metadata, ids, notes = [], [], [], [], []
    for index, age in enumerate(ages):
        name = f"Demo{chr(65 + index // 26)}{chr(65 + index % 26)}123 Example456"
        patient_id = f"synthetic-patient-{index}"
        discharged = (datetime(2026, 10, 1, tzinfo=timezone.utc) - timedelta(days=ranks[index])).isoformat()
        rows.append({"patient_id": patient_id, "patient_name": name, "discharge_ts": discharged,
                     "admission_reason": "Synthetic discharge follow-up", "age_at_discharge": int(age),
                     "length_of_stay_days": 3, "medication_count": 2, "prior_admissions_90d": 1,
                     "med_flag_diuretic": False, "med_flag_anticoagulant": False,
                     "outcome": "POSITIVE" if outcome[index]["event"] else "NEGATIVE",
                     "event_observed": int(outcome[index]["event"])})
        for section, text in (
            ("Medications", "Medications: Review discharge medications with the care team and arrange medication reconciliation."),
            ("History of Present Illness", f"{name} is recovering after a synthetic hospital admission and requires follow-up review."),
            ("Assessment and Plan", "Assessment and Plan: Arrange follow-up with primary care and confirm discharge instructions."),
        ):
            documents.append(text)
            metadata.append({"patient_id": patient_id, "section_name": section,
                             "encounter_id": f"encounter-{index}", "discharge_ts": discharged})
            ids.append(f"{index}-{section}")
        notes.append({"patient_id": patient_id, "discharge_ts": discharged, "text": documents[-1]})
    pd.DataFrame(rows).to_csv(processed / "discharge_records_with_target.csv", index=False)
    (processed / "discharge_notes.jsonl").write_text("\n".join(map(json.dumps, notes)) + "\n", encoding="utf-8")
    collection.add(ids=ids, documents=documents, metadatas=metadata)
