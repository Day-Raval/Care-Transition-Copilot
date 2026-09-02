# src/utils/config.py
"""
Single source of truth for tunable pipeline parameters. Falls back to
sensible defaults if config.yaml is missing, so nothing breaks if it's
not present yet.
"""

from dataclasses import dataclass
import yaml
import os

@dataclass
class Config:
    fhir_dir: str = "data/raw/fhir"
    output_csv: str = "data/processed/discharge_records.csv"
    output_notes: str = "data/processed/discharge_notes.jsonl"
    gap_threshold_hours: int = 6
    medication_lookback_days: int = 180
    readmission_window_days: int = 90

def load_config(path: str = "config.yaml") -> Config:
    if not os.path.exists(path):
        return Config()
    with open(path) as f:
        raw = yaml.safe_load(f)
    return Config(
        fhir_dir=raw.get("data", {}).get("fhir_dir", Config.fhir_dir),
        output_csv=raw.get("data", {}).get("output_csv", Config.output_csv),
        output_notes=raw.get("data", {}).get("output_notes", Config.output_notes),
        gap_threshold_hours=raw.get("episodes", {}).get("gap_threshold_hours", Config.gap_threshold_hours),
        medication_lookback_days=raw.get("features", {}).get("medication_lookback_days", Config.medication_lookback_days),
        readmission_window_days=raw.get("features", {}).get("readmission_window_days", Config.readmission_window_days),
    )