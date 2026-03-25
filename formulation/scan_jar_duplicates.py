#!/usr/bin/env python3
"""Scan all formulation masters for jar-vs-capsule and jar-internal duplicates."""

import json
import re
import os
import glob

master_files = sorted(glob.glob('analysis/*/*/reports/reports_json/formulation_master_*.json'))
print(f'Deep scanning {len(master_files)} formulation masters...\n')


def normalize(name):
    return re.sub(r'\s*\(.*?\)\s*', '', name).strip().lower()


issues = []

for mf in master_files:
    try:
        with open(mf) as f:
            d = json.load(f)
    except Exception:
        continue

    sample = os.path.basename(mf).replace('formulation_master_', '').replace('.json', '')
    batch = mf.split('/')[1]
    formulation = d.get('formulation') or {}

    # ── Collect JAR substances ──
    jar = formulation.get('delivery_format_3_powder_jar') or {}
    jar_substances = {}

    for p in (jar.get('prebiotics') or {}).get('components', []):
        name = normalize(p.get('substance', ''))
        if name:
            jar_substances.setdefault(name, []).append({
                'original': p.get('substance'), 'dose': f"{p.get('dose_g', 0)}g",
                'category': p.get('type', 'prebiotic'), 'pool': 'jar_prebiotics'
            })

    for b in (jar.get('botanicals') or {}).get('components', []):
        name = normalize(b.get('substance', ''))
        if name:
            jar_substances.setdefault(name, []).append({
                'original': b.get('substance'), 'dose': f"{b.get('dose_g', 0)}g",
                'category': b.get('type', 'botanical'), 'pool': 'jar_botanicals'
            })

    for s in (jar.get('supplements') or {}).get('components', []):
        name = normalize(s.get('substance', ''))
        if name:
            jar_substances.setdefault(name, []).append({
                'original': s.get('substance'), 'dose': f"{s.get('dose_mg', s.get('weight_mg', 0))}mg",
                'category': 'supplement', 'pool': 'jar_supplements'
            })

    # ── Collect CAPSULE substances ──
    capsule_substances = {}

    # Morning wellness capsules
    mwc = formulation.get('delivery_format_4_morning_wellness_capsules') or {}
    for c in mwc.get('components', []):
        name = normalize(c.get('substance', ''))
        if name:
            capsule_substances.setdefault(name, []).append({
                'original': c.get('substance'),
                'dose': c.get('dose', f"{c.get('dose_mg', 0)}mg"),
                'unit': 'Morning Wellness Capsule'
            })

    # Evening wellness capsules
    ewc = formulation.get('delivery_format_5_evening_wellness_capsules') or {}
    for c in ewc.get('components', []):
        name = normalize(c.get('substance', ''))
        if name:
            capsule_substances.setdefault(name, []).append({
                'original': c.get('substance'),
                'dose': f"{c.get('dose_mg', 0)}mg",
                'unit': 'Evening Wellness Capsule'
            })

    # Polyphenol capsules (format 5 or 6)
    pp = formulation.get('delivery_format_6_polyphenol_capsule') or formulation.get('delivery_format_5_polyphenol_capsule') or {}
    for c in pp.get('components', []):
        name = normalize(c.get('substance', ''))
        if name:
            capsule_substances.setdefault(name, []).append({
                'original': c.get('substance'),
                'dose': f"{c.get('dose_mg', 0)}mg",
                'unit': 'Polyphenol Capsule'
            })

    # ── Pattern A: Same substance in jar_prebiotics AND jar_botanicals ──
    for name, entries in jar_substances.items():
        pools = set(e['pool'] for e in entries)
        if len(pools) > 1:
            issues.append({
                'batch': batch, 'sample': sample, 'substance': name,
                'pattern': 'A: jar_prebiotics + jar_botanicals',
                'details': entries,
            })

    # ── Pattern B: Same substance in jar AND any capsule ──
    for name in jar_substances:
        if name in capsule_substances:
            issues.append({
                'batch': batch, 'sample': sample, 'substance': name,
                'pattern': 'B: jar + capsule',
                'jar': jar_substances[name],
                'capsule': capsule_substances[name],
            })

    # ── Pattern C: Same substance duplicated WITHIN a single jar pool ──
    for name, entries in jar_substances.items():
        by_pool = {}
        for e in entries:
            by_pool.setdefault(e['pool'], []).append(e)
        for pool, pool_entries in by_pool.items():
            if len(pool_entries) > 1:
                issues.append({
                    'batch': batch, 'sample': sample, 'substance': name,
                    'pattern': f'C: duplicate within {pool}',
                    'details': pool_entries,
                })

# ── Print results ──
if not issues:
    print('✅ No jar-vs-capsule or jar-internal duplicates found in any sample.')
else:
    # Deduplicate
    seen = set()
    unique_issues = []
    for i in issues:
        key = (i['batch'], i['sample'], i['substance'], i['pattern'])
        if key not in seen:
            seen.add(key)
            unique_issues.append(i)

    # Group by sample
    by_sample = {}
    for i in unique_issues:
        key = f"{i['batch']}/{i['sample']}"
        by_sample.setdefault(key, []).append(i)

    print(f'🚨 Found {len(unique_issues)} issue(s) across {len(by_sample)} sample(s):\n')

    for sample_key in sorted(by_sample.keys()):
        sample_issues = by_sample[sample_key]
        print(f'══ {sample_key} ══  [NEEDS RERUN]')
        for si in sample_issues:
            print(f'  Pattern: {si["pattern"]}')
            print(f'  Substance: {si["substance"]}')
            if 'jar' in si and 'capsule' in si:
                for j in si['jar']:
                    print(f'    JAR: {j["original"]} {j["dose"]} ({j["pool"]})')
                for c in si['capsule']:
                    print(f'    CAPSULE: {c["original"]} {c["dose"]} ({c["unit"]})')
            elif 'details' in si:
                for dd in si['details']:
                    print(f'    → {dd["original"]} {dd["dose"]} ({dd.get("pool", "?")})')
            print()

    print('────────────────────────────────────────────')
    print(f'SUMMARY: {len(by_sample)} sample(s) need re-run:')
    for sk in sorted(by_sample.keys()):
        substances = list(set(i['substance'] for i in by_sample[sk]))
        patterns = list(set(i['pattern'] for i in by_sample[sk]))
        print(f'  {sk}: {substances} ({", ".join(patterns)})')
