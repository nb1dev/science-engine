#!/usr/bin/env python3
"""
Distribute questionnaires from a response JSON file to individual sample directories.
Tracks changes and automatically updates the sample tracking CSV.

Usage:
    # Fetch from API and distribute (recommended):
    python science-engine/pipeline/distribute_questionnaires.py --token "eyJhbGciOi..."

    # Use a previously downloaded JSON file:
    python science-engine/pipeline/distribute_questionnaires.py response_1770214891248.json

The script will:
1. (If --token) Fetch all users with questionnaires from the API (paginated)
2. (If --token) Save the response to data/questionnaire/response_TIMESTAMP.json
3. Load the JSON file from data/questionnaire/
4. Extract users with kit_code and questionnaire data
5. Find matching sample directories across all batches
6. Track changes (new, updated, unchanged questionnaires)
7. Write questionnaire JSON to each sample's questionnaire folder
8. Update the sample_tracking_master.csv automatically
9. Show detailed change report
"""

import json
import os
import sys
import csv
import argparse
import ssl
import time
from pathlib import Path
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# Ensure we can find the work directory from this script location
SCRIPT_DIR = Path(__file__).parent
WORK_DIR = SCRIPT_DIR.parent.parent  # science-engine/pipeline -> science-engine -> work

# Default API token — no need to pass --token on the command line
DEFAULT_TOKEN = (
    "eyJhbGciOiJSUzI1NiIsImtpZCI6IjczMmNhOTY3MTNiMWRkMTcyMzg1MDg0Y2U5ZjQzODFhZDAwY2VjZ"
    "TQiLCJ0eXAiOiJKV1QifQ.eyJuYW1lIjoiUG9saW5hIE5vdmlrb3ZhIiwicGljdHVyZSI6Imh0dHBzOi8v"
    "bGgzLmdvb2dsZXVzZXJjb250ZW50LmNvbS9hL0FDZzhvY0tsSVlPTEdQQUYtdExoRjBWWDBqUUZXcm15Q3"
    "FjRVNKMUtCOFh0LVFtcnlTT0Jxd1BzVFE9czk2LWMiLCJpc3MiOiJodHRwczovL3NlY3VyZXRva2VuLmdv"
    "b2dsZS5jb20vbmIxLWhlYWx0aC1kOTcyZCIsImF1ZCI6Im5iMS1oZWFsdGgtZDk3MmQiLCJhdXRoX3RpbWU"
    "iOjE3NzM4MjM2NTUsInVzZXJfaWQiOiJGdE9WS1Nib1IyV3hiUUdPcTBKUjNrUGlvamwyIiwic3ViIjoiRn"
    "RPVktTYm9SMld4YlFHT3EwSlIza1Bpb2psMiIsImlhdCI6MTc3NDI2MDEyNywiZXhwIjoxNzc0MjYzNzI3LC"
    "JlbWFpbCI6Im5vZmYucG9saW5hQGdtYWlsLmNvbSIsImVtYWlsX3ZlcmlmaWVkIjp0cnVlLCJmaXJlYmFzZ"
    "SI6eyJpZGVudGl0aWVzIjp7Imdvb2dsZS5jb20iOlsiMTA3NjU2MDI2MzMxNTk3NTUxNjcwIl0sImVtYWls"
    "IjpbIm5vZmYucG9saW5hQGdtYWlsLmNvbSJdfSwic2lnbl9pbl9wcm92aWRlciI6Imdvb2dsZS5jb20ifX0"
    ".uZ1aXld5XOYATjGSJ1K-ARx3wSrnvQ45hO5Q5pr0AVCgNKvaTjTsNuy-l77Ubzqp8Ocfksx0ZHWD3tF1LkF"
    "C2SYJ0fZ7FOcmlDqH4bysFoaAeGimeDema3WEPyedMv9K8dlxdn5Igom2UokuhMM3dr14KPoKE1cnT_e7Eg70"
    "N2kPR8PZTgmTZIaPEsD7F_A8vEhXbrpDsRdrluam_l1es6OUDr12KYzUD0I5VTzuOJpH22XrfCiAdX6w8mqL"
    "L7xEOeKPCXs8Pfj89yCaag1lPQULjRE3ZrtsqNV0FshWaT61OO1KI4co_cn_fyItl9bL1iYOSjyxIQ0sJWr0"
    "Hx_u2A"
)


def calculate_completion_percentage(questionnaire):
    """
    Calculate questionnaire completion percentage based on filled data.
    
    Args:
        questionnaire: Full questionnaire dictionary (not just questionnaire_data)
        
    Returns:
        Integer percentage (0-100)
    """
    if not questionnaire:
        return 0
    
    # Check if this is the full questionnaire or just questionnaire_data
    if "questionnaire_data" in questionnaire:
        questionnaire_data = questionnaire["questionnaire_data"]
        completed_steps = questionnaire.get("completed_steps", [])
    else:
        questionnaire_data = questionnaire
        completed_steps = questionnaire.get("completed_steps", [])
    
    if not completed_steps:
        return 0
    
    total_steps = 9
    
    # Basic percentage from completed steps
    step_percentage = (len(completed_steps) / total_steps) * 100
    
    # Check data quality - are the steps actually filled?
    filled_quality = 0
    for step_num in completed_steps:
        step_key = f"step_{step_num}"
        if step_key in questionnaire_data:
            step_data = questionnaire_data[step_key]
            if has_meaningful_data(step_data):
                filled_quality += 1
    
    # Average of step count and data quality
    quality_percentage = (filled_quality / total_steps) * 100
    final_percentage = int((step_percentage + quality_percentage) / 2)
    
    return min(final_percentage, 100)


def has_meaningful_data(step_data):
    """Check if a step has meaningful data (not just empty fields)."""
    if not isinstance(step_data, dict):
        return False
    
    # Count non-empty values
    filled_count = 0
    total_count = 0
    
    for key, value in step_data.items():
        if isinstance(value, dict):
            # Recursively check nested dicts
            if has_meaningful_data(value):
                filled_count += 1
            total_count += 1
        elif isinstance(value, list):
            if len(value) > 0:
                filled_count += 1
            total_count += 1
        elif value not in [None, "", [], {}]:
            filled_count += 1
            total_count += 1
        else:
            total_count += 1
    
    # Consider meaningful if >30% of fields are filled
    if total_count == 0:
        return False
    return (filled_count / total_count) > 0.3


def find_sample_directory(kit_code):
    """
    Search for a sample directory with the given kit_code across all batch directories.
    
    Args:
        kit_code: The kit code to search for (e.g., "1421263814738")
        
    Returns:
        Path to the sample directory if found, None otherwise
    """
    base = WORK_DIR / "analysis"
    
    # Search all nb1_2026_* batch directories
    for batch_dir in sorted(base.glob("nb1_2026_*")):
        if batch_dir.is_dir():
            sample_dir = batch_dir / kit_code
            if sample_dir.exists() and sample_dir.is_dir():
                return sample_dir
    
    return None


def load_existing_questionnaire(questionnaire_dir, kit_code):
    """Load existing questionnaire if it exists."""
    json_path = questionnaire_dir / f"questionnaire_{kit_code}.json"
    if json_path.exists():
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return None
    return None


def extract_questionnaire_data(user):
    """
    Extract relevant questionnaire data from a user record.
    
    Args:
        user: User dictionary from the JSON
        
    Returns:
        Dictionary with questionnaire data
    """
    return {
        "kit_code": user.get("kit_code"),
        "user_id": user.get("id"),
        "email": user.get("email"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "phone": user.get("phone"),
        "questionnaire_id": user["questionnaire"].get("id"),
        "biological_sex": user["questionnaire"].get("biological_sex"),
        "age": user["questionnaire"].get("age"),
        "questionnaire_data": user["questionnaire"].get("questionnaire_data"),
        "current_step": user["questionnaire"].get("current_step"),
        "completed_steps": user["questionnaire"].get("completed_steps"),
        "is_completed": user["questionnaire"].get("is_completed"),
        "created_at": user["questionnaire"].get("created_at"),
        "updated_at": user["questionnaire"].get("updated_at"),
        "kit_status": {
            "kit_sent": user.get("kit_sent"),
            "kit_received_by_user": user.get("kit_received_by_user"),
            "kit_received_by_lab": user.get("kit_received_by_lab"),
            "analysis_in_progress": user.get("analysis_in_progress"),
            "report_in_preparation": user.get("report_in_preparation"),
            "report_ready": user.get("report_ready")
        }
    }


def load_tracking_csv():
    """Load the sample tracking CSV."""
    csv_path = WORK_DIR / "work_tracking" / "sample_tracking_master.csv"
    tracking_data = {}
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Strip spaces from all keys and values
                cleaned_row = {k.strip(): v.strip() for k, v in row.items()}
                sample_id = cleaned_row.get('Sample_ID', '')
                if sample_id:
                    tracking_data[sample_id] = cleaned_row
    except Exception as e:
        print(f"Warning: Could not load tracking CSV: {e}")
    return tracking_data


def update_tracking_csv(tracking_data, updates):
    """Update the tracking CSV with new questionnaire data."""
    csv_path = WORK_DIR / "work_tracking" / "sample_tracking_master.csv"
    try:
        # Apply updates
        for kit_code, update_info in updates.items():
            if kit_code in tracking_data:
                tracking_data[kit_code]['Questionnaire'] = update_info['status']
                tracking_data[kit_code]['Last_Updated'] = update_info['date']
                
                # Update notes
                current_notes = tracking_data[kit_code].get('Notes', '').strip()
                if 'Q JSON updated' not in current_notes and 'Q updated' not in current_notes:
                    if current_notes:
                        tracking_data[kit_code]['Notes'] = f"{current_notes} - Q updated"
                    else:
                        tracking_data[kit_code]['Notes'] = "Q updated"
        
        # Write back to CSV
        if tracking_data:
            fieldnames = list(next(iter(tracking_data.values())).keys())
            with open(csv_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for row in tracking_data.values():
                    writer.writerow(row)
            return True
    except Exception as e:
        print(f"Warning: Could not update tracking CSV: {e}")
        return False
    return False


def process_questionnaires(json_filename):
    """
    Process questionnaires from the JSON file and distribute them to sample directories.
    
    Args:
        json_filename: Name of the JSON file (e.g., "response_1770214891248.json")
    """
    # Construct full path to JSON file
    json_path = WORK_DIR / "data" / "questionnaire" / json_filename
    
    if not json_path.exists():
        print(f"Error: File not found: {json_path}")
        sys.exit(1)
    
    print(f"Loading questionnaire data from: {json_path}")
    print("=" * 80)
    
    # Load JSON data
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading JSON: {e}")
        sys.exit(1)
    
    users = data.get("users", [])
    print(f"Total users in JSON: {len(users)}")
    
    # Filter users with kit_code and questionnaire
    valid_users = [
        u for u in users 
        if u.get("kit_code") and u.get("questionnaire") is not None
    ]
    
    print(f"Users with kit_code and questionnaire: {len(valid_users)}")
    
    # Load tracking CSV
    tracking_data = load_tracking_csv()
    print(f"Loaded tracking data for {len(tracking_data)} samples")
    print("=" * 80)
    
    # Track changes
    changes = {
        'new': [],
        'updated': [],
        'unchanged': [],
        'not_found': []
    }
    
    csv_updates = {}
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    # Process each user
    for user in valid_users:
        kit_code = user["kit_code"]
        email = user.get("email", "unknown")
        first_name = user.get("first_name", "")
        last_name = user.get("last_name", "")
        full_name = f"{first_name} {last_name}".strip() or "Unknown"
        
        # Find sample directory
        sample_dir = find_sample_directory(kit_code)
        
        if sample_dir is None:
            changes['not_found'].append({
                'kit_code': kit_code,
                'name': full_name,
                'email': email
            })
            continue
        
        # Create questionnaire directory if it doesn't exist
        questionnaire_dir = sample_dir / "questionnaire"
        questionnaire_dir.mkdir(exist_ok=True)
        
        # Load existing questionnaire
        existing = load_existing_questionnaire(questionnaire_dir, kit_code)
        
        # Extract new questionnaire data
        new_data = extract_questionnaire_data(user)
        
        # Calculate completion percentages
        new_completion = calculate_completion_percentage(new_data)
        old_completion = 0
        
        if existing:
            old_completion = calculate_completion_percentage(existing)
        
        # Determine change type
        batch = sample_dir.parent.name
        change_info = {
            'kit_code': kit_code,
            'name': full_name,
            'email': email,
            'batch': batch,
            'old_completion': old_completion,
            'new_completion': new_completion,
            'old_updated': existing.get('updated_at', '') if existing else '',
            'new_updated': new_data.get('updated_at', '')
        }
        
        if old_completion == 0:
            changes['new'].append(change_info)
            status = f"Incomplete ({new_completion}%)" if new_completion < 100 else "Complete"
        elif new_completion != old_completion:
            changes['updated'].append(change_info)
            status = f"Incomplete ({new_completion}%)" if new_completion < 100 else "Complete"
        else:
            changes['unchanged'].append(change_info)
            status = f"Incomplete ({new_completion}%)" if new_completion < 100 else "Complete"
        
        # Track CSV update (only if there's a change)
        if kit_code in tracking_data and (old_completion != new_completion or old_completion == 0):
            csv_updates[kit_code] = {
                'status': status,
                'date': current_date
            }
        
        # Write questionnaire JSON
        output_filename = f"questionnaire_{kit_code}.json"
        output_path = questionnaire_dir / output_filename
        
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(new_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"✗ Error writing {kit_code}: {e}")
            continue
    
    # Update tracking CSV
    csv_updated = False
    if csv_updates:
        csv_updated = update_tracking_csv(tracking_data, csv_updates)
    
    # Print detailed change report
    print("\n" + "=" * 80)
    print("QUESTIONNAIRE CHANGES DETECTED")
    print("=" * 80)
    
    if changes['new']:
        print(f"\n✓ NEW QUESTIONNAIRES ({len(changes['new'])})")
        print("-" * 80)
        for c in changes['new']:
            print(f"  Kit {c['kit_code']} - {c['name']} ({c['email']})")
            print(f"  └─ {c['batch']}: 0% → {c['new_completion']}% (NEW)")
    
    if changes['updated']:
        print(f"\n↑ UPDATED QUESTIONNAIRES ({len(changes['updated'])})")
        print("-" * 80)
        for c in changes['updated']:
            print(f"  Kit {c['kit_code']} - {c['name']} ({c['email']})")
            print(f"  └─ {c['batch']}: {c['old_completion']}% → {c['new_completion']}% (PROGRESS)")
    
    if changes['unchanged']:
        print(f"\n→ UNCHANGED QUESTIONNAIRES ({len(changes['unchanged'])})")
        print("-" * 80)
        for c in changes['unchanged']:
            print(f"  Kit {c['kit_code']} - {c['name']} ({c['email']})")
            print(f"  └─ {c['batch']}: {c['new_completion']}% (no change)")
    
    if changes['not_found']:
        print(f"\n⚠️  NOT FOUND ({len(changes['not_found'])})")
        print("-" * 80)
        for c in changes['not_found']:
            print(f"  Kit {c['kit_code']} - {c['name']} ({c['email']})")
            print(f"  └─ Directory not found in analysis folders")
    
    # CSV update summary
    print("\n" + "=" * 80)
    print("TRACKING CSV UPDATES")
    print("=" * 80)
    if csv_updated:
        print(f"✓ Updated {len(csv_updates)} sample entries in tracking CSV")
        print(f"  Updated fields: Questionnaire, Last_Updated, Notes")
    else:
        print("⚠️  No CSV updates performed")
    
    # Final summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total questionnaires processed: {len(valid_users)}")
    print(f"  ✓ New: {len(changes['new'])}")
    print(f"  ↑ Updated: {len(changes['updated'])}")
    print(f"  → Unchanged: {len(changes['unchanged'])}")
    print(f"  ⚠️ Not found: {len(changes['not_found'])}")
    print(f"\nTracking CSV: {'Updated' if csv_updated else 'Not updated'}")
    print("=" * 80)


def fetch_questionnaires_from_api(token, base_url="https://api.nb1.com"):
    """
    Fetch all users with questionnaires from the API, paginating through all results.
    
    Args:
        token: Bearer token for API authentication
        base_url: API base URL
        
    Returns:
        Path to the saved JSON file (filename only, relative to data/questionnaire/)
    """
    page_size = 100
    skip = 0
    all_users = []
    
    # Allow unverified SSL for internal API if needed
    ctx = ssl.create_default_context()
    
    print(f"Fetching questionnaires from API...")
    print(f"  Base URL: {base_url}")
    print("=" * 80)
    
    while True:
        url = (
            f"{base_url}/admin/users/"
            f"?skip={skip}&limit={page_size}"
            f"&include_questionnaire=true"
            f"&order_by=created_at&order_desc=true"
        )
        
        req = Request(url)
        req.add_header("accept", "application/json")
        req.add_header("Authorization", f"Bearer {token}")
        
        try:
            response = urlopen(req, context=ctx)
            data = json.loads(response.read().decode("utf-8"))
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print(f"Error: API returned HTTP {e.code}")
            print(f"  {body[:500]}")
            if e.code == 401:
                print("\n  → Token may be expired. Get a fresh one and try again.")
            sys.exit(1)
        except URLError as e:
            print(f"Error: Could not reach API: {e.reason}")
            sys.exit(1)
        
        users = data.get("users", [])
        total = data.get("total", 0)
        all_users.extend(users)
        
        print(f"  Fetched {len(all_users)}/{total} users (page skip={skip})")
        
        # Check if we've got all users
        if len(all_users) >= total or len(users) < page_size:
            break
        
        skip += page_size
        time.sleep(0.2)  # Be polite to the API
    
    print(f"\n✓ Fetched {len(all_users)} users total")
    
    # Build the combined response object
    combined = {
        "total": len(all_users),
        "users": all_users
    }
    
    # Save to data/questionnaire/
    timestamp = int(time.time() * 1000)
    filename = f"response_{timestamp}.json"
    output_dir = WORK_DIR / "data" / "questionnaire"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(combined, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Saved API response to: {output_path}")
    print("=" * 80)
    
    return filename


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Fetch and distribute questionnaires to sample directories.",
        epilog=(
            "Examples:\n"
            "  # Fetch from API and distribute:\n"
            "  python science-engine/pipeline/distribute_questionnaires.py --token 'eyJhbGciOi...'\n\n"
            "  # Use previously downloaded JSON file:\n"
            "  python science-engine/pipeline/distribute_questionnaires.py response_1770214891248.json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "json_filename",
        nargs="?",
        default=None,
        help="JSON filename in data/questionnaire/ (e.g. response_1770214891248.json)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Bearer token to fetch questionnaires from the API. "
             "If provided, the script downloads fresh data before distributing.",
    )
    
    args = parser.parse_args()
    
    # Resolve token: CLI arg > DEFAULT_TOKEN
    token = args.token or DEFAULT_TOKEN

    # If a local JSON file was explicitly provided, skip the API fetch
    if args.json_filename:
        json_filename = args.json_filename
    else:
        # Fetch fresh data from the API (using token)
        json_filename = fetch_questionnaires_from_api(token)
    
    process_questionnaires(json_filename)


if __name__ == "__main__":
    main()
