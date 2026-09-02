"""
Parses raw Synthea FHIR bundles into the canonical DischargeRecord schema.

Only inpatient (IMP) encounters are parsed — confirmed in Day 1 that IMP
is 576/32,108 (1.8%) of all encounters, and that's the actual population
a 30-day readmission model cares about.
"""

import base64
import json
import logging
import os
from datetime import datetime

from src.ingestion.schema import DischargeRecord
from src.ingestion.temporal_filters import active_conditions_at_discharge, active_medications_at_discharge
from src.ingestion.comorbidity import categorize_conditions, flag_high_risk_meds
from src.ingestion.episodes import cluster_encounters_into_episodes, count_prior_episodes_90d
from src.utils.config import Config, load_config

logger = logging.getLogger(__name__)


def resolve_medication_name(med_request: dict, bundle_entries: list[dict]) -> str:
    """Handles both the ~85% inline case and the ~15% reference case (confirmed Day 1)."""
    if "medicationCodeableConcept" in med_request:
        return med_request["medicationCodeableConcept"].get("text", "unknown")
    ref = med_request.get("medicationReference", {}).get("reference")
    med = next(
        (r for r in bundle_entries if r["resourceType"] == "Medication" and f"urn:uuid:{r['id']}" == ref),
        None,
    )
    if med:
        return med.get("code", {}).get("coding", [{}])[0].get("display", "unknown")
    logger.debug("Could not resolve medicationReference %s to a Medication resource", ref)
    return "unknown"


def get_inpatient_encounters(entries: list[dict]) -> list[dict]:
    return [
        r for r in entries
        if r["resourceType"] == "Encounter" and r.get("class", {}).get("code") == "IMP"
    ]


def decode_note(doc_ref: dict) -> str | None:
    try:
        b64 = doc_ref["content"][0]["attachment"]["data"]
        return base64.b64decode(b64).decode("utf-8")
    except (KeyError, IndexError) as e:
        logger.warning("Could not decode DocumentReference %s: %s", doc_ref.get("id", "unknown"), e)
        return None


def calculate_age_years(birth_date: str, as_of: str) -> int:
    """
    Completed years as of `as_of`, the clinical convention — not a fractional
    age. A simple days/365.25 division is close but wrong at year boundaries
    (leap years, and a birthday that hasn't happened yet this year both throw
    it off by up to a year in edge cases).
    """
    birth = datetime.fromisoformat(birth_date).date()
    as_of_date = datetime.fromisoformat(as_of).date()
    years = as_of_date.year - birth.year
    if (as_of_date.month, as_of_date.day) < (birth.month, birth.day):
        years -= 1
    return years


def build_record(entries: list[dict], episode: dict, all_patient_episodes: list[dict], cfg: Config) -> DischargeRecord:
    patient = next(r for r in entries if r["resourceType"] == "Patient")
    patient_id = patient["id"]

    admit_ts = episode["start"]
    discharge_ts = episode["end"]
    episode_encounters = episode["encounters"]
    first_encounter = episode_encounters[0]   # admission — for admission_reason
    last_encounter = episode_encounters[-1]   # actual discharge — for disposition
    encounter_ids = {e["id"] for e in episode_encounters}

    los_days = (
        datetime.fromisoformat(discharge_ts).date() - datetime.fromisoformat(admit_ts).date()
    ).days

    all_conditions = [r for r in entries if r["resourceType"] == "Condition"]
    all_medreqs = [r for r in entries if r["resourceType"] == "MedicationRequest"]

    active_conditions = active_conditions_at_discharge(all_conditions, discharge_ts)
    active_meds = active_medications_at_discharge(all_medreqs, discharge_ts, lookback_days=cfg.medication_lookback_days)
    med_names = [resolve_medication_name(m, entries) for m in active_meds]
    distinct_med_names = sorted(set(med_names))

    doc_refs = [
        r for r in entries
        if r["resourceType"] == "DocumentReference"
        and any(
            e.get("reference", "").replace("urn:uuid:", "") in encounter_ids
            for e in r.get("context", {}).get("encounter", [])
        )
    ]
    doc_refs.sort(key=lambda r: r.get("date", ""), reverse=True)
    if not doc_refs:
        logger.warning("Episode ending %s (patient %s) has no linked DocumentReference — note text will be null",
                        discharge_ts, patient_id)
    note_text = decode_note(doc_refs[0]) if doc_refs else None

    race_ext = next(
        (e for e in patient.get("extension", []) if "us-core-race" in e.get("url", "")), None
    )
    race = None
    if race_ext:
        sub = next((e for e in race_ext.get("extension", []) if e.get("url") == "text"), None)
        race = sub.get("valueString") if sub else None

    record = DischargeRecord(
        patient_id=patient_id,
        encounter_id=first_encounter["id"],
        admit_ts=admit_ts,
        discharge_ts=discharge_ts,
        length_of_stay_days=los_days,
        age_at_discharge=calculate_age_years(patient["birthDate"], discharge_ts),
        encounter_count=len(episode_encounters),
        admission_reason=(
            first_encounter.get("reasonCode", [{}])[0].get("coding", [{}])[0].get("display", "unknown")
            if first_encounter.get("reasonCode") else "unknown"
        ),
        discharge_disposition=last_encounter.get("hospitalization", {}).get("dischargeDisposition", {}).get("text"),
        comorbidity_categories=categorize_conditions(active_conditions),
        medication_count=len(distinct_med_names),
        high_risk_med_flags=flag_high_risk_meds(med_names),
        prior_admissions_90d=count_prior_episodes_90d(all_patient_episodes, episode, readmission_window_days=cfg.readmission_window_days),
        protected_attributes={"sex": patient.get("gender"), "race": race},
        discharge_note_text=note_text,
    )

    logger.debug(
        "Built record: patient=%s episode_encounters=%d age=%d LOS=%dd meds=%d comorbidities=%s",
        patient_id, record.encounter_count, record.age_at_discharge, record.length_of_stay_days,
        record.medication_count, record.comorbidity_categories,
    )
    return record


def parse_bundle(path: str, cfg: Config) -> tuple[list[DischargeRecord], str | None]:
    data = json.load(open(path))
    entries = [e["resource"] for e in data["entry"]]
    imp_encounters = get_inpatient_encounters(entries)
    if not imp_encounters:
        logger.debug("%s: no inpatient (IMP) encounters — skipped", os.path.basename(path))
        return [], None
    episodes = cluster_encounters_into_episodes(imp_encounters, gap_threshold_hours=cfg.gap_threshold_hours)
    if len(episodes) < len(imp_encounters):
        logger.debug("%s: %d raw IMP encounters merged into %d genuine episode(s)",
                     os.path.basename(path), len(imp_encounters), len(episodes))
    records = [build_record(entries, ep, episodes, cfg) for ep in episodes]
    return records, None


def parse_all_bundles(fhir_dir: str, cfg: Config | None = None) -> tuple[list[DischargeRecord], list[dict]]:
    cfg = cfg or load_config()
    records, failures = [], []
    files = [f for f in os.listdir(fhir_dir) if not f.startswith(("hospitalInformation", "practitionerInformation"))]
    logger.info("Starting parse: %d bundles in %s (gap_threshold=%dh, med_lookback=%dd, readmit_window=%dd)",
                len(files), fhir_dir, cfg.gap_threshold_hours, cfg.medication_lookback_days, cfg.readmission_window_days)

    for i, fn in enumerate(files, start=1):
        try:
            recs, _ = parse_bundle(os.path.join(fhir_dir, fn), cfg)
            records.extend(recs)
        except Exception as e:
            logger.error("Failed to parse %s: %s", fn, e, exc_info=True)
            failures.append({"file": fn, "error": str(e)})

        if i % 100 == 0 or i == len(files):
            logger.info("Progress: %d/%d bundles processed, %d records so far, %d failures",
                        i, len(files), len(records), len(failures))

    logger.info("Parse complete: %d records from %d bundles, %d failures",
                len(records), len(files), len(failures))
    if failures:
        logger.warning("%d bundle(s) failed to parse — see errors above", len(failures))

    return records, failures


if __name__ == "__main__":
    import sys
    from src.utils.logging_config import setup_logging

    setup_logging()
    cfg = load_config()
    fhir_dir = sys.argv[1] if len(sys.argv) > 1 else cfg.fhir_dir
    records, failures = parse_all_bundles(fhir_dir, cfg)