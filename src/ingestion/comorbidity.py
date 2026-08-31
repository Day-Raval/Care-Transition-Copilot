"""
Groups SNOMED-coded conditions into comorbidity categories.

Why keyword-based and not a full Elixhauser/Charlson SNOMED crosswalk:
confirmed in Day 1 that Synthea emits 100% SNOMED CT, not ICD-10, so the
standard ICD-based mappings don't apply directly. This is a pragmatic
MVP categorizer using condition display text; swap in a real SNOMED-aware
grouper (or a SNOMED->ICD-10-CM crosswalk + standard Elixhauser table)
before this goes anywhere near a production model.
"""

CATEGORY_KEYWORDS = {
    "heart_failure": ["heart failure", "cardiomyopathy"],
    "diabetes": ["diabetes", "diabetic"],
    "copd": ["chronic obstructive pulmonary", "copd", "emphysema"],
    "renal_disease": ["renal failure", "chronic kidney disease", "kidney disease"],
    "hypertension": ["hypertension"],
    "coronary_artery_disease": ["coronary artery disease", "myocardial infarction", "ischemic heart"],
    "atrial_fibrillation": ["atrial fibrillation"],
    "depression": ["depression", "depressive disorder"],
    "anxiety": ["anxiety"],
    "obesity": ["obesity", "body mass index"],
    "substance_use": ["alcohol", "opioid", "substance"],
    "cancer": ["carcinoma", "malignant neoplasm", "cancer"],
    "asthma": ["asthma"],
    "stroke": ["cerebrovascular accident", "stroke"],
}

HIGH_RISK_MED_KEYWORDS = {
    "anticoagulant": ["warfarin", "heparin", "apixaban", "rivaroxaban", "clopidogrel"],
    "insulin": ["insulin"],
    "diuretic": ["furosemide", "hydrochlorothiazide", "spironolactone"],
    "opioid": ["oxycodone", "hydrocodone", "morphine", "fentanyl"],
}


def categorize_conditions(conditions: list[dict]) -> list[str]:
    """conditions: list of active Condition resources (already time-filtered)."""
    categories = set()
    for c in conditions:
        display = (c.get("code", {}).get("text") or "").lower()
        for cat, keywords in CATEGORY_KEYWORDS.items():
            if any(kw in display for kw in keywords):
                categories.add(cat)
    return sorted(categories)


def flag_high_risk_meds(med_names: list[str]) -> dict:
    """med_names: resolved drug names (already handles the inline/reference split)."""
    flags = {k: False for k in HIGH_RISK_MED_KEYWORDS}
    for name in med_names:
        lname = (name or "").lower()
        for flag, keywords in HIGH_RISK_MED_KEYWORDS.items():
            if any(kw in lname for kw in keywords):
                flags[flag] = True
    return flags