"""Verify connectivity to the PostgreSQL database defined in .env.

Usage:
    python database/check_connection.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

# Load .env from the project root regardless of the caller's cwd
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

DATABASE_URL = os.getenv("DATABASE_URL")


def check_connection() -> bool:
    if not DATABASE_URL:
        print("DATABASE_URL is not set. Check your .env file.")
        return False

    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version()")).scalar_one()
            db_name = conn.execute(text("SELECT current_database()")).scalar_one()
        print("Connection successful.")
        print(f"  Database : {db_name}")
        print(f"  Server   : {version}")
        return True
    except SQLAlchemyError as exc:
        print("Connection failed.")
        print(f"  Error: {exc}")
        return False
    finally:
        engine.dispose()


if __name__ == "__main__":
    ok = check_connection()
    sys.exit(0 if ok else 1)
