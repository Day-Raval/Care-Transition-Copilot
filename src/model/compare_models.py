"""
Compares multiple survival model families and 
hyperparameter settings using grouped, repeated cross-validation.

Why grouped k-fold instead of one train/test split: with only ~50 positive
events total, a single split can land as few as 1 event in the test set, 
making the C-index nearly meaningless. Averaging across 5 patient-grouped 
folds gives a far more stable estimate, and the standard deviation 
across folds tells you how much to trust the mean.

Why three model families: Cox (linear, most interpretable, needs the
fewest events to be stable) as the trustworthy default; Random Survival
Forest and Gradient Boosting as non-linear alternatives that COULD do
better, but are also the most likely to overfit a small event count by
memorizing noise rather than learning real signal.
"""

import logging
import sys
import warnings

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold
from sksurv.ensemble import GradientBoostingSurvivalAnalysis, RandomSurvivalForest
from sksurv.linear_model import CoxPHSurvivalAnalysis
from sksurv.metrics import concordance_index_censored

sys.path.insert(0, ".")
from src.model.train_baseline import prepare_features
from src.utils.config import load_config
from src.utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore")  # sksurv/sklearn convergence warnings are
                                     # expected noise at this sample size,
                                     # not errors worth surfacing per-fold


def cross_validate_grouped(model_factory, X, y, groups, n_splits=5):
    """
    Returns the list of per-fold C-index scores (not just the mean) —
    the spread across folds matters as much as the average here.
    Folds with zero events in the test split are skipped and logged,
    since concordance can't be computed without at least one event.
    """
    gkf = GroupKFold(n_splits=n_splits)
    scores = []
    skipped = 0

    for train_idx, test_idx in gkf.split(X, y, groups=groups):
        X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]

        if y_test["event"].sum() == 0:
            skipped += 1
            continue

        model = model_factory()
        model.fit(X_train, y_train)
        risk_scores = model.predict(X_test)
        c_index = concordance_index_censored(y_test["event"], y_test["time"], risk_scores)[0]
        scores.append(c_index)

    if skipped:
        logger.warning("Skipped %d/%d folds with zero test events", skipped, n_splits)
    return scores


def build_candidates() -> list[tuple[str, callable]]:
    candidates = []

    # Cox (ridge) — vary regularization strength. Higher alpha = more
    # shrinkage = safer with few events, but too high underfits.
    for alpha in [0.1, 0.5, 1.0, 2.0, 5.0]:
        candidates.append((
            f"Cox(alpha={alpha})",
            lambda alpha=alpha: CoxPHSurvivalAnalysis(alpha=alpha),
        ))

    # Random Survival Forest — vary tree count and leaf size. min_samples_leaf
    # is the main overfitting guard here: with ~50 events, a leaf of 5 samples
    # can trivially become "pure" on noise; 15-30 forces more generalization.
    for n_estimators in [100, 200]:
        for min_samples_leaf in [15, 30]:
            candidates.append((
                f"RSF(n={n_estimators},leaf={min_samples_leaf})",
                lambda n=n_estimators, leaf=min_samples_leaf: RandomSurvivalForest(
                    n_estimators=n, min_samples_leaf=leaf, max_depth=4, random_state=42, n_jobs=-1
                ),
            ))

    # Gradient Boosting — vary learning rate and tree count together.
    # Low learning_rate + fewer estimators is the conservative combination;
    # the fast/aggressive combination is included specifically to show how
    # much it overfits, as a teaching point, not because it's expected to win.
    for n_estimators, learning_rate in [(50, 0.05), (100, 0.05), (100, 0.1)]:
        candidates.append((
            f"GBS(n={n_estimators},lr={learning_rate})",
            lambda n=n_estimators, lr=learning_rate: GradientBoostingSurvivalAnalysis(
                n_estimators=n, learning_rate=lr, max_depth=3, random_state=42
            ),
        ))

    return candidates


def run_comparison(X, y, groups, n_splits=5):
    results = []
    for name, factory in build_candidates():
        scores = cross_validate_grouped(factory, X, y, groups, n_splits=n_splits)
        if not scores:
            print(f"{name:<25} — no usable folds, skipped")
            continue
        results.append({
            "model": name,
            "mean_c_index": np.mean(scores),
            "std_c_index": np.std(scores),
            "n_folds": len(scores),
            "fold_scores": scores,
        })

    results.sort(key=lambda r: r["mean_c_index"], reverse=True)
    return results


def print_results(results):
    print(f"\n{'Model':<25} {'Mean C-index':>13} {'Std':>8} {'Folds':>7}   Per-fold scores")
    print("-" * 100)
    for r in results:
        fold_str = ", ".join(f"{s:.3f}" for s in r["fold_scores"])
        print(f"{r['model']:<25} {r['mean_c_index']:>13.3f} {r['std_c_index']:>8.3f} {r['n_folds']:>7}   [{fold_str}]")

    print("\nReading this table:")
    print("- A high mean with a LOW std is trustworthy. A high mean with a HIGH")
    print("  std means it got lucky on some folds and unlucky on others — not")
    print("  a stable result, regardless of how good the average looks.")
    print("- If a tree-ensemble model's mean is only marginally above the best")
    print("  Cox variant, prefer Cox anyway — it's more interpretable and more")
    print("  stable at this event count. Only switch if the margin is large")
    print("  AND the std is comparably tight.")


if __name__ == "__main__":
    setup_logging()
    cfg = load_config()

    target_path = cfg.output_csv.replace(".csv", "_with_target.csv")
    df = pd.read_csv(target_path)

    X, y, groups = prepare_features(df)
    print(f"Modeling set: {len(X)} episodes from {groups.nunique()} unique patients")
    print(f"Total positive events: {y['event'].sum()}")
    print(f"Running 5-fold grouped cross-validation across {len(build_candidates())} model configurations...\n")

    results = run_comparison(X, y, groups, n_splits=5)
    print_results(results)