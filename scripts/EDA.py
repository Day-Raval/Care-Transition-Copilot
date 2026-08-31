"""
Day 3 — exploratory data analysis on data/processed/discharge_records.csv

Covers: distributions, a rough (non-final) readmission label for class
balance, a missingness map, and a leakage spot-check. The readmission
label here is NOT the rigorous survival target — that's Day 5's job with
proper censoring. This is just enough to plan around today.
"""

import sys
from datetime import datetime

import pandas as pd

CSV_PATH = "data/processed/discharge_records.csv"


def load():
    df = pd.read_csv(CSV_PATH)
    # utc=True normalizes every timestamp to a single tz-aware dtype. Without
    # it, mixed offsets (Massachusetts EST vs EDT depending on discharge date)
    # force pandas into an object-dtype column of individual Timestamps,
    # which silently breaks vectorized subtraction later (TypeError:
    # unsupported operand type(s) for -: 'numpy.ndarray' and 'Timestamp').
    df["admit_ts"] = pd.to_datetime(df["admit_ts"], utc=True)
    df["discharge_ts"] = pd.to_datetime(df["discharge_ts"], utc=True)
    return df


def print_distributions(df):
    print("=" * 60)
    print("DISTRIBUTIONS")
    print("=" * 60)
    print(df[["age_at_discharge", "length_of_stay_days", "medication_count"]].describe())
    print()
    print("Top 10 admission reasons:")
    print(df["admission_reason"].value_counts().head(10).to_string())
    print()
    print("Comorbidity category frequency (patients can have multiple):")
    all_cats = df["comorbidity_categories"].fillna("").str.split(",")
    exploded = all_cats.explode()
    exploded = exploded[exploded != ""]
    print(exploded.value_counts().to_string())
    print()


def add_rough_readmission_label(df):
    """
    Same patient, another inpatient encounter starting within 30 days
    AFTER this discharge. Rough/preliminary — see module docstring.
    """
    df = df.sort_values(["patient_id", "admit_ts"]).copy()
    label = []
    by_patient = {pid: g for pid, g in df.groupby("patient_id")}

    for _, row in df.iterrows():
        group = by_patient[row["patient_id"]]
        others = group[group["encounter_id"] != row["encounter_id"]]
        days_to_next = (others["admit_ts"] - row["discharge_ts"]).dt.total_seconds() / 86400
        readmitted = ((days_to_next > 0) & (days_to_next <= 30)).any()
        label.append(bool(readmitted))

    df["rough_readmit_30d"] = label
    return df


def print_class_balance(df):
    print("=" * 60)
    print("CLASS BALANCE (rough label)")
    print("=" * 60)
    n = len(df)
    n_pos = df["rough_readmit_30d"].sum()
    print(f"Total records: {n}")
    print(f"Rough positive (readmitted within 30d): {n_pos} ({100*n_pos/n:.1f}%)")
    print(f"Rough negative: {n - n_pos} ({100*(n-n_pos)/n:.1f}%)")
    if n_pos < 100:
        print(f"NOTE: only {n_pos} positive cases — expect wide confidence intervals")
        print("      on any Day 6 baseline metric. Don't over-read small differences.")
    print()


def print_missingness(df):
    print("=" * 60)
    print("MISSINGNESS MAP")
    print("=" * 60)
    miss = df.isnull().mean().sort_values(ascending=False) * 100
    print(miss[miss > 0].round(1).to_string())
    if miss[miss > 0].empty:
        print("(no missing values across pandas-detected nulls)")
    print()
    if "discharge_disposition" in df.columns:
        blank_disposition = (df["discharge_disposition"].isnull() | (df["discharge_disposition"] == "")).mean() * 100
        print(f"discharge_disposition missing/blank: {blank_disposition:.1f}%")
    print()


def leakage_spot_check(df, n=3):
    print("=" * 60)
    print(f"LEAKAGE SPOT-CHECK — {n} rough-positive records")
    print("=" * 60)
    positives = df[df["rough_readmit_30d"]].head(n)
    for _, row in positives.iterrows():
        print(f"\nPatient {row['patient_id']}, encounter {row['encounter_id']}")
        print(f"  Discharge: {row['discharge_ts']}  |  Age: {row['age_at_discharge']}")
        print(f"  Admission reason: {row['admission_reason']}")
        print(f"  Comorbidities: {row['comorbidity_categories']}")
        print(f"  Medication count: {row['medication_count']}")
        print("  -> Manually confirm none of these reference the LATER readmission,")
        print("     only what was known as of this discharge_ts.")
    print()


def main():
    df = load()
    print(f"Loaded {len(df)} records from {CSV_PATH}\n")

    print_distributions(df)
    df = add_rough_readmission_label(df)
    print_class_balance(df)
    print_missingness(df)
    leakage_spot_check(df)

    print("=" * 60)
    print("Done. Review the leakage spot-check by hand before moving to Day 5.")
    print("=" * 60)


if __name__ == "__main__":
    main()