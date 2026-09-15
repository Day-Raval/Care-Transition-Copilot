"""
Metadata for every feature that has EVER appeared in a logged experiment
run (see results/experiments.csv), not just the currently-deployed model's
features. This is what lets the API schema adapt automatically: when
main.py loads a model, it looks up each of that model's actual features
here and builds a validated request schema on the fly — so re-pointing
config.yaml's production_run_id at a DIFFERENT run (say, one that used
comorbidity_count, or a future one with a new feature) doesn't require
touching this file's calling code, only adding a spec here if the feature
is genuinely new.

Each spec: (python_type, ge, le, description). ge/le of None means
"no bound" — used for fields we don't have a hard domain constraint for.
"""

FEATURE_SPECS = {
    "age_at_discharge": (int, 0, 120, "Patient age in completed years at discharge"),
    "length_of_stay_days": (int, 0, 365, "Length of the hospital stay in calendar days"),
    "medication_count": (int, 0, None, "Distinct active medications at discharge (180-day lookback)"),
    "prior_admissions_90d": (int, 0, None, "Prior inpatient episodes in the 90 days before this admission"),
    "comorbidity_count": (int, 0, None, "Number of flagged chronic comorbidity categories (tested, currently excluded from production — see results/RESULTS.md)"),
    "med_flag_diuretic": (bool, None, None, "On a diuretic medication (furosemide, HCTZ, spironolactone)"),
    "med_flag_anticoagulant": (bool, None, None, "On an anticoagulant (warfarin, apixaban, etc.)"),
    "med_flag_insulin": (bool, None, None, "On insulin (tested, currently excluded — see results/RESULTS.md)"),
    "med_flag_opioid": (bool, None, None, "On an opioid medication (tested, currently excluded — see results/RESULTS.md)"),
}


def get_spec(feature_name: str) -> tuple:
    """
    Falls back to an untyped, unbounded float for any feature not yet
    registered above, rather than crashing — logs a warning so the gap
    gets noticed and a real spec gets added, but doesn't block serving.
    """
    if feature_name in FEATURE_SPECS:
        return FEATURE_SPECS[feature_name]
    import logging
    logging.getLogger(__name__).warning(
        "No feature spec registered for '%s' — serving with no validation "
        "bounds. Add a spec to src/api/feature_specs.py.", feature_name
    )
    return (float, None, None, f"(no spec registered for {feature_name})")