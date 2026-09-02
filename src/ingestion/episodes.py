"""
Groups raw inpatient (IMP) encounters into genuine hospitalization episodes.

Why this exists: Synthea sometimes represents one continuous hospital stay
as multiple separate IMP Encounter resources (e.g. an admission encounter
plus a same-stay procedure encounter, a few hours apart). Confirmed on real
data: 576 raw IMP encounters collapse into 534 genuine episodes once
overlapping/back-to-back encounters are merged. Without this step,
prior_admissions_90d and any readmission label both overcount — a patient
never actually left the hospital, but counting raw encounters makes it
look like they did.
"""

from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def _load_gap_threshold_hours() -> int:
    """Read the default hospitalization gap threshold from the repo config file."""
    default_value = 6
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"

    if not config_path.exists() or yaml is None:
        return default_value

    try:
        with config_path.open("r", encoding="utf-8") as fh:
            config = yaml.safe_load(fh) or {}
    except Exception:
        return default_value

    if not isinstance(config, dict):
        return default_value

    value = config.get("gap_threshold_hours", config.get("GAP_THRESHOLD_HOURS"))
    if value is None:
        return default_value

    try:
        return int(value)
    except (TypeError, ValueError):
        return default_value


GAP_THRESHOLD_HOURS = _load_gap_threshold_hours()  # config.yaml override if present


def cluster_encounters_into_episodes(imp_encounters: list[dict], gap_threshold_hours: int = GAP_THRESHOLD_HOURS) -> list[dict]:
    """
    imp_encounters: all IMP Encounter resources for ONE patient.
    Returns episodes sorted chronologically:
        [{"start": ts, "end": ts, "encounters": [...]}, ...]
    A single non-overlapping encounter becomes its own one-encounter episode.
    """
    if not imp_encounters:
        return []

    sorted_encs = sorted(imp_encounters, key=lambda e: e["period"]["start"])
    episodes = []
    current = {
        "start": sorted_encs[0]["period"]["start"],
        "end": sorted_encs[0]["period"]["end"],
        "encounters": [sorted_encs[0]],
    }

    for enc in sorted_encs[1:]:
        prev_end = datetime.fromisoformat(current["end"])
        this_admit = datetime.fromisoformat(enc["period"]["start"])
        gap_hours = (this_admit - prev_end).total_seconds() / 3600

        if gap_hours <= gap_threshold_hours:
            current["encounters"].append(enc)
            this_end = datetime.fromisoformat(enc["period"]["end"])
            if this_end > prev_end:
                current["end"] = enc["period"]["end"]
        else:
            episodes.append(current)
            current = {"start": enc["period"]["start"], "end": enc["period"]["end"], "encounters": [enc]}

    episodes.append(current)
    return episodes


def count_prior_episodes_90d(all_patient_episodes: list[dict], current_episode: dict, readmission_window_days: int = 90) -> int:
    """Same idea as the old encounter-based version, but on episodes."""
    current_start = datetime.fromisoformat(current_episode["start"])
    count = 0
    for ep in all_patient_episodes:
        if ep is current_episode:
            continue
        other_start = datetime.fromisoformat(ep["start"])
        days_before = (current_start - other_start).total_seconds() / 86400
        if 0 < days_before <= readmission_window_days:
            count += 1
    return count