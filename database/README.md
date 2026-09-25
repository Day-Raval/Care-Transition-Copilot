# database/

Utilities for managing and inspecting the project's PostgreSQL database
(connection defined by `DATABASE_URL` in the root `.env` file).

## Scripts

- **check_connection.py** — Loads `.env` and verifies the app can connect to
  Postgres. Prints the server version and current database on success.

  ```bash
  python database/check_connection.py
  ```

  Exits with code `0` on success, `1` on failure.

- **migrate_to_postgres.py** — Creates the app's tables in Postgres (reusing
  the schema from `src.api.production`) and migrates existing local MVP data
  into them:
  - `results/decisions.sqlite3` → `care_plan_decisions` (upserted per
    `patient_id` + `discharge_ts`)
  - `results/care_plans.jsonl` → `care_plans`
  - `results/audit_log.jsonl` → `audit_events`
  - `data/processed/discharge_records_with_target.csv` → `discharge_records_with_target`
    (full reload each run — static source-of-truth export, keyed on
    `encounter_id`)

  Safe to re-run — already-migrated rows are skipped/upserted, not
  duplicated.

  ```bash
  python database/migrate_to_postgres.py
  ```

- **verify_migration.py** — Compares local file/SQLite/CSV data against
  Postgres row-by-row and reports PASS/FAIL per table, plus current Postgres
  row counts.

  ```bash
  python database/verify_migration.py
  ```

  Exits with code `0` if every local record has a matching Postgres row,
  `1` otherwise.

## Typical workflow

```bash
python database/check_connection.py       # 1. confirm Postgres is reachable
python database/migrate_to_postgres.py    # 2. move local data into Postgres
python database/verify_migration.py       # 3. confirm nothing was lost
```

After migration, set `PERSISTENCE_BACKEND=database` in `.env` so the API
reads/writes decisions, care plans, and audit events from Postgres instead of
the local JSONL/SQLite files.
