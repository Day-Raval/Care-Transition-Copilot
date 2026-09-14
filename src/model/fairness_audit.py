"""
WHY THIS MATTERS
protected_sex and protected_race have been carried through the schema pipeline, 
deliberately kept OUT of the training feature set (see
schema.py: "Evaluation-only — never joined into the training feature
matrix"). But keeping race/sex out of the model doesn't automatically
make it fair — a model can still end up performing unevenly across groups
if its actual training features happen to correlate differently with the
outcome for different groups. This isn't theoretical: a widely-cited 2019
study (Obermeyer et al., Science) found a commercial healthcare risk
algorithm under-flagged Black patients for extra care management relative
to equally-sick White patients — not because race was a model input, but
because the model's target (healthcare cost) correlated with unequal
historical access to care. This project's pipeline has the same basic
shape (proxy-heavy features predicting a future clinical outcome), which
is exactly the setup where that failure mode shows up.

WHAT THIS CHECKS
Two different questions per protected group, since they can fail
independently:
1. DISCRIMINATION — does the model rank risk correctly within each group,
   as well as it does overall? (group-wise C-index)
2. MISS RATE AT A REAL DECISION THRESHOLD — if this model were used to flag
   the highest-risk 20% of patients for extra care management, does it
   miss (fail to flag) truly high-risk patients from one group more than
   another? (false negative rate by group — the costly error here: a
   genuinely high-risk patient who doesn't get flagged for follow-up)

HONEST LIMITATION, UP FRONT
With only ~52 total positive events in the whole dataset, splitting
further by race/sex leaves some subgroups with single-digit event counts.
A subgroup with fewer than MIN_EVENTS_FOR_AUDIT events gets explicitly
flagged as "too few events to audit reliably" rather than handed a number
that looks precise but isn't.
"""

import sys

import numpy as np
import pandas as pd
from sksurv.linear_model import CoxPHSurvivalAnalysis
from sksurv.metrics import concordance_index_censored

sys.path.insert(0, ".")
from src.model.train_baseline import prepare_features, train_test_split_grouped
from src.utils.config import load_config

MIN_EVENTS_FOR_AUDIT = 5  # below this, don't report a number — say so instead
HIGH_RISK_PERCENTILE = 80  # "flagged for care management" = top 20% risk

import os
from datetime import datetime


class Tee:
    """Writes to both the terminal and a file at once, so running the
    script still shows output live, but every run also leaves a saved,
    timestamped record in results/ — no extra step required."""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)

    def flush(self):
        for s in self.streams:
            s.flush()


def train_and_score(df: pd.DataFrame):
    """
    Trains on the same grouped split train_baseline.py uses, then scores
    the TEST set only — auditing fairness on data the model was trained on
    would make it look artificially fair.
    """
    X, y, groups = prepare_features(df)
    X_train, X_test, y_train, y_test, groups_train, groups_test = train_test_split_grouped(X, y, groups)

    model = CoxPHSurvivalAnalysis(alpha=1.0)
    model.fit(X_train, y_train)
    risk_scores = model.predict(X_test)

    # Re-attach protected attributes and outcome info for the test rows only
    modeling_df = df[df["outcome"].isin(["POSITIVE", "NEGATIVE"])].copy()
    test_df = modeling_df.loc[X_test.index].copy()
    test_df["risk_score"] = risk_scores
    return test_df, y_test


def audit_group(test_df: pd.DataFrame, y_test: np.ndarray, group_col: str, group_value: str) -> dict:
    mask = (test_df[group_col] == group_value).values
    n = mask.sum()
    n_events = test_df.loc[mask, "event_observed"].sum()

    result = {"group": group_value, "n": n, "n_events": n_events}

    if n_events < MIN_EVENTS_FOR_AUDIT:
        result["auditable"] = False
        return result

    result["auditable"] = True

    y_group = y_test[mask]
    risk_group = test_df.loc[mask, "risk_score"].values
    result["c_index"] = concordance_index_censored(y_group["event"], y_group["time"], risk_group)[0]

    global_threshold = np.percentile(test_df["risk_score"], HIGH_RISK_PERCENTILE)
    flagged = test_df.loc[mask, "risk_score"] >= global_threshold
    actual_positive = test_df.loc[mask, "event_observed"].astype(bool)

    true_positives = (flagged & actual_positive).sum()
    false_negatives = (~flagged & actual_positive).sum()
    total_positive = actual_positive.sum()

    result["flagged_rate"] = flagged.mean()
    result["false_negative_rate"] = false_negatives / total_positive if total_positive > 0 else None
    result["recall"] = true_positives / total_positive if total_positive > 0 else None

    return result


def print_audit(test_df: pd.DataFrame, y_test: np.ndarray, group_col: str, label: str):
    print(f"\n{'='*70}")
    print(f"FAIRNESS AUDIT: {label}")
    print(f"{'='*70}")

    overall_threshold = np.percentile(test_df["risk_score"], HIGH_RISK_PERCENTILE)
    print(f"(Decision threshold: top {100-HIGH_RISK_PERCENTILE}% highest risk score, "
          f"same cutoff = {overall_threshold:.3f} applied to every group)\n")

    for group_value in sorted(test_df[group_col].dropna().unique()):
        r = audit_group(test_df, y_test, group_col, group_value)

        if not r["auditable"]:
            print(f"{r['group']:<45} n={r['n']:<5} events={r['n_events']:<3} "
                  f"-> TOO FEW EVENTS TO AUDIT RELIABLY (need >={MIN_EVENTS_FOR_AUDIT})")
            continue

        print(f"{r['group']:<45} n={r['n']:<5} events={r['n_events']:<3}")
        print(f"    C-index:              {r['c_index']:.3f}")
        print(f"    Flagged as high-risk: {r['flagged_rate']*100:.1f}%")
        print(f"    Recall (caught):      {r['recall']*100:.1f}%")
        print(f"    False negative rate:  {r['false_negative_rate']*100:.1f}%  "
              f"(truly high-risk patients who were NOT flagged)")


def summarize_findings(test_df: pd.DataFrame, y_test: np.ndarray, group_col: str, label: str):
    """Plain-language flag if any auditable subgroup's FNR is notably worse than another's."""
    results = [audit_group(test_df, y_test, group_col, g) for g in test_df[group_col].dropna().unique()]
    auditable = [r for r in results if r["auditable"]]

    if len(auditable) < 2:
        print(f"\n{label}: fewer than 2 groups had enough events to compare — "
              f"cannot draw a fairness conclusion either way for this attribute yet.")
        return

    fnrs = {r["group"]: r["false_negative_rate"] for r in auditable}
    worst_group = max(fnrs, key=fnrs.get)
    best_group = min(fnrs, key=fnrs.get)
    gap = fnrs[worst_group] - fnrs[best_group]

    print(f"\n{label} — plain-language summary:")
    print(f"  Largest false-negative-rate gap: {best_group} ({fnrs[best_group]*100:.1f}%) "
          f"vs {worst_group} ({fnrs[worst_group]*100:.1f}%) — a {gap*100:.1f} point difference.")
    if gap > 0.15:
        print(f"  This is a LARGE gap. The model misses high-risk {worst_group} patients "
              f"substantially more often than {best_group} patients. Worth investigating "
              f"before this model is used for anything beyond a portfolio baseline.")
    elif gap > 0.05:
        print(f"  This is a moderate gap — worth watching, not necessarily disqualifying "
              f"at this sample size, but re-check once the dataset grows.")
    else:
        print(f"  This gap is small enough to plausibly be sample-size noise, not a real "
              f"disparity — but note the small event counts mean this isn't a strong "
              f"confirmation of fairness either, just an absence of an obvious problem.")



def print_overall_conclusion(sex_results: list[dict], race_results: list[dict]):
    """
    Synthesizes both attribute audits into one explicit closing statement.
    Without this, a reader has to piece together the verdict from two
    separate per-attribute notes — this makes it impossible to miss.
    """
    print(f"\n{'='*70}")
    print("CONCLUSION")
    print(f"{'='*70}")

    def attribute_verdict(results: list[dict], label: str) -> str:
        auditable = [r for r in results if r["auditable"]]
        if len(auditable) >= 2:
            return f"{label}: DETERMINATE — {len(auditable)} groups had enough events to compare."
        elif len(auditable) == 1:
            only = auditable[0]["group"]
            return (f"{label}: INCONCLUSIVE — only '{only}' had enough events (>={MIN_EVENTS_FOR_AUDIT}) "
                    f"to audit. No other group could be compared against it.")
        else:
            return f"{label}: INCONCLUSIVE — no group had enough events to audit at all."

    sex_line = attribute_verdict(sex_results, "Sex")
    race_line = attribute_verdict(race_results, "Race")
    print(sex_line)
    print(race_line)

    all_inconclusive = "INCONCLUSIVE" in sex_line and "INCONCLUSIVE" in race_line
    print()
    if all_inconclusive:
        print(
            "This model's fairness has NOT been validated across sex or race at the "
            "current dataset size. This is not a negative finding about the model — "
            "it is an absence of enough data to check. Do not represent this model as "
            "'fairness-audited' or 'bias-checked' in any writeup, demo, or documentation "
            "until this becomes determinate. The audit infrastructure is built and "
            "working correctly; it needs a larger population to produce an answer."
        )
    else:
        print(
            "At least one protected attribute produced a determinate comparison this "
            "run. Review the false-negative-rate gap above for that attribute directly "
            "before drawing conclusions — a small gap is not proof of fairness, and a "
            "large gap warrants investigation before this model is used beyond a "
            "portfolio baseline."
        )


if __name__ == "__main__":
    os.makedirs("results", exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    report_path = f"results/fairness_audit_{timestamp}.txt"

    original_stdout = sys.stdout
    with open(report_path, "w") as f:
        sys.stdout = Tee(original_stdout, f)
        try:
            print(f"Fairness Audit — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

            cfg = load_config()
            df = pd.read_csv(cfg.output_csv.replace(".csv", "_with_target.csv"))

            test_df, y_test = train_and_score(df)
            print(f"Auditing on the held-out test set: {len(test_df)} episodes, "
                  f"{test_df['event_observed'].sum()} positive events")

            print_audit(test_df, y_test, "protected_sex", "SEX")
            summarize_findings(test_df, y_test, "protected_sex", "Sex")

            print_audit(test_df, y_test, "protected_race", "RACE")
            summarize_findings(test_df, y_test, "protected_race", "Race")

            sex_results = [audit_group(test_df, y_test, "protected_sex", g)
                           for g in test_df["protected_sex"].dropna().unique()]
            race_results = [audit_group(test_df, y_test, "protected_race", g)
                            for g in test_df["protected_race"].dropna().unique()]
            print_overall_conclusion(sex_results, race_results)
        finally:
            sys.stdout = original_stdout

    print(f"\nReport saved to {report_path}")