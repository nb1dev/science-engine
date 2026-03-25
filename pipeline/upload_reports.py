#!/usr/bin/env python3
"""
Upload analysis reports to the NB1 admin platform API.

Uploads per-sample files to PATCH https://api.nb1.com/lab/report-data

File types available:
  --health-report     health_report_interpretations_*.json  → report_json
  --narrative-report  narrative_report_*.pdf                → report_pdf
  --decision-trace    decision_trace_*.json                 → guide_json
  --recipe            manufacturing_recipe_*.pdf            → guide_pdf

Default (no file type flags): all 4 types.

Usage examples:
  # Upload all 4 types for a single batch:
  python science-engine/pipeline/upload_reports.py --batch nb1_2026_011

  # Upload only decision trace + recipe for multiple batches:
  python science-engine/pipeline/upload_reports.py \\
      --batch nb1_2026_009 nb1_2026_010 nb1_2026_011 \\
      --decision-trace --recipe

  # Upload only health-report JSON for all batches:
  python science-engine/pipeline/upload_reports.py --all-batches --health-report

  # Upload everything for all batches with a fresh token:
  python science-engine/pipeline/upload_reports.py --all-batches --token "eyJ..."
"""

import argparse
import json
import os
import ssl
import subprocess
import sys
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
WORK_DIR = SCRIPT_DIR.parent.parent          # science-engine/pipeline → work
ANALYSIS_DIR = WORK_DIR / "analysis"
API_URL = "https://api.nb1.com/lab/report-data"

# ── Default token (refresh when expired) ─────────────────────────────────────
DEFAULT_TOKEN = (
    "eyJhbGciOiJSUzI1NiIsImtpZCI6IjczMmNhOTY3MTNiMWRkMTcyMzg1MDg0Y2U5ZjQzODFhZD"
    "AwY2VjZTQiLCJ0eXAiOiJKV1QifQ.eyJuYW1lIjoiUG9saW5hIE5vdmlrb3ZhIiwicGljdHVyZSI"
    "6Imh0dHBzOi8vbGgzLmdvb2dsZXVzZXJjb250ZW50LmNvbS9hL0FDZzhvY0tsSVlPTEdQQUYtdEx"
    "oRjBWWDBqUUZXcm15Q3FjRVNKMUtCOFh0LVFtcnlTT0Jxd1BzVFE9czk2LWMiLCJpc3MiOiJodHR"
    "wczovL3NlY3VyZXRva2VuLmdvb2dsZS5jb20vbmIxLWhlYWx0aC1kOTcyZCIsImF1ZCI6Im5iMS1o"
    "ZWFsdGgtZDk3MmQiLCJhdXRoX3RpbWUiOjE3NzM4MjM2NTUsInVzZXJfaWQiOiJGdE9WS1Nib1Iy"
    "V3hiUUdPcTBKUjNrUGlvamwyIiwic3ViIjoiRnRPVktTYm9SMld4YlFHT3EwSlIza1Bpb2psMiIsI"
    "mlhdCI6MTc3NDM3ODI1MywiZXhwIjoxNzc0MzgxODUzLCJlbWFpbCI6Im5vZmYucG9saW5hQGdtYWl"
    "sLmNvbSIsImVtYWlsX3ZlcmlmaWVkIjp0cnVlLCJmaXJlYmFzZSI6eyJpZGVudGl0aWVzIjp7Imdvb"
    "2dsZS5jb20iOlsiMTA3NjU2MDI2MzMxNTk3NTUxNjcwIl0sImVtYWlsIjpbIm5vZmYucG9saW5hQGd"
    "tYWlsLmNvbSJdfSwic2lnbl9pbl9wcm92aWRlciI6Imdvb2dsZS5jb20ifX0.kBU8xrmX_BrBBpPjYZ"
    "LeX_QF_Y165ePRSiwjtHqUtmPA9Nd7alWz4TwiaV0Ch9-hzPjlrTZs8fgQiDMSmDafI2SClJmkNQbC_"
    "BDvLV-X3Wjx2jKPrggda7p5_cWCJRHKLoIm_64mIFOgRQUnFs-iEz2eyhSoux-YFiARUD0OGqA3qbSU"
    "Jn3zVtUGeUgkqwT0KyM3MMuoNfAnnvL08fMzEZ4vNFWfP0AqKdjdiUUiQj0EPDS5J9-ptGQB9q-7cqX"
    "TbNnpAUCxMNYndjh9jf_967UqfQmtE3zlkav1--qrN3HDqYxZOTFKLLjBpdtLX9TWCS5-lGsCGpVRgn"
    "4l8buJcw"
)

# ── File type definitions ─────────────────────────────────────────────────────
FILE_TYPES = {
    "health_report": {
        "label": "health-report",
        "pattern": "health_report_interpretations_{sample}.json",
        "subdir": "reports/reports_json",
        "api_field": "report_json",
        "upload_as": "string",   # sent as text content, not file
    },
    "narrative_report": {
        "label": "narrative-report",
        "pattern": "narrative_report_{sample}.pdf",
        "subdir": "reports/reports_pdf",
        "api_field": "report_pdf",
        "upload_as": "file",
        "mime": "application/pdf",
    },
    "decision_trace": {
        "label": "decision-trace",
        "pattern": "decision_trace_{sample}.json",
        "subdir": "reports/reports_json",
        "api_field": "guide_json",
        "upload_as": "string",
    },
    "recipe": {
        "label": "recipe",
        "pattern": "manufacturing_recipe_{sample}.pdf",
        "subdir": "reports/reports_pdf",
        "api_field": "guide_pdf",
        "upload_as": "file",
        "mime": "application/pdf",
    },
}


def resolve_batches(batch_args, all_batches):
    """Return sorted list of batch directory paths to process."""
    if all_batches:
        batches = sorted(
            d for d in ANALYSIS_DIR.iterdir()
            if d.is_dir() and d.name.startswith("nb1_2026_")
        )
    else:
        batches = []
        for b in batch_args:
            bd = ANALYSIS_DIR / b
            if not bd.exists():
                print(f"⚠️  Batch not found, skipping: {b}")
            else:
                batches.append(bd)
    return batches


def resolve_file_types(args):
    """Return list of file type keys to upload."""
    selected = []
    if args.health_report:
        selected.append("health_report")
    if args.narrative_report:
        selected.append("narrative_report")
    if args.decision_trace:
        selected.append("decision_trace")
    if args.recipe:
        selected.append("recipe")
    # Default = all
    if not selected:
        selected = list(FILE_TYPES.keys())
    return selected


def build_curl_cmd(sample_id, files_to_upload, token):
    """
    Build a curl command for one sample.
    Returns (cmd_list, missing_files) tuple.
    """
    cmd = [
        "curl", "-s", "-X", "PATCH", API_URL,
        "-H", "accept: application/json",
        "-H", f"Authorization: Bearer {token}",
        "-F", f"kit_number={sample_id}",
    ]

    missing = []
    for key in files_to_upload:
        ft = FILE_TYPES[key]
        path = ft["path"]  # resolved path injected by caller
        if not path.exists():
            missing.append(ft["label"])
            continue

        field = ft["api_field"]
        if ft["upload_as"] == "string":
            cmd += ["-F", f"{field}=<{path}"]
        else:
            cmd += ["-F", f"{field}=@{path};type={ft['mime']}"]

    return cmd, missing


def upload_sample(sample_id, sample_dir, file_type_keys, token, dry_run):
    """Upload files for one sample. Returns status string."""
    # Resolve actual file paths into a copy of FILE_TYPES
    resolved = {}
    for key in file_type_keys:
        ft = dict(FILE_TYPES[key])
        filename = ft["pattern"].replace("{sample}", sample_id)
        ft["path"] = sample_dir / ft["subdir"] / filename
        resolved[key] = ft

    # Check which files exist
    present = [k for k in file_type_keys if resolved[k]["path"].exists()]
    missing = [resolved[k]["label"] for k in file_type_keys if not resolved[k]["path"].exists()]

    if not present:
        return "SKIP", f"all requested files missing ({', '.join(missing)})"

    # Build curl command using only present files
    cmd = [
        "curl", "-s", "-X", "PATCH", API_URL,
        "-H", "accept: application/json",
        "-H", f"Authorization: Bearer {token}",
        "-F", f"kit_number={sample_id}",
    ]
    for key in present:
        ft = resolved[key]
        field = ft["api_field"]
        path = ft["path"]
        if ft["upload_as"] == "string":
            cmd += ["-F", f"{field}=<{path}"]
        else:
            cmd += ["-F", f"{field}=@{path};type={ft['mime']}"]

    if dry_run:
        uploaded = [resolved[k]["label"] for k in present]
        note = f"[DRY RUN] would upload: {', '.join(uploaded)}"
        if missing:
            note += f" | missing (skipped): {', '.join(missing)}"
        return "DRY", note

    result = subprocess.run(cmd, capture_output=True, text=True)
    response_text = result.stdout.strip()

    # Try to parse JSON response
    try:
        resp = json.loads(response_text)
        if isinstance(resp, dict) and resp.get("detail"):
            detail = resp["detail"]
            if "expired" in str(detail).lower() or "401" in str(detail).lower():
                return "AUTH_ERROR", f"Token expired or unauthorized: {detail}"
            return "API_ERROR", f"API error: {detail}"
    except json.JSONDecodeError:
        pass  # Not JSON, that's ok

    if result.returncode != 0:
        return "ERROR", f"curl error: {result.stderr.strip()}"

    uploaded = [resolved[k]["label"] for k in present]
    note = f"uploaded: {', '.join(uploaded)}"
    if missing:
        note += f" | missing (skipped): {', '.join(missing)}"
    return "OK", note


def main():
    parser = argparse.ArgumentParser(
        description="Upload analysis reports to api.nb1.com",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Batch selection
    batch_group = parser.add_mutually_exclusive_group(required=True)
    batch_group.add_argument(
        "--batch", nargs="+", metavar="BATCH",
        help="One or more batch names (e.g. nb1_2026_011 nb1_2026_010)"
    )
    batch_group.add_argument(
        "--all-batches", action="store_true",
        help="Process all nb1_2026_* batches in analysis/"
    )

    # File type selection (default = all)
    parser.add_argument("--health-report", action="store_true",
                        help="Upload health_report_interpretations JSON (report_json)")
    parser.add_argument("--narrative-report", action="store_true",
                        help="Upload narrative_report PDF (report_pdf)")
    parser.add_argument("--decision-trace", action="store_true",
                        help="Upload decision_trace JSON (guide_json)")
    parser.add_argument("--recipe", action="store_true",
                        help="Upload manufacturing_recipe PDF (guide_pdf)")

    # Auth
    parser.add_argument("--token", default=None,
                        help="Bearer token (uses hardcoded default if not provided)")

    # Dry run
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be uploaded without actually sending")

    args = parser.parse_args()

    token = args.token or DEFAULT_TOKEN
    file_type_keys = resolve_file_types(args)
    batches = resolve_batches(args.batch, args.all_batches)

    if not batches:
        print("No batches found. Exiting.")
        sys.exit(1)

    type_labels = [FILE_TYPES[k]["label"] for k in file_type_keys]
    batch_names = [b.name for b in batches]

    print("=" * 70)
    print("NB1 REPORT UPLOAD")
    print("=" * 70)
    print(f"Batches  : {', '.join(batch_names)}")
    print(f"Files    : {', '.join(type_labels)}")
    print(f"Endpoint : {API_URL}")
    if args.dry_run:
        print("Mode     : DRY RUN (nothing will be sent)")
    print("=" * 70)

    # Counters
    total = ok = skipped = errors = auth_errors = 0

    for batch_dir in batches:
        batch_name = batch_dir.name
        samples = sorted(
            d.name for d in batch_dir.iterdir()
            if d.is_dir() and d.name.isdigit()
        )

        if not samples:
            print(f"\n[{batch_name}] No sample directories found, skipping.")
            continue

        print(f"\n[{batch_name}] {len(samples)} samples")
        print("-" * 70)

        for sample_id in samples:
            sample_dir = batch_dir / sample_id
            total += 1

            status, note = upload_sample(
                sample_id, sample_dir, file_type_keys, token, args.dry_run
            )

            if status == "OK":
                ok += 1
                print(f"  ✓ {sample_id}  {note}")
            elif status == "DRY":
                ok += 1
                print(f"  ~ {sample_id}  {note}")
            elif status == "SKIP":
                skipped += 1
                print(f"  – {sample_id}  SKIP  {note}")
            elif status == "AUTH_ERROR":
                auth_errors += 1
                errors += 1
                print(f"  ✗ {sample_id}  AUTH ERROR  {note}")
                print("\n  → Token may be expired. Get a fresh token and re-run.")
                print("=" * 70)
                print(f"STOPPED: auth error on {sample_id}")
                print(f"Processed {total} samples before stopping.")
                sys.exit(1)
            else:
                errors += 1
                print(f"  ✗ {sample_id}  ERROR  {note}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total samples : {total}")
    print(f"  ✓ Uploaded  : {ok}")
    print(f"  – Skipped   : {skipped}")
    print(f"  ✗ Errors    : {errors}")
    print("=" * 70)


if __name__ == "__main__":
    main()
