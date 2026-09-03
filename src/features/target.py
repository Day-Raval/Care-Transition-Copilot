"""
Builds the rigorous 30-day readmission target — not the rough re-admission label.

Every episode gets exactly one of four outcomes:

  POSITIVE   — another episode for this patient started within
               readmission_horizon_days after this discharge.

  NEGATIVE   — no readmission occurred, AND we can prove it: either the
               dataset's observation cutoff is at least horizon days past
               this discharge, or the patient's recorded death is at least
               horizon days past this discharge. Either way, there was
               enough time to observe a readmission if one were coming,
               and none came.

  DEATH      — the patient died within the horizon, before a readmission
               could have occurred. This is a competing risk, not a
               "negative" — dying is a different outcome than "stayed
               healthy," and folding it into the negative class would
               misrepresent both.

  EXCLUDED   — none of the above: no readmission yet, but also not enough
               time has passed since discharge (relative to the dataset's
               own cutoff) to know whether one would have happened.
               Labeling these as negative would be a real error (we
               genuinely don't know), not a rounding choice.
"""

import logging
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)


def get_dataset_cutoff(df: pd.DataFrame) -> datetime:
    """The latest discharge in the whole dataset — our de facto 'today'."""
    return df["discharge_ts"].max()


def build_target(df: pd.DataFrame, horizon_days: int, cutoff: datetime | None = None) -> pd.DataFrame:
    """
    df must have: patient_id, encounter_id, admit_ts, discharge_ts (parsed
    datetimes), deceased_date (parsed datetime or NaT).
    Returns df with four new columns: outcome, days_observed, event_observed, excluded.
    """
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
        if not future.empty:
            next_ep = future.loc[future["admit_ts"].idxmin()]
            gap_to_next = (next_ep["admit_ts"] - row["discharge_ts"]).total_seconds() / 86400

        if gap_to_next is not None and gap_to_next <= horizon_days:
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
    df["excluded"] = df["outcome"] == "EXCLUDED"
    return df


def summarize(df: pd.DataFrame) -> None:
    n = len(df)
    counts = df["outcome"].value_counts()
    print(f"Total episodes: {n}")
    for outcome in ["POSITIVE", "NEGATIVE", "DEATH", "EXCLUDED"]:
        c = counts.get(outcome, 0)
        print(f"  {outcome:<10} {c:>4}  ({100*c/n:.1f}%)")

    modeling_set = df[df["outcome"].isin(["POSITIVE", "NEGATIVE"])]
    if len(modeling_set):
        pos_rate = (modeling_set["outcome"] == "POSITIVE").mean()
        print(f"\nUsable for modeling (POSITIVE + NEGATIVE only): {len(modeling_set)} episodes")
        print(f"True positive rate among those: {100*pos_rate:.1f}%")


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