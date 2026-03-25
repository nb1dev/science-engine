#!/usr/bin/env python3
"""
patch_supplement_cards.py — Re-runs build_supplement_cards() on existing interpretations JSONs.

Fixes:
  - Botanicals (Peppermint, Fennel) missing from powder jar card
  - Magnesium Bisglycinate capsule not appearing as a supplement card

Usage:
  cd science-engine
  python report/patch_supplement_cards.py
"""
import json
import os
import sys

# Add report dir to path so we can import build_supplement_cards
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_health_report import build_supplement_cards

SAMPLE_DIRS = [
    '/Users/pnovikova/Documents/work/analysis/nb1_2026_001/1421819436544',
    '/Users/pnovikova/Documents/work/analysis/nb1_2026_006/1421425343541',
    '/Users/pnovikova/Documents/work/analysis/nb1_2026_007/1421773212865',
    '/Users/pnovikova/Documents/work/analysis/nb1_2026_008/1421093249814',
    '/Users/pnovikova/Documents/work/analysis/nb1_2026_009/1421504848853',
]

for sample_dir in SAMPLE_DIRS:
    sample_id = os.path.basename(sample_dir)
    reports_json = os.path.join(sample_dir, 'reports', 'reports_json')

    interp_path = os.path.join(reports_json, f'health_report_interpretations_{sample_id}.json')
    formulation_path = os.path.join(reports_json, f'formulation_master_{sample_id}.json')
    analysis_path = os.path.join(reports_json, f'microbiome_analysis_master_{sample_id}.json')

    if not os.path.exists(interp_path):
        print(f'  SKIP {sample_id} — no interpretations JSON')
        continue
    if not os.path.exists(formulation_path):
        print(f'  SKIP {sample_id} — no formulation master')
        continue

    with open(formulation_path) as f:
        formulation = json.load(f)
    with open(analysis_path) as f:
        analysis = json.load(f)
    with open(interp_path) as f:
        interp = json.load(f)

    old_cards = interp.get('supplement_cards', [])
    new_cards = build_supplement_cards(formulation, analysis)

    # Report what changed
    old_names = [c.get('name', '') for c in old_cards]
    new_names = [c.get('name', '') for c in new_cards]
    added = [n for n in new_names if n not in old_names]
    removed = [n for n in old_names if n not in new_names]

    if added or removed:
        print(f'\n{sample_id}:')
        if added:
            print(f'  + Added: {added}')
        if removed:
            print(f'  - Removed: {removed}')
        # Also report ingredient counts per card
        for card in new_cards:
            print(f'  Card {card["num"]}: {card["name"]} — {len(card.get("pills", []))} ingredients')
    else:
        print(f'{sample_id}: no change in card names')

    # Update and save
    interp['supplement_cards'] = new_cards
    with open(interp_path, 'w') as f:
        json.dump(interp, f, indent=2, ensure_ascii=False)

    print(f'  ✓ Updated: {interp_path}')

print('\nDone. Now run --use-cached to re-render HTMLs.')
