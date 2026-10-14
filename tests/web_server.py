"""Browser-test server: real models/retrieval/storage, deterministic hosted-AI responses.

Only this test entry point installs provider stubs and test control endpoints.
"""
import json
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from dotenv import load_dotenv
import httpx
from groq import APIConnectionError
from groq.resources.chat.completions import Completions
import uvicorn

from src.api import local

os.chdir(local.ROOT)
load_dotenv(".env")
os.environ.update({"PERSISTENCE_BACKEND": "local", "AUTH_MODE": "api_key", "REDIS_URL": "",
                   "KAFKA_ENABLED": "false", "KAFKA_CONSUMER_ENABLED": "false", "LANGSMITH_TRACING": "false",
                   "NOTIFICATIONS_ENABLED": "false", "PRECOMPUTE_ON_STARTUP": "false",
                   "RISK_API_BASE_URL": "http://127.0.0.1:8093/api", "RATE_LIMIT_CHAT_PER_MINUTE": "0",
                   "RATE_LIMIT_ASSESSMENT_PER_MINUTE": "0", "RATE_LIMIT_WRITE_PER_MINUTE": "0"})
from src.api import main, production

outage = False


def completion(**kwargs):
    if outage:
        raise APIConnectionError(request=httpx.Request("POST", "https://api.groq.com"))
    messages = kwargs["messages"]
    system = messages[0]["content"]
    content, calls = None, None
    if "You generate targeted search queries" in system:
        content = json.dumps({"Medications": "current medications at discharge",
                              "Follow up": "follow-up care instructions and discharge planning"})
    elif "You are reviewing a draft" in system:
        content = "STATUS: PASS\nISSUES: None\nSUMMARY: Review the draft with the clinician."
    elif "You draft follow-up care plans" in system:
        content = "## KEY RISK FACTORS TO MONITOR\nReview the documented discharge context.\n\n## RECOMMENDED FOLLOW-UP ACTIONS\nSchedule follow-up and medication reconciliation.\n\n## ADDITIONAL REVIEW NOTES\nClinician review required."
    elif kwargs.get("tools"):
        if messages[-1]["role"] != "tool":
            question = messages[1]["content"]
            name = re.sub(r"^Review\s+", "", question, flags=re.I).strip(".?")
            calls = [SimpleNamespace(id="lookup", type="function", function=SimpleNamespace(
                name="find_patient_by_name", arguments=json.dumps({"name": name}))) ]
        else:
            content = "Patient lookup completed. Review the indexed chart and discharge plan."
    else:
        content = "Review the discharge plan with the care team."
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))])


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="copilot-browser-") as folder:
        root = Path(folder)
        production.CARE_DELIVERY_RESULTS_DIR = root / "care_delivery"
        production.OPERATIONS_RESULTS_DIR = root / "operations"
        production.RESULTS_DIR = root
        production.REPORTS_DIR = root / "reports"
        production.DECISIONS_DB_PATH = root / "decisions.sqlite3"
        for name, filename in (("CARE_PLANS_PATH", "care_plans.jsonl"), ("AUDIT_LOG_PATH", "audit.jsonl"),
                               ("FOLLOW_UPS_PATH", "follow_ups.jsonl"), ("REMINDERS_PATH", "reminders.jsonl"),
                               ("NOTIFICATIONS_PATH", "notifications.jsonl"), ("FHIR_WRITEBACKS_PATH", "writebacks.jsonl")):
            setattr(production, name, root / filename)
        original_lock = local.deployment_lock
        local.deployment_lock = lambda: original_lock(root)
        app = local.create_app()

        @app.post("/__test__/outage/{enabled}")
        def toggle_outage(enabled: bool):
            global outage
            outage = enabled
            return {"outage": outage}

        @app.post("/__test__/restart")
        def simulate_restart():
            # A process restart is checked separately; this reproduces loss of volatile cache.
            main._state["assessment_cache"].clear()
            return {"status": "cleared"}

        with patch.object(Completions, "create", side_effect=completion):
            uvicorn.run(app, host="127.0.0.1", port=8093, access_log=False)
