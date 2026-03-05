#!/usr/bin/env python3
"""
Pipeline A: Demo transcript → account_memo.json v1 + agent_spec.json v1
Usage: python pipeline_a.py <transcript_path> <account_id>
"""

import sys
import os
import json
import hashlib
import logging
from datetime import datetime
from pathlib import Path

try:
    import google.generativeai as genai
except ImportError:
    print("ERROR: google-generativeai package not installed. Run: pip install google-generativeai")
    sys.exit(1)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("outputs/pipeline_a.log", mode="a"),
    ],
)
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
MODEL = "gemini-2.5-flash-lite"
OUTPUTS_DIR = Path("outputs/accounts")

MEMO_EXTRACTION_PROMPT = """You are an expert at extracting structured business configuration data from sales demo call transcripts for a voice AI answering service.

Extract ALL available information from the transcript below and return ONLY a valid JSON object matching the schema exactly.

RULES:
- NEVER hallucinate or infer values not explicitly stated
- Use null for any field not mentioned
- Use empty arrays [] for list fields with no data
- Capture exact phone numbers, names, and addresses as stated
- Flag anything ambiguous in questions_or_unknowns

SCHEMA:
{
  "account_id": "<string>",
  "company_name": "<string|null>",
  "business_hours": {
    "days": "<string|null>",
    "start": "<string|null>",
    "end": "<string|null>",
    "timezone": "<string|null>",
    "timezone_iana": "<string|null>"
  },
  "office_address": "<string|null>",
  "services_supported": ["<string>"],
  "emergency_definition": ["<string>"],
  "emergency_routing_rules": {
    "primary_contact": {"name": "<string|null>", "phone": "<string|null>"},
    "order": ["<string>"],
    "timeout_seconds": "<number|null>",
    "fallback": "<string|null>"
  },
  "non_emergency_routing_rules": "<string|null>",
  "call_transfer_rules": {
    "timeout_seconds": "<number|null>",
    "retries": "<number|null>",
    "message_if_fails": "<string|null>"
  },
  "integration_constraints": ["<string>"],
  "after_hours_flow_summary": "<string|null>",
  "office_hours_flow_summary": "<string|null>",
  "custom_greeting": "<string|null>",
  "ai_disclosure_preference": "<string|null>",
  "transfer_hold_message": "<string|null>",
  "special_data_capture": ["<string>"],
  "questions_or_unknowns": ["<string>"],
  "notes": "<string|null>",
  "version": "v1",
  "source": "demo_call"
}

Return ONLY the JSON object. No preamble, no explanation, no markdown fences.

TRANSCRIPT:
"""

AGENT_PROMPT_TEMPLATE = """You are an expert voice AI prompt engineer. Generate a complete, production-ready system prompt for a Retell AI voice agent based on the account configuration below.

REQUIREMENTS:
- Professional, warm phone manner
- Business hours flow: greeting → purpose → collect name+number → transfer → fallback if fails → close
- After-hours flow: greeting → determine emergency → emergency branch (collect details, attempt transfer, fallback) OR non-emergency branch (collect message, confirm next-business-day callback)
- NEVER mention internal function calls or tools to caller
- NEVER volunteer AI identity unprompted; if asked directly, use the ai_disclosure_preference
- ALWAYS collect caller name and phone number before any transfer attempt
- Use the custom_greeting if provided
- Reference exact emergency definitions provided
- Reference exact phone numbers for transfers

ACCOUNT CONFIG:
{memo_json}

Generate the complete system prompt text only. No JSON wrapper, no markdown.
"""


# ── Gemini helpers ────────────────────────────────────────────────────────────

def _get_gemini_model(max_tokens: int = 2000):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY environment variable not set")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(
        MODEL,
        generation_config=genai.GenerationConfig(max_output_tokens=max_tokens),
    )


def _clean_json(raw: str) -> str:
    """Strip markdown fences from LLM output."""
    if "```" in raw:
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else parts[0]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


# ── Core functions ────────────────────────────────────────────────────────────

def load_transcript(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def generate_account_id(filepath: str) -> str:
    h = hashlib.md5(filepath.encode()).hexdigest()[:8]
    return f"acct_{h}"


def extract_memo(transcript: str, account_id: str) -> dict:
    log.info("Calling Gemini for memo extraction (Pipeline A)...")
    model = _get_gemini_model(max_tokens=2000)
    response = model.generate_content(MEMO_EXTRACTION_PROMPT + transcript)
    raw = _clean_json(response.text.strip())
    memo = json.loads(raw)
    memo["account_id"] = account_id
    memo["extracted_at"] = datetime.utcnow().isoformat() + "Z"
    return memo


def generate_agent_prompt(memo: dict) -> str:
    log.info("Calling Gemini for agent prompt generation (Pipeline A)...")
    model = _get_gemini_model(max_tokens=3000)
    prompt = AGENT_PROMPT_TEMPLATE.replace("{memo_json}", json.dumps(memo, indent=2))
    response = model.generate_content(prompt)
    return response.text.strip()


def build_agent_spec(memo: dict, system_prompt: str) -> dict:
    er = memo.get("emergency_routing_rules", {})
    ctr = memo.get("call_transfer_rules", {})
    company = memo.get("company_name", "the company")

    primary = er.get("primary_contact", {}) or {}
    order = er.get("order", [])

    return {
        "agent_name": f"{company} - Clara v1",
        "version": "v1",
        "voice_style": "professional, warm, calm",
        "system_prompt": system_prompt,
        "key_variables": {
            "company_name": memo.get("company_name"),
            "business_hours": memo.get("business_hours"),
            "office_address": memo.get("office_address"),
            "emergency_definitions": memo.get("emergency_definition", []),
            "primary_emergency_contact": primary,
            "timezone": (memo.get("business_hours") or {}).get("timezone_iana"),
            "custom_greeting": memo.get("custom_greeting"),
            "ai_disclosure": memo.get("ai_disclosure_preference"),
        },
        "tool_invocation_placeholders": [
            {
                "function": "transfer_call",
                "description": "Transfer call to specified phone number",
                "parameters": {"phone_number": "string", "reason": "string"},
            },
            {
                "function": "end_call",
                "description": "End the call gracefully",
                "parameters": {"reason": "string"},
            },
        ],
        "call_transfer_protocol": {
            "primary_number": primary.get("phone"),
            "transfer_order": order,
            "timeout_seconds": er.get("timeout_seconds") or ctr.get("timeout_seconds", 30),
            "transfer_hold_message": memo.get("transfer_hold_message", "Please hold while I connect you."),
        },
        "fallback_protocol": {
            "message": er.get("fallback", "We are unable to connect you right now. Please leave your name and number and we will call you back as soon as possible."),
            "collect_fields": ["caller_name", "caller_phone", "issue_summary"],
        },
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def save_outputs(account_id: str, memo: dict, agent_spec: dict) -> Path:
    out_dir = OUTPUTS_DIR / account_id / "v1"
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "account_memo.json", "w") as f:
        json.dump(memo, f, indent=2)

    with open(out_dir / "agent_spec.json", "w") as f:
        json.dump(agent_spec, f, indent=2)

    log.info(f"Saved: {out_dir}/account_memo.json")
    log.info(f"Saved: {out_dir}/agent_spec.json")
    return out_dir


def run_pipeline_a(transcript_path: str, account_id: str = None) -> dict:
    log.info(f"=== Pipeline A START | transcript={transcript_path} ===")

    if not account_id:
        account_id = generate_account_id(transcript_path)
        log.info(f"Auto-generated account_id: {account_id}")

    transcript = load_transcript(transcript_path)
    log.info(f"Loaded transcript ({len(transcript)} chars)")

    memo = extract_memo(transcript, account_id)
    log.info(f"Extracted memo for: {memo.get('company_name', 'unknown')}")

    system_prompt = generate_agent_prompt(memo)
    log.info(f"Generated agent prompt ({len(system_prompt)} chars)")

    agent_spec = build_agent_spec(memo, system_prompt)

    out_dir = save_outputs(account_id, memo, agent_spec)

    result = {
        "status": "success",
        "account_id": account_id,
        "company_name": memo.get("company_name"),
        "output_dir": str(out_dir),
        "questions_or_unknowns": memo.get("questions_or_unknowns", []),
    }
    log.info(f"=== Pipeline A DONE | {account_id} ===")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python pipeline_a.py <transcript_path> [account_id]")
        sys.exit(1)

    transcript_path = sys.argv[1]
    account_id = sys.argv[2] if len(sys.argv) > 2 else None

    os.makedirs("outputs", exist_ok=True)

    try:
        result = run_pipeline_a(transcript_path, account_id)
        print(json.dumps(result, indent=2))
    except Exception as e:
        log.error(f"Pipeline A failed: {e}", exc_info=True)
        sys.exit(1)
