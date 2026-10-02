"""
CLI Utility to precompute care plan assessments in batch.

Usage:
  python scripts/precompute_assessments.py
  python scripts/precompute_assessments.py --category high --limit 5
  python scripts/precompute_assessments.py --category high,medium --limit 20 --force

Precomputes dynamic retrieval, LLM reasoning, and critique for targeted patients,
persisting the generated care plan and caching it so clinician UI requests load in <10ms.
"""

from __future__ import annotations

import argparse
import sys
import time

import pandas as pd
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, ".")

from src.api.main import (
    _categorize,
    _generate_assessment,
    _latest_discharge_ts,
    _state,
    load_production_model,
)
from src.api.production import latest_saved_care_plan
from src.utils.config import load_config


def parse_args():
    parser = argparse.ArgumentParser(description="Precompute care plan assessments for discharged patients.")
    parser.add_argument(
        "--category",
        type=str,
        default="high",
        help="Comma-separated risk categories to process (e.g. 'high', 'high,medium', or 'all'). Default: high.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of patient episodes to process. Default: 10.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-generation even if an assessment is already saved/cached.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    print("=" * 65)
    print("Care Transition Copilot — Assessment Pre-generation Worker")
    print("=" * 65)

    print("Loading production model and reference dataset...")
    load_production_model()

    queue_df = _state["queue_df"]
    scores = _state["reference_scores"]

    candidates = []
    for i in range(len(queue_df)):
        risk_score = float(scores[i])
        percentile = float((scores < risk_score).mean() * 100)
        cat = _categorize(percentile)
        candidates.append({
            "patient_id": str(queue_df.iloc[i]["patient_id"]),
            "patient_name": str(queue_df.iloc[i]["patient_name"]),
            "discharge_ts": str(queue_df.iloc[i]["discharge_ts"]),
            "admission_reason": str(queue_df.iloc[i]["admission_reason"]),
            "risk_score": round(risk_score, 4),
            "risk_percentile": round(percentile, 1),
            "risk_category": cat,
        })

    # Sort highest risk score first
    candidates.sort(key=lambda x: x["risk_score"], reverse=True)

    if args.category.lower() != "all":
        target_cats = {c.strip().lower() for c in args.category.split(",") if c.strip()}
        candidates = [c for c in candidates if c["risk_category"].lower() in target_cats]

    selected = candidates[: args.limit]
    print(f"Targeting {len(selected)} patients (categories='{args.category}', limit={args.limit}, force={args.force})")
    print("-" * 65)

    completed = 0
    skipped = 0
    failed = 0

    for idx, patient in enumerate(selected, 1):
        p_id = patient["patient_id"]
        d_ts = patient["discharge_ts"]
        p_name = patient["patient_name"]
        cat = patient["risk_category"]
        pct = patient["risk_percentile"]

        if not args.force:
            existing = latest_saved_care_plan(p_id, d_ts)
            if existing and "draft_plan" in existing:
                print(f"[{idx}/{len(selected)}] {p_name} ({p_id[:8]}...) - {cat.upper()} ({pct:.1f}%) -> SKIPPED (already cached)")
                skipped += 1
                continue

        print(f"[{idx}/{len(selected)}] {p_name} ({p_id[:8]}...) - {cat.upper()} ({pct:.1f}%) -> Generating plan...", end="", flush=True)
        t0 = time.monotonic()
        try:
            assessment = _generate_assessment(p_id, d_ts)
            elapsed = time.monotonic() - t0
            print(f" DONE in {elapsed:.2f}s (cached)")
            completed += 1
            # Brief delay between LLM calls
            time.sleep(0.3)
        except Exception as exc:
            elapsed = time.monotonic() - t0
            print(f" FAILED in {elapsed:.2f}s: {exc}")
            failed += 1

    print("-" * 65)
    print(f"Precompute complete: {completed} generated, {skipped} skipped (cached), {failed} failed.")
    print("=" * 65)


if __name__ == "__main__":
    main()
