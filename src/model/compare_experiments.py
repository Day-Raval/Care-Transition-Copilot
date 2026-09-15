"""
Prints every logged run side by side, sorted by test C-index, with a
plain-language flag for anything that looks like it's winning mostly on
noise rather than a real, stable improvement.
"""

import sys

sys.path.insert(0, ".")
from src.model.experiment_registry import load_registry


def print_comparison():
    df = load_registry()
    if df.empty:
        print("No runs logged yet. Run src/model/run_experiment.py at least once first.")
        return

    df = df.sort_values("c_index_test", ascending=False)

    print(f"{'run_id':<28} {'alpha':>6} {'#feat':>6} {'test_events':>12} {'C-index (tr/test)':>20}   notes")
    print("-" * 110)
    for _, r in df.iterrows():
        gap = r["c_index_train"] - r["c_index_test"]
        overfit_flag = " *" if gap > 0.15 else "  "
        print(f"{r['run_id']:<28} {r['alpha']:>6} {r['n_features']:>6} {r['events_test']:>12} "
              f"{r['c_index_train']:>8.3f} / {r['c_index_test']:<8.3f}{overfit_flag}   {r['notes']}")

    print("\n* = train/test C-index gap > 0.15 — possible overfitting, treat this run's")
    print("    test score with extra caution regardless of how high it looks.")

    best = df.iloc[0]
    print(f"\nBest by test C-index: {best['run_id']} ({best['c_index_test']:.3f}) — {best['notes']}")
    print(f"Reminder: with only {int(best['events_test'])} test events behind this number, treat")
    print(f"a difference of a few hundredths between top runs as noise, not a real winner,")
    print(f"unless it's confirmed across multiple splits (see src/model/compare_models.py).")


if __name__ == "__main__":
    print_comparison()