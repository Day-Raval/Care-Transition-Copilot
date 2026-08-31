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


def count_prior_admissions_90d(all_imp_encounters: list[dict], current_encounter: dict) -> int:
    current_start = datetime.fromisoformat(current_encounter["period"]["start"])
    count = 0
    for enc in all_imp_encounters:
        if enc["id"] == current_encounter["id"]:
            continue
        other_start = datetime.fromisoformat(enc["period"]["start"])
        days_before = (current_start - other_start).total_seconds() / 86400
        if 0 < days_before <= 90:
            count += 1
    return count


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


def build_record(entries: list[dict], encounter: dict, all_imp_encounters: list[dict]) -> DischargeRecord:
    patient = next(r for r in entries if r["resourceType"] == "Patient")
    patient_id = patient["id"]

    discharge_ts = encounter["period"]["end"]
    admit_ts = encounter["period"]["start"]
    los_days = (
        datetime.fromisoformat(discharge_ts) - datetime.fromisoformat(admit_ts)
    ).total_seconds() / 86400

    all_conditions = [r for r in entries if r["resourceType"] == "Condition"]
    all_medreqs = [r for r in entries if r["resourceType"] == "MedicationRequest"]

    active_conditions = active_conditions_at_discharge(all_conditions, discharge_ts)
    active_meds = active_medications_at_discharge(all_medreqs, discharge_ts)
    med_names = [resolve_medication_name(m, entries) for m in active_meds]
    distinct_med_names = sorted(set(med_names))

    doc_refs = [
        r for r in entries
        if r["resourceType"] == "DocumentReference"
        and any(e.get("reference") == f"urn:uuid:{encounter['id']}" for e in r.get("context", {}).get("encounter", []))
    ]
    if not doc_refs:
        logger.warning("Encounter %s (patient %s) has no linked DocumentReference — note text will be null",
                        encounter["id"], patient_id)
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
        encounter_id=encounter["id"],
        admit_ts=admit_ts,
        discharge_ts=discharge_ts,
        length_of_stay_days=round(los_days, 2),
        age_at_discharge=calculate_age_years(patient["birthDate"], discharge_ts),
        admission_reason=(
            encounter.get("reasonCode", [{}])[0].get("coding", [{}])[0].get("display", "unknown")
            if encounter.get("reasonCode") else "unknown"
        ),
        discharge_disposition=encounter.get("hospitalization", {}).get("dischargeDisposition", {}).get("text"),
        comorbidity_categories=categorize_conditions(active_conditions),
        medication_count=len(distinct_med_names),
        high_risk_med_flags=flag_high_risk_meds(med_names),
        prior_admissions_90d=count_prior_admissions_90d(all_imp_encounters, encounter),
        protected_attributes={"sex": patient.get("gender"), "race": race},
        discharge_note_text=note_text,
    )

    logger.debug(
        "Built record: patient=%s encounter=%s age=%d LOS=%.1fd meds=%d comorbidities=%s",
        patient_id, encounter["id"], record.age_at_discharge, record.length_of_stay_days,
        record.medication_count, record.comorbidity_categories,
    )
    return record


def parse_bundle(path: str) -> tuple[list[DischargeRecord], str | None]:
    data = json.load(open(path))
    entries = [e["resource"] for e in data["entry"]]
    imp_encounters = get_inpatient_encounters(entries)
    if not imp_encounters:
        logger.debug("%s: no inpatient (IMP) encounters — skipped", os.path.basename(path))
        return [], None
    logger.debug("%s: %d inpatient encounter(s) found", os.path.basename(path), len(imp_encounters))
    records = [build_record(entries, enc, imp_encounters) for enc in imp_encounters]
    return records, None


def parse_all_bundles(fhir_dir: str) -> tuple[list[DischargeRecord], list[dict]]:
    records, failures = [], []
    files = [f for f in os.listdir(fhir_dir) if not f.startswith(("hospitalInformation", "practitionerInformation"))]
    logger.info("Starting parse: %d bundles in %s", len(files), fhir_dir)

    for i, fn in enumerate(files, start=1):
        try:
            recs, _ = parse_bundle(os.path.join(fhir_dir, fn))
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
    fhir_dir = sys.argv[1] if len(sys.argv) > 1 else "data/raw/fhir"
    records, failures = parse_all_bundles(fhir_dir)