# Care Transition Copilot — Data Documentation

This document specifies the data sources, schemas, and transformation rules used in the
Data Ingestion (01) and Prediction (02) stages of the architecture. It's meant as a
reference for anyone implementing the ingestion pipeline or the feature engineering
layer.

---

## 1. Data Sources Overview

Discharge data arrives from the hospital system in two different forms, each serving a
different purpose:

| Source | Format | Role | Delivery |
|---|---|---|---|
| ADT feed | HL7v2 | Event trigger — signals *that* a discharge happened | Pushed in real time |
| Clinical data | FHIR R4 | Clinical content — *what* happened | Pulled via REST after the trigger |

**Rule of thumb:** HL7v2 tells you *when*, FHIR tells you *what*. The raw HL7v2 message
is never fed into the model — it only triggers the FHIR fetch.

For the MVP, both are generated synthetically with [Synthea](https://synthetichealth.github.io/synthea/),
which can emit FHIR bundles and simulate ADT-style admit/discharge timing, so no real
patient data or data use agreement is required.

---

## 2. HL7v2 — Discharge Event (ADT^A03)

### 2.1 Format

Pipe-delimited segments, each field positional (not named). Segments relevant to
discharge:

| Segment | Meaning |
|---|---|
| `MSH` | Message header (message type, timestamp, sender/receiver) |
| `PID` | Patient identification |
| `PV1` | Patient visit (attending provider, service, admit/discharge timestamps) |
| `DG1` | Diagnosis (may or may not be populated depending on site config) |

### 2.2 Example message

```
MSH|^~\&|EPIC|GENHOSP|COPILOT|COPILOT|20260821143000||ADT^A03|MSG00001|P|2.5
PID|1||88213^^^MRN||Whitaker^James^^^^||19580312|M
PV1|1|I|CARD^204^1||||123456^Alvarez^Maria|||CARD|||||||||88213-VISIT9|||||||||||||||||||||||||20260815080000|20260821143000
DG1|1||I50.9^Congestive Heart Failure^ICD10
```

### 2.3 Fields extracted

Only three fields are pulled out of this message — everything else is discarded at this
stage:

| Field | Segment-position | Example value | Purpose |
|---|---|---|---|
| `patient_id` | `PID-3` | `88213` | Key used to fetch FHIR resources |
| `event_type` | `MSH-9` | `ADT^A03` | Confirms this is a discharge, not admit/transfer |
| `discharge_ts` | `PV1-45` | `20260821143000` | Anchors "time zero" for the risk window |

### 2.4 Parsing

Use `hl7apy` or `python-hl7` rather than manual string splitting — positional parsing
by hand is fragile against site-to-site HL7v2 configuration differences (Z-segments,
optional fields).

```python
import hl7

msg = hl7.parse(raw_message)
patient_id = str(msg.segment("PID")[3][0])
event_type = str(msg.segment("MSH")[9])
discharge_ts = str(msg.segment("PV1")[45])
```

---

## 3. FHIR R4 — Clinical Content

Once the ADT trigger fires, the ingestion gateway pulls four resource types via FHIR
REST calls, scoped to that `patient_id`.

### 3.1 Encounter

Provides admit/discharge timestamps and disposition.

```json
{
  "resourceType": "Encounter",
  "id": "88213-VISIT9",
  "status": "finished",
  "subject": { "reference": "Patient/88213" },
  "period": { "start": "2026-08-15T08:00:00Z", "end": "2026-08-21T14:30:00Z" },
  "hospitalization": {
    "dischargeDisposition": { "text": "Home with home health" }
  },
  "serviceType": { "text": "Cardiology" }
}
```

| Field used | Path | Feature derived |
|---|---|---|
| Length of stay | `period.end - period.start` | `length_of_stay_days` |
| Discharge disposition | `hospitalization.dischargeDisposition.text` | `discharged_with_home_health` (flag) |
| Service line | `serviceType.text` | `service_line` (categorical) |

### 3.2 Condition (one resource per diagnosis)

> **Correction (confirmed against generated data, 2026-08-25):** Synthea emits
> Condition codes as **SNOMED CT**, not ICD-10, in 100% of sampled records —
> the `http://hl7.org/fhir/sid/icd-10` example below was a planning
> assumption and does not match real output. A real hospital feed may still
> use ICD-10, so the parser should read `coding[].system` and branch rather
> than assuming one system.

```json
{
  "resourceType": "Condition",
  "subject": { "reference": "Patient/88213" },
  "code": {
    "coding": [{ "system": "http://snomed.info/sct", "code": "84114007", "display": "Heart failure" }]
  },
  "recordedDate": "2026-08-15"
}
```

Raw codes are **not** used directly as model features (too sparse,
high-cardinality) regardless of system. They're grouped first — see Section 5.
SNOMED CT requires a different comorbidity-grouping path than ICD-10
(Elixhauser/Charlson mappings are ICD-based) — either map SNOMED → ICD-10-CM
first, or use a SNOMED-native comorbidity grouper.

### 3.3 MedicationRequest (one resource per discharge medication)

> **Correction (confirmed against generated data):** medication coding is
> **mixed**, not uniformly inline — about 85% of MedicationRequest resources
> carry an inline `medicationCodeableConcept`, but the remaining ~15% only
> carry a `medicationReference` pointing to a separate `Medication` resource
> elsewhere in the same bundle. The parser must check for
> `medicationCodeableConcept` first and fall back to resolving
> `medicationReference` against the bundle's `Medication` entries.

```json
{
  "resourceType": "MedicationRequest",
  "subject": { "reference": "Patient/88213" },
  "medicationCodeableConcept": { "text": "Furosemide 40mg" },
  "authoredOn": "2026-08-21",
  "dosageInstruction": [{ "text": "Once daily" }]
}
```

```json
// The ~15% case — requires a join against the bundle's Medication resources
{
  "resourceType": "MedicationRequest",
  "subject": { "reference": "Patient/88213" },
  "medicationReference": { "reference": "urn:uuid:39c659fd-..." },
  "status": "completed",
  "intent": "order"
}
```

Used for a medication count and specific risk-class flags — not the raw drug list.

### 3.4 DocumentReference (discharge summary / notes)

```json
{
  "resourceType": "DocumentReference",
  "subject": { "reference": "Patient/88213" },
  "type": { "text": "Discharge Summary" },
  "content": [{
    "attachment": {
      "contentType": "text/plain",
      "data": "UGF0aWVudCBkaXNjaGFyZ2VkIG9uIGZ1cm9zZW1pZGUgNDBtZy4uLg=="
    }
  }]
}
```

`content[].attachment.data` is base64-encoded and must be decoded before use. This is
the **only** field that goes to the text pipeline (Section 6) — it never reaches the
structured feature set.

### 3.5 Parsing

Use `fhir.resources` (typed Pydantic models) instead of raw `dict` access — it validates
the resource shape and catches malformed data before it reaches feature engineering.

```python
from fhir.resources.encounter import Encounter

enc = Encounter.parse_raw(response.text)
los_days = (enc.period.end - enc.period.start).days
```

---

## 4. Canonical Record

Both sources are flattened into one record per encounter before anything reaches the
model or the vector store. This is the schema every downstream consumer (Risk Model,
Retrieval Agent) reads from — not the raw HL7v2/FHIR payloads.

```python
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class DischargeRecord:
    patient_id: str
    encounter_id: str
    discharge_ts: datetime
    admit_ts: datetime
    length_of_stay_days: float
    service_line: str
    discharged_with_home_health: bool

    # structured features → Risk Model
    comorbidity_categories: list[str]        # grouped, not raw ICD-10
    medication_count: int
    high_risk_med_flags: dict[str, bool]      # e.g. {"anticoagulant": True, "insulin": False}
    prior_admissions_90d: int

    # evaluation-only — never used as a model input
    protected_attributes: dict[str, str]      # e.g. {"sex": "M"}, kept separate for the fairness audit

    # text → Retrieval Agent / vector store, not the risk model
    discharge_note_text: str
```

---

## 5. Feature Engineering Rules

| Raw input | Transformation | Output feature |
|---|---|---|
| ICD-10 codes (`Condition`) | Grouped via Elixhauser or Charlson comorbidity mapping | `comorbidity_categories` (list of ~20 categories, not hundreds of raw codes) |
| Medication list (`MedicationRequest`) | Counted; checked against a small risk-class lookup (anticoagulants, diuretics, insulin) | `medication_count`, `high_risk_med_flags` |
| `Encounter.period` | `end - start` | `length_of_stay_days` |
| Discharge timestamp | Compared against prior encounters for the same `patient_id` | `prior_admissions_90d` |
| Sex, age, etc. | Kept separate from the training feature set | `protected_attributes` (evaluation-only, used by the Fairlearn subgroup audit) |

**Why this matters:** raw codes and dates fed straight into the model either don't
generalize (sparse ICD-10 one-hot vectors) or leak information the model shouldn't use
as a shortcut (protected attributes). Grouping and deriving features first is what makes
the risk model both more accurate and auditable.

---

## 6. Text Pipeline (Notes → Vector Store)

`discharge_note_text` follows a separate path from the structured features:

1. Decode the base64 `DocumentReference` content.
2. Chunk into ~500-token segments with a small overlap (~50 tokens) to avoid splitting
   a clinically relevant sentence across chunks.
3. Embed each chunk (via the embedding model used by LlamaIndex).
4. Store in ChromaDB tagged with `patient_id` and `encounter_id` metadata, so the
   Retrieval Agent can filter to the correct patient before searching, and cite the
   correct chunk back to the clinician.

---

## 7. Data Quality Notes

- HL7v2 field positions vary slightly by EHR vendor and site configuration — validate
  against a site-specific message profile rather than assuming the example above is
  universal.
- FHIR `Condition` resources may arrive with codes from more than one system (ICD-10,
  SNOMED CT) — check `coding[].system` before mapping to comorbidity categories.
- A `DocumentReference` may be absent for a given encounter (e.g., note not yet
  transcribed at discharge time) — the pipeline should handle a missing note gracefully
  rather than failing the whole record.
- `protected_attributes` should be captured but **excluded from training** — carried
  alongside the record purely for the fairness audit, never joined into the feature
  matrix passed to `scikit-survival`.
