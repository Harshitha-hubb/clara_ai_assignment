# Clara Answers — Automation Pipeline

A zero-cost, reproducible automation pipeline that converts demo and onboarding call transcripts into versioned Retell AI voice agent configurations.

---

## Quick Start

### 1. Clone and Install

```bash
git clone <your-repo-url>
cd clara-pipeline
pip install google-generativeai
```

### 2. Set your Gemini API Key

```bash
export GEMINI_API_KEY=your-gemini-api-key-here
```

Or create a `.env` file:
```
GEMINI_API_KEY=your-gemini-key-here
```
Then: `set -a && source .env && set +a`

### 3. Run Everything (Full Batch)

```bash
python scripts/run_batch.py --dataset ./dataset --manifest ./dataset/manifest.json
```

This processes all 10 demo + onboarding pairs and writes all outputs to `outputs/accounts/`.

---

## Architecture

```
Transcript (demo or onboarding)
        │
        ▼
┌─────────────────────────────────────────────┐
│               PIPELINE A                   │
│  Load Transcript → LLM Extract Memo v1     │
│  → Generate Agent Prompt → agent_spec v1   │
└─────────────────────────────────────────────┘
        │
        ▼
 outputs/accounts/<account_id>/v1/
   ├── account_memo.json
   └── agent_spec.json

Onboarding Transcript
        │
        ▼
┌─────────────────────────────────────────────┐
│               PIPELINE B                   │
│  Load v1 Memo → LLM Extract Updates        │
│  → Deep Merge → Diff v1 vs v2              │
│  → Write Memo v2, Agent Spec v2, Changelog │
└─────────────────────────────────────────────┘
        │
        ▼
 outputs/accounts/<account_id>/v2/
   ├── account_memo.json
   ├── agent_spec.json
   ├── changelog.json
   └── changelog.md
```

---

## Directory Structure

```
clara-pipeline/
├── README.md
├── docker-compose.yml              # n8n local setup
├── dataset/
│   ├── manifest.json               # Maps demo → onboarding → account_id
│   ├── demo/                       # Demo call transcripts (10 files)
│   └── onboarding/                 # Onboarding call transcripts (10 files)
├── scripts/
│   ├── pipeline_a.py               # Demo → v1 assets
│   ├── pipeline_b.py               # Onboarding → v2 assets + changelog
│   └── run_batch.py                # Batch all 10 pairs
├── workflows/
│   └── clara_n8n_workflow.json     # n8n import file
└── outputs/
    ├── pipeline_a.log
    ├── pipeline_b.log
    ├── batch_summary.json
    └── accounts/
        └── <account_id>/
            ├── v1/
            │   ├── account_memo.json
            │   └── agent_spec.json
            └── v2/
                ├── account_memo.json
                ├── agent_spec.json
                ├── changelog.json
                └── changelog.md
```

---

## Running Individual Pipelines

### Pipeline A — Demo → v1

```bash
python scripts/pipeline_a.py dataset/demo/account_01_demo.txt acct_fire001
```

**Output:**
```
outputs/accounts/acct_fire001/v1/account_memo.json
outputs/accounts/acct_fire001/v1/agent_spec.json
```

### Pipeline B — Onboarding → v2 + Changelog

```bash
python scripts/pipeline_b.py dataset/onboarding/account_01_onboarding.txt acct_fire001
```

**Output:**
```
outputs/accounts/acct_fire001/v2/account_memo.json
outputs/accounts/acct_fire001/v2/agent_spec.json
outputs/accounts/acct_fire001/v2/changelog.json
outputs/accounts/acct_fire001/v2/changelog.md
```

---

## n8n Local Setup (Visual Workflow Orchestrator)

### Prerequisites
- Docker and Docker Compose installed
- `GEMINI_API_KEY` set in your environment or `.env`

### Start n8n

```bash
docker-compose up -d
```

n8n will be running at: **http://localhost:5678**

Default login:
- Username: `admin`
- Password: `clarapassword123`

> ⚠️ Change these in `docker-compose.yml` before sharing or deploying.

### Import the Workflow

1. Open **http://localhost:5678**
2. Log in with the credentials above
3. Click **+** → **Import from File**
4. Select `workflows/clara_n8n_workflow.json`
5. Go to **Credentials** → Add your Gemini API key as a plain text credential named `GEMINI_API_KEY`
6. Activate the workflow

### Set Up Gemini API Key in n8n

1. In n8n, go to **Settings → Credentials → New Credential**
2. Go to **Settings → Variables** and add `GEMINI_API_KEY`
3. Enter your API key
4. Save with name `GEMINI_API_KEY`
5. Save

### Trigger Pipeline A via Webhook

```bash
curl -X POST http://localhost:5678/webhook/demo-call \
  -H "Content-Type: application/json" \
  -d '{
    "transcript": "PASTE TRANSCRIPT TEXT HERE",
    "account_id": "acct_fire001"
  }'
```

### Trigger Pipeline B via Webhook

```bash
curl -X POST http://localhost:5678/webhook/onboarding-call \
  -H "Content-Type: application/json" \
  -d '{
    "transcript": "PASTE ONBOARDING TRANSCRIPT HERE",
    "account_id": "acct_fire001",
    "v1_memo": { "...paste v1 account_memo.json here..." }
  }'
```

### n8n Workflow Nodes

| Node | Purpose |
|------|---------|
| Webhook: Demo Call | Receives POST to `/webhook/demo-call` |
| Validate Input A | Checks required fields exist |
| LLM: Extract Memo v1 | Claude extracts structured config from transcript |
| Build Memo v1 | Parses LLM output, attaches account_id + timestamp |
| LLM: Generate Agent Prompt v1 | Claude writes the full Retell system prompt |
| Write v1 Outputs | Saves `account_memo.json` + `agent_spec.json` to disk |
| Respond: Pipeline A | Returns JSON result to caller |
| Webhook: Onboarding Call | Receives POST to `/webhook/onboarding-call` |
| Validate Input B | Checks transcript + account_id + v1_memo present |
| LLM: Extract Updates v2 | Claude extracts only changed/new fields |
| Build Memo v2 + Changelog | Deep merges v1 → v2, computes field-level diff |
| LLM: Generate Agent Prompt v2 | Claude writes updated production prompt |
| Write v2 Outputs | Saves memo, spec, changelog.json, changelog.md |
| Respond: Pipeline B | Returns JSON result |

---

## Output Schema Reference

### account_memo.json

| Field | Type | Description |
|-------|------|-------------|
| `account_id` | string | Unique identifier |
| `company_name` | string\|null | Client company name |
| `business_hours` | object | `days`, `start`, `end`, `timezone`, `timezone_iana` |
| `office_address` | string\|null | Physical office address |
| `services_supported` | array | Service categories handled |
| `emergency_definition` | array | Triggers classifying a call as emergency |
| `emergency_routing_rules` | object | `primary_contact`, `order`, `timeout_seconds`, `fallback` |
| `non_emergency_routing_rules` | string\|null | How to handle non-urgent after-hours |
| `call_transfer_rules` | object | `timeout_seconds`, `retries`, `message_if_fails` |
| `integration_constraints` | array | System-specific rules |
| `custom_greeting` | string\|null | Exact greeting text for agent |
| `ai_disclosure_preference` | string\|null | What to say if asked if it's AI |
| `transfer_hold_message` | string\|null | Message while transferring |
| `special_data_capture` | array | Extra fields to collect (job numbers, claim numbers, etc.) |
| `questions_or_unknowns` | array | Flagged ambiguities |
| `version` | string | `v1` or `v2` |
| `source` | string | `demo_call` or `onboarding_call` |

### agent_spec.json

| Field | Type | Description |
|-------|------|-------------|
| `agent_name` | string | Display name for Retell |
| `version` | string | `v1` or `v2` |
| `voice_style` | string | Tone descriptor |
| `system_prompt` | string | Full production-ready agent prompt |
| `key_variables` | object | Flattened config snapshot |
| `tool_invocation_placeholders` | array | Function signatures |
| `call_transfer_protocol` | object | Transfer logic config |
| `fallback_protocol` | object | Fallback message + data collection |

---

## Dataset Accounts (Pre-loaded)

| Account ID | Company | Industry | Location |
|------------|---------|----------|----------|
| `acct_fire001` | FireGuard Pro Services | Fire Protection | Dallas, TX |
| `acct_plumb002` | RapidFlow Plumbing | Plumbing | Phoenix, AZ |
| `acct_hvac003` | ComfortZone HVAC | HVAC | Atlanta, GA |
| `acct_elec004` | Voltage Masters Electric | Electrical | Seattle, WA |
| `acct_pest005` | ShieldPest Solutions | Pest Control | Miami, FL |
| `acct_roof006` | SkyHigh Roofing | Roofing | Denver, CO |
| `acct_land007` | GreenScape Landscaping | Landscaping | Austin, TX |
| `acct_clean008` | PristineClean Commercial | Commercial Cleaning | Chicago, IL |
| `acct_pool009` | AquaClear Pool Services | Pool Services | San Diego, CA |
| `acct_garage010` | OptiDoor Garage Solutions | Garage Doors | Nashville, TN |

---

## Zero-Cost Compliance

| Component | Tool | Cost |
|-----------|------|------|
| LLM extraction | Google Gemini (API key) | $0* |
| Orchestration | Python scripts | $0 |
| Visual orchestration | n8n (self-hosted Docker) | $0 |
| Storage | Local filesystem | $0 |
| Transcription | Whisper (if needed) | $0 |

*Claude API has a free tier; standard usage applies.

---

## Retell Integration

**Free tier limitation:** Retell's free tier does not support programmatic agent creation via API.

**Manual import steps:**
1. Log in at `app.retellai.com`
2. Click **Create New Agent**
3. Set agent name from `agent_spec.json → agent_name`
4. Paste `system_prompt` into the system prompt field
5. Configure voice per `voice_style`
6. Set up transfer tools using `tool_invocation_placeholders` as reference
7. Set timeout from `call_transfer_protocol.timeout_seconds`

**When Retell API access is available**, replace the file-write step with the API call documented in the original README.

---

## Adding Your Own Transcripts

1. Drop demo transcripts as `.txt` files in `dataset/demo/`
2. Drop onboarding transcripts as `.txt` files in `dataset/onboarding/`
3. Add entries to `dataset/manifest.json`:

```json
{
  "demo": "./dataset/demo/your_demo.txt",
  "onboarding": "./dataset/onboarding/your_onboarding.txt",
  "account_id": "acct_yourcode"
}
```

4. Run the batch: `python scripts/run_batch.py`

---

## Troubleshooting

**`GEMINI_API_KEY` not set:**
```bash
export GEMINI_API_KEY=your-gemini-key-here
```

**n8n can't write files:**  
The `outputs/` directory is mounted into the Docker container at `/home/node/outputs`. n8n's Code nodes write to `process.cwd()` which resolves there.

**LLM returns non-JSON:**  
The scripts strip markdown code fences automatically. If the model returns unexpected formats, check `outputs/pipeline_a.log` or `pipeline_b.log` for raw responses.

**Pipeline B fails with "v1 memo not found":**  
Run Pipeline A first for the account, or ensure `outputs/accounts/<account_id>/v1/account_memo.json` exists.
