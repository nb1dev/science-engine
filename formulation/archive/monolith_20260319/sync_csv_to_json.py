#!/usr/bin/env python3
"""
CSV → JSON Sync Script for Formulation Knowledge Base

Reads source CSVs from documents/supplement_module/final/ and generates
consolidated, validated JSON files in knowledge_base/.

Usage:
    python sync_csv_to_json.py                    # Sync all
    python sync_csv_to_json.py --check             # Dry run, report differences only
    python sync_csv_to_json.py --vitamins          # Sync vitamins only
    python sync_csv_to_json.py --supplements       # Sync supplements only
    python sync_csv_to_json.py --strains           # Sync strains only

Source CSVs (read from original location, NOT copied):
    documents/supplement_module/final/vitamins_minerals.csv
    documents/supplement_module/final/supplements_nonvitamins.csv
    documents/supplement_module/final/list_of_bacterial_strains.csv

Output JSONs:
    knowledge_base/vitamins_minerals.json
    knowledge_base/supplements_nonvitamins.json
    knowledge_base/bacterial_strains.json

Note: synbiotic_mixes.json is NOT auto-synced from CSV because it contains
rich clinical data (triggers, mechanisms, prebiotics) that goes beyond the CSV.
It is maintained manually and versioned in knowledge_base/.
"""

import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Paths
SCRIPT_DIR = Path(__file__).parent
WORK_DIR = SCRIPT_DIR.parent.parent  # /Users/pnovikova/Documents/work
CSV_SOURCE_DIR = WORK_DIR / "documents" / "supplement_module" / "final"
KB_OUTPUT_DIR = SCRIPT_DIR / "knowledge_base"

# Known typo corrections (applied during sync)
TYPO_CORRECTIONS = {
    "Maganese": "Manganese",
    "3rd Choide": "3rd Choice",
}


def apply_typo_corrections(text) -> str:
    """Apply known typo corrections to text."""
    if not text or not isinstance(text, str):
        return text if isinstance(text, str) else str(text) if text else ""
    for wrong, correct in TYPO_CORRECTIONS.items():
        text = text.replace(wrong, correct)
    return text


def parse_dose_value(dose_str: str) -> dict:
    """Parse a dose string into structured data."""
    if not dose_str:
        return None

    result = {}

    # Handle sex-dependent doses like "11mg/d for men and 8mg/d for women"
    sex_match = re.match(r'(\d+\.?\d*)\s*(\w+)/d\s+for\s+men\s+and\s+(\d+\.?\d*)\s*(\w+)/d\s+for\s+women', dose_str)
    if sex_match:
        return {
            "male": float(sex_match.group(1)),
            "female": float(sex_match.group(3)),
            "unit": sex_match.group(2),
            "frequency": "daily",
            "raw": dose_str
        }

    # Handle form prefix like "Nicotinamide: 160 mg/d"
    form_match = re.match(r'(\w+):\s*(.+)', dose_str)
    if form_match:
        result["form"] = form_match.group(1)
        dose_str = form_match.group(2)

    # Handle range doses like "300mg-600mg" or "300-600 mg"
    range_match = re.match(r'(\d+\.?\d*)\s*(\w+)?\s*[-–]\s*(\d+\.?\d*)\s*(\w+)?', dose_str)
    if range_match:
        result["min"] = float(range_match.group(1))
        result["max"] = float(range_match.group(3))
        result["unit"] = range_match.group(2) or range_match.group(4) or ""
        result["raw"] = dose_str
        return result

    # Handle simple dose like "250 mg/d" or "20 mcg/d"
    simple_match = re.match(r'(\d+\.?\d*)\s*(\w+)', dose_str)
    if simple_match:
        result["value"] = float(simple_match.group(1))
        result["unit"] = simple_match.group(2).replace("/d", "").replace("/day", "")
        result["raw"] = dose_str
        return result

    # Handle complex text doses
    return {"raw": dose_str}


def parse_health_claims(subcategory: str) -> list:
    """Parse semicolon-separated health claims."""
    if not subcategory:
        return []
    return [claim.strip() for claim in subcategory.split(";") if claim.strip()]


def classify_interaction_level(risk_text: str) -> str:
    """Classify interaction risk level from text."""
    if not risk_text:
        return "unknown"
    risk_lower = risk_text.lower()
    if "contraindicated" in risk_lower:
        return "contraindicated"
    if "avoid" in risk_lower:
        return "high"
    if "caution" in risk_lower:
        return "medium"
    if "none" in risk_lower:
        return "none"
    if "very low" in risk_lower:
        return "very_low"
    if "low" in risk_lower:
        return "low"
    if "minimal" in risk_lower:
        return "minimal"
    if "medium" in risk_lower or "moderate" in risk_lower or "med" in risk_lower:
        return "medium"
    return "low"


def make_id(substance: str) -> str:
    """Generate a clean ID from substance name."""
    # Remove parenthetical content
    clean = re.sub(r'\([^)]*\)', '', substance)
    # Remove special chars, lowercase, replace spaces with underscores
    clean = re.sub(r'[^a-zA-Z0-9\s]', '', clean).strip().lower()
    clean = re.sub(r'\s+', '_', clean)
    return clean


def parse_rank(rank_str: str) -> int:
    """Parse rank string to integer."""
    if not rank_str:
        return 99
    rank_str = apply_typo_corrections(rank_str)
    match = re.match(r'(\d+)', rank_str)
    if match:
        return int(match.group(1))
    return 99


# ─── VITAMINS & MINERALS ─────────────────────────────────────────────────────

# Global safety principles (from vitamins_minerals_production.json — unique data)
GLOBAL_SAFETY_PRINCIPLES = {
    "spacing_rules": {
        "mineral_mineral_spacing_hours": 2,
        "mineral_antibiotic_spacing_hours_range": [2, 6],
        "rationale": "Minerals compete for absorption transporters; spacing prevents competitive inhibition"
    },
    "timing_optimization": {
        "fat_soluble_vitamins": {
            "vitamins": ["A", "D", "E", "K"],
            "requirement": "Take with fat-containing meals for optimal absorption"
        },
        "water_soluble_vitamins": {
            "vitamins": ["B-complex", "C"],
            "requirement": "Flexible timing; some better on empty stomach (B9, B12)"
        },
        "minerals": "Generally with food to reduce GI upset; exceptions noted per nutrient"
    },
    "key_synergies": {
        "b_vitamin_complex": "B vitamins work synergistically; best taken together",
        "calcium_vitamin_d": "Vitamin D required for calcium absorption",
        "iron_vitamin_c": "Vitamin C enhances iron absorption by 3-4x",
        "copper_zinc_ratio": "Maintain 1:8-15 ratio to prevent competitive inhibition"
    }
}


def sync_vitamins_minerals(check_only: bool = False) -> dict:
    """Sync vitamins_minerals.csv → vitamins_minerals.json"""
    csv_path = CSV_SOURCE_DIR / "vitamins_minerals.csv"
    json_path = KB_OUTPUT_DIR / "vitamins_minerals.json"

    if not csv_path.exists():
        print(f"  ❌ Source CSV not found: {csv_path}")
        return {"status": "error", "message": "CSV not found"}

    entries = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Apply typo corrections to all fields
            row = {k: apply_typo_corrections(v) for k, v in row.items()}

            substance = row.get("Substance", "").strip()
            if not substance:
                continue

            entry = {
                "id": make_id(substance),
                "substance": substance,
                "supplement_type": row.get("Supplement Type", "").strip(),
                "subcategory": row.get("Subcategory", "").strip(),
                "max_intake_in_supplements": row.get("Max Intake in Supplements", "").strip(),
                "rationale": row.get("Rationale", "").strip(),
                "interaction_risk": row.get("Interaction Risk", "").strip(),
                "timing_in_protocol": row.get("Timing in Protocol", "").strip(),
                "notes": row.get("Notes", "").strip(),
                "parsed": {
                    "dose": parse_dose_value(row.get("Max Intake in Supplements", "").strip()),
                    "health_claims": parse_health_claims(row.get("Subcategory", "").strip()),
                    "interaction_level": classify_interaction_level(row.get("Interaction Risk", "").strip()),
                }
            }
            entries.append(entry)

    output = {
        "metadata": {
            "version": "1.1.0",
            "source_csv": str(csv_path.relative_to(WORK_DIR)),
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "record_count": len(entries),
            "typo_corrections_applied": list(TYPO_CORRECTIONS.keys()),
            "description": "Vitamins & minerals reference — doses, interactions, timing. Source of truth: CSV."
        },
        "global_safety_principles": GLOBAL_SAFETY_PRINCIPLES,
        "vitamins_and_minerals": entries
    }

    if check_only:
        return _compare_with_existing(json_path, output, "vitamins_minerals")

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"  ✅ vitamins_minerals.csv → vitamins_minerals.json ({len(entries)} entries)")
    return {"status": "ok", "count": len(entries)}


# ─── SUPPLEMENTS (NON-VITAMINS) ──────────────────────────────────────────────

def sync_supplements_nonvitamins(check_only: bool = False) -> dict:
    """Sync supplements_nonvitamins.csv → supplements_nonvitamins.json

    Produces BOTH a flat list AND a health-category-grouped view.
    """
    csv_path = CSV_SOURCE_DIR / "supplements_nonvitamins.csv"
    json_path = KB_OUTPUT_DIR / "supplements_nonvitamins.json"

    if not csv_path.exists():
        print(f"  ❌ Source CSV not found: {csv_path}")
        return {"status": "error", "message": "CSV not found"}

    entries = []
    categories = {}

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            row = {k: apply_typo_corrections(v) for k, v in row.items()}

            substance = row.get("Substance", "").strip()
            if not substance:
                continue

            subcategory = row.get("Subcategory", "").strip()
            rank_str = row.get("Rank", "").strip()

            entry = {
                "id": make_id(substance),
                "substance": substance,
                "supplement_type": row.get("Supplement Type", "").strip(),
                "subcategory": subcategory,
                "rank": rank_str,
                "standard_dose": row.get("Standard Dose", "").strip(),
                "rationale": row.get("Rationale", "").strip(),
                "interaction_risk": row.get("Interaction Risk", "").strip(),
                "timing_in_protocol": row.get("Timing in Protocol", "").strip(),
                "notes": row.get("Notes", "").strip(),
                "parsed": {
                    "dose": parse_dose_value(row.get("Standard Dose", "").strip()),
                    "rank_priority": parse_rank(rank_str),
                    "interaction_level": classify_interaction_level(row.get("Interaction Risk", "").strip()),
                    "health_claims": [subcategory] if subcategory else [],
                }
            }
            entries.append(entry)

            # Build category grouping
            if subcategory not in categories:
                categories[subcategory] = []
            categories[subcategory].append(entry)

    # Sort within each category by rank
    for cat in categories:
        categories[cat].sort(key=lambda x: x["parsed"]["rank_priority"])

    # Build grouped view
    health_categories = []
    for cat_name in sorted(categories.keys()):
        health_categories.append({
            "category": cat_name,
            "supplement_count": len(categories[cat_name]),
            "supplements": categories[cat_name]
        })

    output = {
        "metadata": {
            "version": "1.1.0",
            "source_csv": str(csv_path.relative_to(WORK_DIR)),
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "record_count": len(entries),
            "category_count": len(categories),
            "typo_corrections_applied": list(TYPO_CORRECTIONS.keys()),
            "description": "Non-vitamin supplements — botanicals, amino acids, adaptogens, fibers, omegas. Includes both flat list and health-category grouping."
        },
        "supplements_flat": entries,
        "health_categories": health_categories
    }

    if check_only:
        return _compare_with_existing(json_path, output, "supplements_nonvitamins")

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"  ✅ supplements_nonvitamins.csv → supplements_nonvitamins.json ({len(entries)} entries, {len(categories)} categories)")
    return {"status": "ok", "count": len(entries), "categories": len(categories)}


# ─── BACTERIAL STRAINS ────────────────────────────────────────────────────────

def sync_bacterial_strains(check_only: bool = False) -> dict:
    """Sync list_of_bacterial_strains.csv → bacterial_strains.json"""
    csv_path = CSV_SOURCE_DIR / "list_of_bacterial_strains.csv"
    json_path = KB_OUTPUT_DIR / "bacterial_strains.json"

    if not csv_path.exists():
        print(f"  ❌ Source CSV not found: {csv_path}")
        return {"status": "error", "message": "CSV not found"}

    entries = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            row = {k: apply_typo_corrections(v) for k, v in row.items()}
            # Keep all columns dynamically
            entry = {k.strip().lower().replace(" ", "_"): v.strip() for k, v in row.items() if v.strip()}
            if entry:
                entries.append(entry)

    output = {
        "metadata": {
            "version": "1.0.0",
            "source_csv": str(csv_path.relative_to(WORK_DIR)),
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "record_count": len(entries),
            "description": "Master list of available bacterial strains for probiotic formulations."
        },
        "strains": entries
    }

    if check_only:
        return _compare_with_existing(json_path, output, "bacterial_strains")

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"  ✅ list_of_bacterial_strains.csv → bacterial_strains.json ({len(entries)} entries)")
    return {"status": "ok", "count": len(entries)}


# ─── COMPARISON UTILITY ───────────────────────────────────────────────────────

def _compare_with_existing(json_path: Path, new_data: dict, name: str) -> dict:
    """Compare new generated data with existing JSON file."""
    if not json_path.exists():
        print(f"  📄 {name}: No existing JSON — would create new ({new_data['metadata']['record_count']} entries)")
        return {"status": "new", "count": new_data['metadata']['record_count']}

    with open(json_path, 'r', encoding='utf-8') as f:
        existing = json.load(f)

    old_count = existing.get("metadata", {}).get("record_count", "?")
    new_count = new_data["metadata"]["record_count"]

    if old_count == new_count:
        print(f"  ✅ {name}: {new_count} entries (no count change)")
    else:
        print(f"  ⚠️  {name}: {old_count} → {new_count} entries (CHANGED)")

    return {"status": "check_complete", "old_count": old_count, "new_count": new_count}


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Sync CSVs to knowledge base JSONs")
    parser.add_argument("--check", action="store_true", help="Dry run — report differences only")
    parser.add_argument("--vitamins", action="store_true", help="Sync vitamins only")
    parser.add_argument("--supplements", action="store_true", help="Sync supplements only")
    parser.add_argument("--strains", action="store_true", help="Sync strains only")
    args = parser.parse_args()

    sync_all = not (args.vitamins or args.supplements or args.strains)

    print(f"\n{'='*60}")
    print(f"  Formulation Knowledge Base Sync")
    print(f"  Source: {CSV_SOURCE_DIR}")
    print(f"  Output: {KB_OUTPUT_DIR}")
    print(f"  Mode:   {'CHECK (dry run)' if args.check else 'SYNC (write files)'}")
    print(f"{'='*60}\n")

    results = {}

    if sync_all or args.vitamins:
        results["vitamins_minerals"] = sync_vitamins_minerals(check_only=args.check)

    if sync_all or args.supplements:
        results["supplements_nonvitamins"] = sync_supplements_nonvitamins(check_only=args.check)

    if sync_all or args.strains:
        results["bacterial_strains"] = sync_bacterial_strains(check_only=args.check)

    print(f"\n{'='*60}")
    print(f"  Sync complete.")
    if not args.check:
        print(f"  Knowledge base JSONs written to: {KB_OUTPUT_DIR}")
    print(f"{'='*60}\n")

    return results


if __name__ == "__main__":
    main()
