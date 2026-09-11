"""
Builds the rigorous 30-day readmission target — not the rough label.

Outcomes: POSITIVE, NEGATIVE, DEATH, EXCLUDED — see prior day's docstring.

PLANNED (refined): earlier version excluded any episode whose next episode
shared the exact same admission_reason within the horizon. That correctly
caught the lung cancer staging confound (89.3% of original positives were
the same staging code repeating), but ALSO incorrectly swept up genuine
same-diagnosis chronic-disease bounce-backs (confirmed: a CHF case and two
cardiac valve cases were wrongly excluded this way) — which are arguably
the canonical true-positive case a readmission model exists to catch.

Narrowed to only exclude the specific confirmed pattern: same admission
reason AND that reason is oncology staging/treatment language. A repeat
CHF or valve-disease admission is no longer excluded.
"""

import logging
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)

PLANNED_KEYWORDS = ["carcinoma", "malignant neoplasm", "chemotherapy", "radiation therapy"]
# Deliberately narrow: only oncology staging/treatment language, the exact
# pattern confirmed as the confound (lung cancer staging codes repeating
# within days of themselves). A same-diagnosis repeat for a condition like
# CHF or aortic valve disease is NOT excluded here — that's a genuine
# unplanned bounce-back, arguably the canonical true-positive case a
# readmission model exists to catch, and an earlier version of this rule
# (any matching admission_reason, regardless of diagnosis) was incorrectly
# sweeping a handful of those into PLANNED alongside the real confound.


def _is_planned_pattern(admission_reason: str, next_admission_reason: str) -> bool:
    same_reason = admission_reason == next_admission_reason
    is_oncology = any(kw in admission_reason.lower() for kw in PLANNED_KEYWORDS)
    return same_reason and is_oncology


def get_dataset_cutoff(df: pd.DataFrame) -> datetime:
    return df["discharge_ts"].max()


def build_target(df: pd.DataFrame, horizon_days: int, cutoff: datetime | None = None) -> pd.DataFrame:
    df = df.sort_values(["patient_id", "admit_ts"]).copy()
    cutoff = cutoff or get_dataset_cutoff(df)

    outcomes = []
    days_observed_list = []
    by_patient = {pid: g for pid, g in df.groupby("patient_id")}

    for _, row in df.iterrows():
        patient_episodes = by_patient[row["patient_id"]]
        others = patient_episodes[patient_episodes["encounter_id"] != row["encounter_id"]]
        future = others[others["admit_ts"] > row["discharge_ts"]]
        gap_to_next = None
        next_ep = None
        if not future.empty:
            next_ep = future.loc[future["admit_ts"].idxmin()]
            gap_to_next = (next_ep["admit_ts"] - row["discharge_ts"]).total_seconds() / 86400

        if gap_to_next is not None and gap_to_next <= horizon_days:
            if _is_planned_pattern(row["admission_reason"], next_ep["admission_reason"]):
                outcomes.append("PLANNED")
                days_observed_list.append(gap_to_next)
                continue
            outcomes.append("POSITIVE")
            days_observed_list.append(gap_to_next)
            continue

        deceased = row.get("deceased_date")
        if pd.notnull(deceased):
            days_to_death = (deceased - row["discharge_ts"]).total_seconds() / 86400
            if 0 <= days_to_death <= horizon_days:
                outcomes.append("DEATH")
                days_observed_list.append(days_to_death)
                continue

        days_observed_to_cutoff = (cutoff - row["discharge_ts"]).total_seconds() / 86400
        if days_observed_to_cutoff >= horizon_days:
            outcomes.append("NEGATIVE")
            days_observed_list.append(horizon_days)
        else:
            outcomes.append("EXCLUDED")
            days_observed_list.append(days_observed_to_cutoff)

    df["outcome"] = outcomes
    df["days_observed"] = days_observed_list
    df["event_observed"] = df["outcome"] == "POSITIVE"
    df["excluded"] = df["outcome"].isin(["EXCLUDED", "PLANNED"])
    return df


def summarize(df: pd.DataFrame) -> None:
    n = len(df)
    counts = df["outcome"].value_counts()
    print(f"Total episodes: {n}")
    for outcome in ["POSITIVE", "NEGATIVE", "DEATH", "PLANNED", "EXCLUDED"]:
        c = counts.get(outcome, 0)
        print(f"  {outcome:<10} {c:>4}  ({100*c/n:.1f}%)")
    modeling_set = df[df["outcome"].isin(["POSITIVE", "NEGATIVE"])]
    if len(modeling_set):
        pos_rate = (modeling_set["outcome"] == "POSITIVE").mean()
        print(f"\nUsable for modeling: {len(modeling_set)} episodes, positive rate {100*pos_rate:.1f}%")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from src.utils.config import load_config
    from src.utils.logging_config import setup_logging

    setup_logging()
    cfg = load_config()

    df = pd.read_csv(cfg.output_csv)
    df["admit_ts"] = pd.to_datetime(df["admit_ts"], utc=True)
    df["discharge_ts"] = pd.to_datetime(df["discharge_ts"], utc=True)
    df["deceased_date"] = pd.to_datetime(df["deceased_date"], utc=True, errors="coerce")

    df = build_target(df, horizon_days=cfg.readmission_horizon_days)
    summarize(df)

    out_path = cfg.output_csv.replace(".csv", "_with_target.csv")
    df.to_csv(out_path, index=False)
    print(f"\nWritten to {out_path}")