"""Provisioned-runtime integration check: write and read in separate API processes."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]

PROGRAM = r'''
import json, os, sys
from pathlib import Path
from unittest.mock import patch
from dotenv import load_dotenv
load_dotenv('.env')
os.environ.update({'PERSISTENCE_BACKEND':'local','AUTH_MODE':'api_key','REDIS_URL':'',
 'API_KEY':'restart-test-key-0123456789012345','PATIENT_REF_SALT':'restart-test-salt-0123456789012345',
 'KAFKA_ENABLED':'false','KAFKA_CONSUMER_ENABLED':'false','NOTIFICATIONS_ENABLED':'false',
 'LANGSMITH_TRACING':'false','PRECOMPUTE_ON_STARTUP':'false'})
from src.api import local, main, production
from fastapi.testclient import TestClient
from groq.resources.chat.completions import Completions
folder=Path(sys.argv[1])
production.DECISIONS_DB_PATH=folder/'decisions.sqlite3'
production.REPORTS_DIR=folder/'reports'
production.CARE_DELIVERY_RESULTS_DIR=folder/'care_delivery'
production.OPERATIONS_RESULTS_DIR=folder/'operations'
for name in ('CARE_PLANS_PATH','AUDIT_LOG_PATH','FOLLOW_UPS_PATH','REMINDERS_PATH','NOTIFICATIONS_PATH','FHIR_WRITEBACKS_PATH'):
 setattr(production,name,folder/(name.lower()+'.jsonl'))
original_lock=local.deployment_lock
local.deployment_lock=lambda:original_lock(folder)
headers={'X-API-Key':os.environ['API_KEY']}
with patch.object(Completions,'create',side_effect=AssertionError('Saved state must not call Groq')):
 with TestClient(local.create_app()) as client:
  assert client.get('/api/ready').status_code==200
  if sys.argv[2]=='write':
   item=client.get('/api/patients',headers=headers).json()[0]
   patient_id=main._resolve_patient_key(item['patient_ref'])
   production.save_care_plan({**item,'patient_id':patient_id,'draft_plan':'Persistent clinician plan',
      'patient_context_summary':'## Chart context\n- Documented discharge note.',
      'critique_notes':'STATUS: PASS','categories_with_no_match':[]})
   (folder/'episode.json').write_text(json.dumps({'ref':item['patient_ref'],'discharge_ts':item['discharge_ts']}))
   ref=item['patient_ref']; params={'discharge_ts':item['discharge_ts']}
   response=client.post(f'/api/patients/{ref}/decision',params=params,headers=headers,json={'decision':'approved'})
   assert response.status_code==200,response.text
  else:
   episode=json.loads((folder/'episode.json').read_text())
   ref=episode['ref'];params={'discharge_ts':episode['discharge_ts']}
  decision=client.get(f'/api/patients/{ref}/decision',params=params,headers=headers)
  assert decision.status_code==200 and decision.json()['decision']=='approved'
  report=client.get(f'/api/patients/{ref}/report',params=params,headers=headers)
  assert report.status_code==200 and 'Persistent clinician plan' in report.json()['report_markdown']
  assessment=client.get(f'/api/patients/{ref}/assessment',params=params,headers=headers)
  assert assessment.status_code==200 and assessment.json()['draft_plan']=='Persistent clinician plan'
print('Separate-process approval, assessment, and report persistence verified')
'''


@pytest.mark.skipif(not (ROOT / "web/dist/index.html").exists() or not any((ROOT / "models").glob("*.joblib")),
                    reason="Requires a provisioned local model, dataset, retrieval store, and frontend build")
def test_approval_assessment_and_report_survive_a_real_process_restart(tmp_path):
    for phase in ("write", "read"):
        result = subprocess.run([sys.executable, "-c", PROGRAM, str(tmp_path), phase], cwd=ROOT,
                                capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Separate-process" in result.stdout
