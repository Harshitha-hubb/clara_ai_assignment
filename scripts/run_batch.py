#!/usr/bin/env python3
"""
Batch runner: Processes all demo/onboarding pairs from manifest.json
Usage: python run_batch.py [--dataset ./dataset] [--manifest ./dataset/manifest.json]
"""

import sys
import os
import json
import argparse
import logging
import traceback
from datetime import datetime
from pathlib import Path

# Add scripts dir to path
sys.path.insert(0, str(Path(__file__).parent))

from pipeline_a import run_pipeline_a
from pipeline_b import run_pipeline_b

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("outputs/batch.log", mode="a"),
    ],
)
log = logging.getLogger(__name__)


def run_batch(dataset_dir: str, manifest_path: str) -> dict:
    os.makedirs("outputs", exist_ok=True)

    with open(manifest_path) as f:
        manifest = json.load(f)

    log.info(f"=== BATCH START | {len(manifest)} accounts ===")

    summary = {
        "started_at": datetime.utcnow().isoformat() + "Z",
        "total": len(manifest),
        "success": 0,
        "failed": 0,
        "accounts": [],
    }

    for idx, entry in enumerate(manifest, 1):
        account_id = entry.get("account_id")
        demo_path = entry.get("demo")
        onboarding_path = entry.get("onboarding")

        log.info(f"\n[{idx}/{len(manifest)}] Processing account: {account_id}")

        account_result = {
            "account_id": account_id,
            "demo_path": demo_path,
            "onboarding_path": onboarding_path,
            "pipeline_a": None,
            "pipeline_b": None,
            "status": "pending",
        }

        # ── Pipeline A ────────────────────────────────────────────────────────
        try:
            if not Path(demo_path).exists():
                raise FileNotFoundError(f"Demo transcript not found: {demo_path}")

            result_a = run_pipeline_a(demo_path, account_id)
            account_result["pipeline_a"] = result_a
            log.info(f"  ✓ Pipeline A complete: {account_id}")

        except Exception as e:
            log.error(f"  ✗ Pipeline A FAILED for {account_id}: {e}")
            account_result["pipeline_a"] = {"status": "error", "error": str(e), "traceback": traceback.format_exc()}
            account_result["status"] = "failed"
            summary["failed"] += 1
            summary["accounts"].append(account_result)
            continue  # Skip Pipeline B if A failed

        # ── Pipeline B ────────────────────────────────────────────────────────
        try:
            if not Path(onboarding_path).exists():
                raise FileNotFoundError(f"Onboarding transcript not found: {onboarding_path}")

            result_b = run_pipeline_b(onboarding_path, account_id)
            account_result["pipeline_b"] = result_b
            account_result["status"] = "success"
            summary["success"] += 1
            log.info(f"  ✓ Pipeline B complete: {account_id} | changes={result_b.get('total_changes', 0)}")

        except Exception as e:
            log.error(f"  ✗ Pipeline B FAILED for {account_id}: {e}")
            account_result["pipeline_b"] = {"status": "error", "error": str(e), "traceback": traceback.format_exc()}
            account_result["status"] = "partial"
            summary["failed"] += 1

        summary["accounts"].append(account_result)

    summary["completed_at"] = datetime.utcnow().isoformat() + "Z"

    # Save summary
    summary_path = Path("outputs/batch_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    log.info(f"\n=== BATCH DONE | success={summary['success']} failed={summary['failed']} ===")
    log.info(f"Summary saved to {summary_path}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Batch process all Clara pipeline accounts")
    parser.add_argument("--dataset", default="./dataset", help="Path to dataset directory")
    parser.add_argument("--manifest", default="./dataset/manifest.json", help="Path to manifest.json")
    args = parser.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: GEMINI_API_KEY environment variable not set")
        sys.exit(1)

    summary = run_batch(args.dataset, args.manifest)

    print("\n" + "=" * 60)
    print(f"BATCH COMPLETE")
    print(f"  Total:   {summary['total']}")
    print(f"  Success: {summary['success']}")
    print(f"  Failed:  {summary['failed']}")
    print("=" * 60)

    # Print any accounts with failures or open questions
    for acc in summary["accounts"]:
        if acc["status"] != "success":
            print(f"  ⚠ {acc['account_id']}: {acc['status']}")

        b_result = acc.get("pipeline_b") or {}
        questions = b_result.get("questions_or_unknowns", [])
        if questions:
            print(f"\n  ❓ Open questions for {acc['account_id']}:")
            for q in questions:
                print(f"     - {q}")


if __name__ == "__main__":
    main()
