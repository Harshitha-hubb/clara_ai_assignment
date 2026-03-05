#!/usr/bin/env python3
"""
Pipeline B: Onboarding transcript → account_memo.json v2 + agent_spec.json v2 + changelog
Usage: python pipeline_b.py <transcript_path> <account_id>
Requires v1 outputs to already exist for the account.
"""

import sys
import os
import json
import logging
import copy
from datetime import datetime
from pathlib import Path

try:
    import google.generativeai as genai
except ImportError:
    print("ERROR: google-generativeai package not installed. Run: pip install google-generativeai")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("outputs/pipeline_b.log", mode="a"),
    ],
)
log = logging.getLogger(__name__)

MODEL = "gemini-2.5-flash-lite"
OUTPUTS_DIR = Path("outputs/accounts")

ONBOARDING_EXTRACTION_PROMPT = """You are extracting UPDATES and CONFIRMATIONS from an onboarding call transcript for a voice AI answering service.

You will receive:
1. The existing v1 account memo (from the demo call)
2. The onboarding call transcript

Your job: Extract ONLY the fields that are NEW, CHANGED, or explicitly CONFIRMED in the onboarding call.

RULES:
- Return a partial JSON object with ONLY the fields that changed or were confirmed
- For nested objects, include the full object if any field within it changed
- NEVER hallucinate or infer values not stated
- Flag new ambiguities in questions_or_unknowns
- If a field is confirmed unchanged, still include it with confirmed=true in the change_reasons

EXISTING V1 MEMO:
{v1_memo}

Return a JSON object with this structure:
{{
  "updates": {{
    // Only include fields that changed or were newly specified
    // Use the same field names as the account_memo schema
  }},
  "confirmations": ["<field_name>: confirmed unchanged"],
  "new_questions": ["<question or ambiguity>"],
  "change_reasons": {{
    "<field_name>": "<reason for change>"
  }}
}}

Return ONLY the JSON object. No preamble, no markdown fences.

ONBOARDING TRANSCRIPT:
"""

AGENT_PROMPT_TEMPLATE = """You are an expert voice AI prompt engineer. Generate a complete, production-ready system prompt for a Retell AI voice agent based on the UPDATED account configuration below.

This is version 2, refined from a demo configuration with confirmed onboarding details.

REQUIREMENTS:
- Professional, warm phone manner
- Business hours flow: greeting → purpose → collect name+number → transfer → fallback if fails → close
- After-hours flow: greeting → determine emergency → emergency branch (collect details, attempt transfer, fallback) OR non-emergency branch (collect message, confirm next-business-day callback)
- NEVER mention internal function calls or tools to caller
- NEVER volunteer AI identity unprompted; if asked, use the ai_disclosure_preference
- ALWAYS collect caller name and phone number before any transfer attempt
- Use the custom_greeting if provided
- Reference exact emergency definitions
- Reference exact confirmed phone numbers

ACCOUNT CONFIG (v2):
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
    if "```" in raw:
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else parts[0]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


# ── Data helpers ──────────────────────────────────────────────────────────────

def deep_merge(base: dict, updates: dict) -> dict:
    """Recursively merge updates into base dict."""
    result = copy.deepcopy(base)
    for key, val in updates.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(val, dict)
        ):
            result[key] = deep_merge(result[key], val)
        elif isinstance(val, list) and key in result and isinstance(result[key], list):
            existing = result[key]
            for item in val:
                if item not in existing:
                    existing.append(item)
            result[key] = existing
        else:
            result[key] = val
    return result


def compute_diff(v1: dict, v2: dict, path: str = "") -> list:
    """Return list of change records between v1 and v2."""
    changes = []
    all_keys = set(list(v1.keys()) + list(v2.keys()))
    for key in all_keys:
        full_path = f"{path}.{key}" if path else key
        if key not in v1:
            changes.append({"field": full_path, "change_type": "added", "old": None, "new": v2[key]})
        elif key not in v2:
            changes.append({"field": full_path, "change_type": "removed", "old": v1[key], "new": None})
        elif isinstance(v1[key], dict) and isinstance(v2[key], dict):
            changes.extend(compute_diff(v1[key], v2[key], full_path))
        elif v1[key] != v2[key]:
            changes.append({"field": full_path, "change_type": "modified", "old": v1[key], "new": v2[key]})
    return changes


# ── Core functions ────────────────────────────────────────────────────────────

def load_transcript(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def load_v1_memo(account_id: str) -> dict:
    memo_path = OUTPUTS_DIR / account_id / "v1" / "account_memo.json"
    if not memo_path.exists():
        raise FileNotFoundError(f"v1 memo not found at {memo_path}. Run Pipeline A first.")
    with open(memo_path) as f:
        return json.load(f)


def extract_updates(transcript: str, v1_memo: dict) -> dict:
    log.info("Calling Gemini for onboarding update extraction (Pipeline B)...")
    model = _get_gemini_model(max_tokens=2000)
    prompt = ONBOARDING_EXTRACTION_PROMPT.replace(
        "{v1_memo}", json.dumps(v1_memo, indent=2)
    ) + transcript
    response = model.generate_content(prompt)
    raw = _clean_json(response.text.strip())
    return json.loads(raw)


def generate_agent_prompt(memo: dict) -> str:
    log.info("Calling Gemini for agent prompt generation (Pipeline B)...")
    model = _get_gemini_model(max_tokens=3000)
    prompt = AGENT_PROMPT_TEMPLATE.replace("{memo_json}", json.dumps(memo, indent=2))
    response = model.generate_content(prompt)
    return response.text.strip()


def build_agent_spec(memo: dict, system_prompt: str) -> dict:
    er = memo.get("emergency_routing_rules", {}) or {}
    ctr = memo.get("call_transfer_rules", {}) or {}
    company = memo.get("company_name", "the company")
    primary = er.get("primary_contact", {}) or {}
    order = er.get("order", [])

    return {
        "agent_name": f"{company} - Clara v2",
        "version": "v2",
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
            "transfer_hold_message": memo.get(
                "transfer_hold_message", "Please hold while I connect you."
            ),
        },
        "fallback_protocol": {
            "message": er.get(
                "fallback",
                "We are unable to connect you right now. Please leave your name and number and we will call you back.",
            ),
            "collect_fields": ["caller_name", "caller_phone", "issue_summary"],
        },
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def build_changelog(account_id: str, v1_memo: dict, v2_memo: dict, change_reasons: dict) -> tuple:
    """Returns (changelog_dict, changelog_md_string)"""
    changes = compute_diff(v1_memo, v2_memo)

    skip_fields = {"version", "source", "extracted_at"}
    changes = [c for c in changes if not any(s in c["field"] for s in skip_fields)]

    for change in changes:
        field = change["field"]
        change["reason"] = change_reasons.get(field, "Updated during onboarding call")

    changelog_dict = {
        "account_id": account_id,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "v1_source": "demo_call",
        "v2_source": "onboarding_call",
        "total_changes": len(changes),
        "changes": changes,
    }

    lines = [
        f"# Changelog — {account_id}",
        f"**Generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Changes:** {len(changes)} field(s) updated from demo → onboarding",
        "",
        "## Summary of Changes",
        "",
    ]

    if not changes:
        lines.append("_No changes detected. All fields confirmed from demo call._")
    else:
        for c in changes:
            old_val = json.dumps(c["old"]) if c["old"] is not None else "_not set_"
            new_val = json.dumps(c["new"]) if c["new"] is not None else "_removed_"
            lines.append(f"### `{c['field']}` ({c['change_type'].upper()})")
            lines.append(f"- **Before:** {old_val}")
            lines.append(f"- **After:** {new_val}")
            if c.get("reason"):
                lines.append(f"- **Reason:** {c['reason']}")
            lines.append("")

    return changelog_dict, "\n".join(lines)


def save_outputs(account_id: str, v2_memo: dict, agent_spec: dict, changelog_dict: dict, changelog_md: str) -> Path:
    out_dir = OUTPUTS_DIR / account_id / "v2"
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "account_memo.json", "w") as f:
        json.dump(v2_memo, f, indent=2)
    with open(out_dir / "agent_spec.json", "w") as f:
        json.dump(agent_spec, f, indent=2)
    with open(out_dir / "changelog.json", "w") as f:
        json.dump(changelog_dict, f, indent=2)
    with open(out_dir / "changelog.md", "w") as f:
        f.write(changelog_md)

    log.info(f"Saved v2 outputs to {out_dir}")
    return out_dir


def run_pipeline_b(transcript_path: str, account_id: str) -> dict:
    log.info(f"=== Pipeline B START | transcript={transcript_path} | account={account_id} ===")

    transcript = load_transcript(transcript_path)
    log.info(f"Loaded transcript ({len(transcript)} chars)")

    v1_memo = load_v1_memo(account_id)
    log.info(f"Loaded v1 memo for: {v1_memo.get('company_name', 'unknown')}")

    extraction_result = extract_updates(transcript, v1_memo)
    updates = extraction_result.get("updates", {})
    change_reasons = extraction_result.get("change_reasons", {})
    new_questions = extraction_result.get("new_questions", [])
    log.info(f"Extracted {len(updates)} update fields")

    v2_memo = deep_merge(v1_memo, updates)
    v2_memo["version"] = "v2"
    v2_memo["source"] = "onboarding_call"
    v2_memo["extracted_at"] = datetime.utcnow().isoformat() + "Z"

    existing_questions = v2_memo.get("questions_or_unknowns", [])
    for q in new_questions:
        if q not in existing_questions:
            existing_questions.append(q)
    v2_memo["questions_or_unknowns"] = existing_questions

    system_prompt = generate_agent_prompt(v2_memo)
    agent_spec = build_agent_spec(v2_memo, system_prompt)

    v1_memo_clean = load_v1_memo(account_id)
    changelog_dict, changelog_md = build_changelog(account_id, v1_memo_clean, v2_memo, change_reasons)

    out_dir = save_outputs(account_id, v2_memo, agent_spec, changelog_dict, changelog_md)

    result = {
        "status": "success",
        "account_id": account_id,
        "company_name": v2_memo.get("company_name"),
        "output_dir": str(out_dir),
        "total_changes": changelog_dict["total_changes"],
        "questions_or_unknowns": v2_memo.get("questions_or_unknowns", []),
    }
    log.info(f"=== Pipeline B DONE | {account_id} | changes={changelog_dict['total_changes']} ===")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python pipeline_b.py <transcript_path> <account_id>")
        sys.exit(1)

    transcript_path = sys.argv[1]
    account_id = sys.argv[2]

    os.makedirs("outputs", exist_ok=True)

    try:
        result = run_pipeline_b(transcript_path, account_id)
        print(json.dumps(result, indent=2))
    except Exception as e:
        log.error(f"Pipeline B failed: {e}", exc_info=True)
        sys.exit(1)
