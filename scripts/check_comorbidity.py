"""
Checks comorbidity_count against every other remaining feature, not just
the two flags already dropped — to find what it's still competing with
for credit, if the sign is still wrong after removing insulin/opioid.
"""

import sys
import pandas as pd

sys.path.insert(0, ".")
from src.utils.config import load_config
from scripts.check_multicollinearity import interpret_correlation

COMORBIDITY_CATEGORIES = [
    "heart_failure", "diabetes", "copd", "renal_disease", "hypertension",
    "coronary_artery_disease", "obesity", "substance_use", "cancer",
]

cfg = load_config()
df = pd.read_csv(cfg.output_csv.replace(".csv", "_with_target.csv"))
modeling = df[df["outcome"].isin(["POSITIVE", "NEGATIVE"])].copy()

for cat in COMORBIDITY_CATEGORIES:
    modeling[f"comorbid_{cat}"] = modeling["comorbidity_categories"].fillna("").str.contains(cat).astype(int)
modeling["comorbidity_count"] = modeling[[f"comorbid_{c}" for c in COMORBIDITY_CATEGORIES]].sum(axis=1)

remaining_features = ["medication_count", "prior_admissions_90d", "age_at_discharge",
                       "length_of_stay_days", "med_flag_diuretic", "med_flag_anticoagulant"]

print("comorbidity_count correlation against every remaining feature:")
for feat in remaining_features:
    r = modeling["comorbidity_count"].corr(modeling[feat].astype(float))
    print(f"  vs {feat:<25} {r:>6.3f}   -> {interpret_correlation(r)}")