# Model Card

## Model Summary

Care Transition Copilot currently uses a baseline Cox proportional hazards model
to produce a relative 30-day readmission risk score for synthetic discharge
episodes. The configured production artifact is selected by
`model.production_run_id` in `config.yaml`.

## Intended Use

- Local research and demo workflows.
- Synthetic patient readmission-risk ranking.
- Demonstrating clinician-reviewed care-transition workflows.

## Not Intended For

- Clinical decision-making.
- Real patient data or PHI.
- Automated diagnosis, treatment, discharge, outreach, or care-plan approval.
- Use as a calibrated probability without additional validation.

## Inputs

The model uses structured discharge-time features such as age at discharge,
length of stay, medication count, prior admissions, and high-risk medication
flags. The API request schema is generated from the saved model artifact.

## Outputs

The API returns a relative risk score, percentile, and low/medium/high category.
These outputs are advisory and must be interpreted as demo signals, not
clinically validated predictions.

## Evaluation

The repository includes grouped train/test splitting, grouped cross-validation,
model-family comparison, experiment logging, drift reporting, and subgroup
fairness audit scripts. Current results are limited by the small synthetic event
count, including only 52 positive readmission events in the documented run.

## Limitations

- Synthetic data may not represent real hospital populations.
- Current risk scores are not calibrated clinical probabilities.
- Fairness results are inconclusive at the current subgroup/event counts.
- Retrieval and generated plans require independent clinical validation.
- Model performance may shift when data generation, features, or labeling logic
  changes.

## Required Human Oversight

Generated plans remain drafts until a clinician approves them. Rejected plans are
marked for edit/resubmission, and reports are generated only after approval.
