"""
Real-Time Intake Gateway: HL7v2 ADT^A03 & FHIR Ingestion.

Receives incoming ADT discharge events (HL7v2 ADT^A03 or structured triggers),
extracts patient identifiers and encounter timestamps, resolves clinical context,
derives model features, scores 30-day readmission risk, triggers proactive
precomputation for high-risk patients, and publishes the normalized episode to Kafka.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.data_services.kafka_events import publish_audit_event, publish_discharge_episode
from src.utils.config import load_config

logger = logging.getLogger(__name__)


def parse_hl7_timestamp(ts_str: str) -> str:
    """
    Parses HL7v2 timestamp formats (e.g., '20260821143000', '20260821', '2026-08-21T14:30:00Z')
    into standard ISO format string.
    """
    if not ts_str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    cleaned = ts_str.strip()
    # Check if already ISO format
    if "T" in cleaned:
        try:
            return datetime.fromisoformat(cleaned.replace("Z", "+00:00")).isoformat(timespec="seconds")
        except Exception:
            pass

    # Standard HL7 YYYYMMDDHHMMSS or YYYYMMDD
    if len(cleaned) >= 14 and cleaned[:14].isdigit():
        dt = datetime.strptime(cleaned[:14], "%Y%m%d%H%M%S")
        return dt.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
    elif len(cleaned) == 8 and cleaned.isdigit():
        dt = datetime.strptime(cleaned, "%Y%m%d")
        return dt.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")

    # Fallback try dateutil or raw
    try:
        dt = datetime.fromisoformat(cleaned)
        return dt.isoformat(timespec="seconds")
    except Exception:
        return cleaned


def parse_hl7_adt_message(raw_msg: str) -> dict[str, Any]:
    """
    Parses a raw pipe-delimited HL7v2 message (ADT^A03 discharge event).
    Extracts patient_id, event_type, admit_ts, discharge_ts, encounter_id,
    patient_name, and diagnoses.
    """
    if not raw_msg or not raw_msg.strip():
        raise ValueError("Empty HL7 message provided")

    lines = [line.strip() for line in raw_msg.strip().splitlines() if line.strip()]
    segments: dict[str, list[list[str]]] = {}
    for line in lines:
        parts = line.split("|")
        seg_name = parts[0].strip().upper()
        if seg_name not in segments:
            segments[seg_name] = []
        segments[seg_name].append(parts)

    if "MSH" not in segments:
        raise ValueError("Missing MSH segment in HL7 message")

    msh = segments["MSH"][0]
    # MSH-9 is message type (e.g. ADT^A03)
    # Note: in HL7, MSH field numbering counts field separator as field 1
    # parts[8] corresponds to MSH-9
    message_type = msh[8] if len(msh) > 8 else "UNKNOWN"
    msh_timestamp = parse_hl7_timestamp(msh[6]) if len(msh) > 6 else None

    # Patient identification (PID)
    patient_id = "unknown"
    patient_name = "Unknown Patient"
    birth_date = None
    gender = None

    if "PID" in segments:
        pid = segments["PID"][0]
        # PID-3: Patient Identifier List (parts[3])
        if len(pid) > 3 and pid[3]:
            # Handle repetitions or components (e.g. 88213^^^MRN)
            patient_id = pid[3].split("^")[0].strip()
        # PID-5: Patient Name
        if len(pid) > 5 and pid[5]:
            name_parts = pid[5].split("^")
            family = name_parts[0].strip() if len(name_parts) > 0 else ""
            given = name_parts[1].strip() if len(name_parts) > 1 else ""
            patient_name = f"{given} {family}".strip() or "Unknown Patient"
        # PID-7: Date/Time of Birth
        if len(pid) > 7 and pid[7]:
            birth_date = pid[7].strip()
        # PID-8: Administrative Sex
        if len(pid) > 8 and pid[8]:
            gender = pid[8].strip()

    # Patient Visit (PV1)
    encounter_id = f"ENC-{patient_id}"
    admit_ts = None
    discharge_ts = None
    service_line = "General"
    attending_provider = None

    if "PV1" in segments:
        pv1 = segments["PV1"][0]
        # PV1-7: Attending Doctor
        if len(pv1) > 7 and pv1[7]:
            attending_provider = pv1[7].replace("^", " ").strip()
        # PV1-10: Hospital Service
        if len(pv1) > 10 and pv1[10]:
            service_line = pv1[10].strip()
        # PV1-19: Visit Number / Encounter ID
        if len(pv1) > 19 and pv1[19]:
            encounter_id = pv1[19].split("^")[0].strip()
        # PV1-44: Admit Date/Time
        if len(pv1) > 44 and pv1[44]:
            admit_ts = parse_hl7_timestamp(pv1[44])
        # PV1-45: Discharge Date/Time
        if len(pv1) > 45 and pv1[45]:
            discharge_ts = parse_hl7_timestamp(pv1[45])

    if not discharge_ts:
        discharge_ts = msh_timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Diagnoses (DG1)
    diagnoses = []
    if "DG1" in segments:
        for dg in segments["DG1"]:
            # DG1-3: Diagnosis Code
            if len(dg) > 3 and dg[3]:
                dg_parts = dg[3].split("^")
                code = dg_parts[0].strip()
                desc = dg_parts[1].strip() if len(dg_parts) > 1 else code
                diagnoses.append({"code": code, "description": desc})

    return {
        "event_type": message_type,
        "patient_id": patient_id,
        "patient_name": patient_name,
        "encounter_id": encounter_id,
        "admit_ts": admit_ts,
        "discharge_ts": discharge_ts,
        "service_line": service_line,
        "attending_provider": attending_provider,
        "birth_date": birth_date,
        "gender": gender,
        "diagnoses": diagnoses,
    }


def find_patient_fhir_bundle(patient_id: str) -> dict[str, Any] | None:
    """Looks for the patient's FHIR bundle in raw or sample directories."""
    candidate_dirs = [
        Path("data/raw/fhir"),
        Path("data/samples_inpatient"),
        Path("data/samples"),
    ]
    for d in candidate_dirs:
        if not d.exists():
            continue
        direct_file = d / f"{patient_id}.json"
        if direct_file.is_file():
            try:
                with open(direct_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as exc:
                logger.warning("Failed to read FHIR file %s: %s", direct_file, exc)

        # Search JSON files inside directory
        for fpath in d.glob("*.json"):
            if patient_id.lower() in fpath.name.lower():
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        return data
                except Exception:
                    continue
    return None


def extract_or_lookup_features(
    patient_id: str,
    discharge_ts: str,
    fhir_bundle: dict[str, Any] | None = None,
    parsed_adt: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str, str]:
    """
    Extracts the 6 risk model features, admission reason, and patient name.
    Attempts extraction from:
    1. The provided FHIR bundle (if available)
    2. The local dataset (if already ingested)
    3. The parsed ADT event as fallback
    """
    import pandas as pd
    cfg = load_config()

    # 1. Check if patient is already in the processed dataset
    target_csv = cfg.output_csv.replace(".csv", "_with_target.csv")
    if Path(target_csv).exists():
        try:
            df = pd.read_csv(target_csv)
            matches = df[df["patient_id"] == patient_id]
            if not matches.empty:
                # Match nearest discharge_ts or latest
                row = matches.sort_values("discharge_ts").iloc[-1]
                features = {
                    "age_at_discharge": int(row["age_at_discharge"]),
                    "length_of_stay_days": int(row["length_of_stay_days"]),
                    "medication_count": int(row["medication_count"]),
                    "prior_admissions_90d": int(row["prior_admissions_90d"]),
                    "med_flag_diuretic": bool(row["med_flag_diuretic"]),
                    "med_flag_anticoagulant": bool(row["med_flag_anticoagulant"]),
                }
                reason = str(row.get("admission_reason", "unknown"))
                name = str(row.get("patient_name", "Unknown Patient"))
                return features, reason, name
        except Exception as exc:
            logger.debug("Lookup in processed dataset failed for %s: %s", patient_id, exc)

    # 2. Extract from FHIR bundle
    bundle = fhir_bundle or find_patient_fhir_bundle(patient_id)
    if bundle and "entry" in bundle:
        try:
            from src.ingestion.fhir_parser import (
                calculate_age_years,
                extract_patient_name,
                get_inpatient_encounters,
                resolve_medication_name,
            )
            from src.ingestion.temporal_filters import (
                active_conditions_at_discharge,
                active_medications_at_discharge,
            )
            from src.ingestion.comorbidity import flag_high_risk_meds

            entries = [e.get("resource", {}) for e in bundle.get("entry", []) if "resource" in e]
            patient_res = next((r for r in entries if r.get("resourceType") == "Patient"), {})
            patient_name = extract_patient_name(patient_res) if patient_res else "Unknown Patient"
            birth_date = patient_res.get("birthDate", "1960-01-01")
            age = calculate_age_years(birth_date, discharge_ts)

            # Medication count and flags
            all_meds = [r for r in entries if r.get("resourceType") == "MedicationRequest"]
            active_meds = active_medications_at_discharge(all_meds, discharge_ts, lookback_days=cfg.medication_lookback_days)
            med_names = [resolve_medication_name(m, entries) for m in active_meds]
            med_flags = flag_high_risk_meds(med_names)

            # Inpatient encounters & LOS
            inpatient_encs = get_inpatient_encounters(entries)
            los_days = 3
            admission_reason = "Acute inpatient care"
            if inpatient_encs:
                latest_enc = inpatient_encs[-1]
                start = latest_enc.get("period", {}).get("start", discharge_ts)
                try:
                    los_days = max(1, (datetime.fromisoformat(discharge_ts[:10]) - datetime.fromisoformat(start[:10])).days)
                except Exception:
                    los_days = 3
                if latest_enc.get("reasonCode"):
                    admission_reason = latest_enc["reasonCode"][0].get("coding", [{}])[0].get("display", admission_reason)

            features = {
                "age_at_discharge": max(18, age),
                "length_of_stay_days": los_days,
                "medication_count": len(set(med_names)),
                "prior_admissions_90d": max(0, len(inpatient_encs) - 1),
                "med_flag_diuretic": bool(med_flags.get("diuretic", False)),
                "med_flag_anticoagulant": bool(med_flags.get("anticoagulant", False)),
            }
            return features, admission_reason, patient_name
        except Exception as exc:
            logger.warning("Failed extracting features from FHIR bundle for %s: %s", patient_id, exc)

    # 3. Fallback from parsed ADT event
    name = (parsed_adt or {}).get("patient_name", "Unknown Patient")
    diagnoses = (parsed_adt or {}).get("diagnoses", [])
    reason = diagnoses[0]["description"] if diagnoses else (parsed_adt or {}).get("service_line", "Inpatient discharge")

    admit = (parsed_adt or {}).get("admit_ts")
    los = 3
    if admit:
        try:
            los = max(1, (datetime.fromisoformat(discharge_ts[:10]) - datetime.fromisoformat(admit[:10])).days)
        except Exception:
            los = 3

    features = {
        "age_at_discharge": 65,
        "length_of_stay_days": los,
        "medication_count": 5,
        "prior_admissions_90d": 0,
        "med_flag_diuretic": any("heart failure" in d.get("description", "").lower() for d in diagnoses),
        "med_flag_anticoagulant": False,
    }
    return features, reason, name


def score_features_in_process(features: dict[str, Any]) -> tuple[float, float, str]:
    """Scores features using loaded model state or default scoring function."""
    try:
        from src.api.main import _categorize, _predict_score, _state
        if _state and "model" in _state and "reference_scores" in _state:
            score = _predict_score(features)
            ref_scores = _state["reference_scores"]
            percentile = float((ref_scores < score).mean() * 100)
            category = _categorize(percentile)
            return round(score, 4), round(percentile, 1), category
    except Exception as exc:
        logger.debug("In-process model scoring not ready: %s; using heuristic fallback", exc)

    # Heuristic fallback based on baseline coefficients if API not active
    # Log hazard baseline ~ 0.02 * age + 0.08 * los + 0.05 * meds + 0.45 * prior
    age = features.get("age_at_discharge", 65)
    los = features.get("length_of_stay_days", 3)
    meds = features.get("medication_count", 5)
    priors = features.get("prior_admissions_90d", 0)
    diur = 1.0 if features.get("med_flag_diuretic") else 0.0
    anticoag = 1.0 if features.get("med_flag_anticoagulant") else 0.0

    raw = 0.02 * (age - 65) + 0.08 * (los - 3) + 0.05 * (meds - 5) + 0.45 * priors + 0.25 * diur + 0.2 * anticoag
    import math
    risk_score = round(math.exp(raw), 4)
    # Approximate percentile:
    percentile = round(min(99.0, max(1.0, 50.0 + raw * 25.0)), 1)
    if percentile >= 80.0:
        cat = "high"
    elif percentile >= 50.0:
        cat = "medium"
    else:
        cat = "low"
    return risk_score, percentile, cat


def process_realtime_intake(
    adt_input: str | dict[str, Any],
    fhir_bundle: dict[str, Any] | None = None,
    trigger_precompute: bool = True,
) -> dict[str, Any]:
    """
    Core intake handler:
    1. Parses HL7 message or dict.
    2. Derives structured risk predictors.
    3. Computes 30-day readmission risk score.
    4. Proactively launches precompute if high/medium risk.
    5. Publishes discharge episode event to Kafka.
    6. Emits audit log entry.
    """
    if isinstance(adt_input, str):
        parsed = parse_hl7_adt_message(adt_input)
    else:
        parsed = dict(adt_input)
        if "discharge_ts" in parsed:
            parsed["discharge_ts"] = parse_hl7_timestamp(parsed["discharge_ts"])
        if "admit_ts" in parsed and parsed["admit_ts"]:
            parsed["admit_ts"] = parse_hl7_timestamp(parsed["admit_ts"])

    patient_id = parsed["patient_id"]
    discharge_ts = parsed["discharge_ts"]
    encounter_id = parsed.get("encounter_id", f"ENC-{patient_id}")

    # Extract features
    features, admission_reason, patient_name = extract_or_lookup_features(
        patient_id=patient_id,
        discharge_ts=discharge_ts,
        fhir_bundle=fhir_bundle,
        parsed_adt=parsed,
    )

    # Score risk
    risk_score, risk_percentile, risk_category = score_features_in_process(features)

    # Proactive precomputation for medium/high risk
    precompute_triggered = False
    if trigger_precompute and risk_category in {"high", "medium"}:
        try:
            from src.api.precompute import precompute_manager
            # Trigger background generation for this patient
            from src.api.main import _assessment_cache_get, _generate_assessment_payload
            evt, is_creator = precompute_manager.get_patient_event(patient_id, discharge_ts)
            if is_creator:
                def _bg_generate():
                    try:
                        _generate_assessment_payload(patient_id, discharge_ts)
                    except Exception as e:
                        logger.warning("Background precompute failed for %s: %s", patient_id, e)
                    finally:
                        precompute_manager.finish_patient_event(patient_id, discharge_ts)

                import threading
                t = threading.Thread(target=_bg_generate, name=f"precompute-{patient_id}", daemon=True)
                t.start()
                precompute_triggered = True
        except Exception as exc:
            logger.debug("Could not trigger proactive precompute: %s", exc)

    # Prepare normalized episode payload
    episode_payload = {
        "encounter_id": encounter_id,
        "patient_id": patient_id,
        "patient_name": patient_name,
        "discharge_ts": discharge_ts,
        "admit_ts": parsed.get("admit_ts", discharge_ts),
        "admission_reason": admission_reason,
        "features": features,
        "risk_score": risk_score,
        "risk_percentile": risk_percentile,
        "risk_category": risk_category,
        "intake_source": "hl7_adt_a03" if isinstance(adt_input, str) else "structured_trigger",
    }

    # Publish to Kafka
    published_to_kafka = publish_discharge_episode(episode_payload)

    # Audit log
    publish_audit_event(
        "realtime_intake_triggered",
        {
            "patient_id": patient_id,
            "encounter_id": encounter_id,
            "discharge_ts": discharge_ts,
            "risk_score": risk_score,
            "risk_category": risk_category,
            "precompute_triggered": precompute_triggered,
            "published_to_kafka": published_to_kafka,
        },
    )

    return {
        "status": "success",
        "patient_id": patient_id,
        "encounter_id": encounter_id,
        "discharge_ts": discharge_ts,
        "patient_name": patient_name,
        "admission_reason": admission_reason,
        "features": features,
        "risk_score": risk_score,
        "risk_percentile": risk_percentile,
        "risk_category": risk_category,
        "precompute_triggered": precompute_triggered,
        "published_to_kafka": published_to_kafka,
    }
