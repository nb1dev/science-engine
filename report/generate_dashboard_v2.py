"""
Generate NB1 microbiome health dashboard from platform JSON(s).

Usage:
    python3 generate_nb1_dashboard.py <platform_json> [output_path]
    python3 generate_nb1_dashboard.py <tp1.json> <tp2.json> <tp3.json> [--output output_path]

If multiple JSONs are provided (one per timepoint), the dashboard will show
the time-slider with all timepoints. The last JSON is treated as the most recent.

The script reads the same _platform.json format as generate_dashboard.py.
"""

import json
import sys
import os
import re
import base64
import argparse
from datetime import datetime

# ── Guild name → our internal key ──────────────────────────────────────────
# Matches against guild_display, name, or guild key (case-insensitive, partial)
GUILD_ALIASES = {
    'fd': ['fiber_degrader', 'fiber degrader', 'fiberdegrader',
           'fiber deg', 'fiberdeg'],
    'bb': ['bifidobacter', 'bifido'],
    'cf': ['cross_feeder', 'cross feeder', 'crossfeeder',
           'cross-feeder', 'crossfeed'],
    'bp': ['butyrate_producer', 'butyrate producer', 'butyrate prod',
           'butyrateprod'],
    'pg': ['proteolytic', 'protein_ferm', 'protein ferm',
           'protein fermenting', 'protein ferment'],
    'md': ['mucin_degrader', 'mucin degrader', 'mucus_layer',
           'mucus layer', 'mucin deg'],
}

def _match_guild_key(name: str) -> str | None:
    """Return our internal key (fd/bb/cf/bp/pg/md) for a guild name, or None."""
    name_l = name.lower().replace('-', '_').replace(' ', '_')
    for key, aliases in GUILD_ALIASES.items():
        for alias in aliases:
            alias_n = alias.replace(' ', '_').replace('-', '_')
            if alias_n in name_l:
                return key
    return None


def _extract_guilds(p: dict) -> dict:
    """
    Extract guild abundances (as fractions 0-1) from a platform JSON.
    Returns dict like {'fd': 0.152, 'bb': 0.103, ...}

    Looks in guild_scenarios first (has abundance_pct), then bacterial_groups_tab.
    """
    guilds = {}

    # ── Primary source: guild_scenarios ──────────────────────────────────────
    for sc in p.get('guild_scenarios', []):
        display = sc.get('guild_display', '') or sc.get('guild', '')
        pct = sc.get('abundance_pct')
        if pct is None:
            continue
        key = _match_guild_key(display)
        if key:
            guilds[key] = float(pct) / 100.0

    # ── Fallback: bacterial_groups_tab → guilds ───────────────────────────
    if len(guilds) < 6:
        bg = p.get('bacterial_groups_tab', {})
        for g in bg.get('guilds', []):
            name = g.get('name', '') or g.get('guild_display', '')
            key = _match_guild_key(name)
            if not key or key in guilds:
                continue
            # Try abundance_pct, then cap actual/optimal ratio, then 0
            pct = g.get('abundance_pct') or g.get('abundance')
            if pct is not None:
                guilds[key] = float(pct) / 100.0
                continue
            cap = g.get('capacity', {})
            actual = cap.get('actual_players')
            optimal = cap.get('optimal_players')
            if actual is not None and optimal:
                guilds[key] = float(actual) / float(optimal) * 0.20  # rough estimate
            elif actual is not None:
                guilds[key] = float(actual) / 100.0

    # ── Fallback: action_plan monitor_guilds ─────────────────────────────
    if len(guilds) < 6:
        ap = p.get('action_plan_tab', {})
        for mg in ap.get('monitor_guilds', []):
            key = _match_guild_key(mg.get('name', ''))
            if key and key not in guilds:
                ab = mg.get('abundance')
                if ab is not None:
                    guilds[key] = float(ab) / 100.0

    # Ensure all six keys exist (default 0 if missing)
    for k in ('fd', 'bb', 'cf', 'bp', 'pg', 'md'):
        guilds.setdefault(k, 0.0)

    return guilds


def _extract_label(p: dict) -> str:
    """Build a human-readable timepoint label from platform JSON metadata."""
    meta = p.get('metadata', {})
    sample_id = meta.get('sample_id', '')
    report_date = meta.get('report_date', '')
    collection_date = meta.get('collection_date', meta.get('sample_date', ''))

    date_str = collection_date or report_date
    if date_str:
        try:
            dt = datetime.strptime(date_str[:10], '%Y-%m-%d')
            date_str = dt.strftime('%b %Y')
        except Exception:
            pass

    if sample_id and date_str:
        return f"{sample_id} — {date_str}"
    elif date_str:
        return f"Test — {date_str}"
    elif sample_id:
        return sample_id
    return "Baseline"


def _load_template() -> str:
    """Load the dashboard HTML template from the same directory as this script."""
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Look for the template file (several possible names)
    candidates = [
        'microbiome-health-report.html',
        'nb1_dashboard_template.html',
        'dashboard_template.html',
    ]
    for name in candidates:
        path = os.path.join(script_dir, name)
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()

    raise FileNotFoundError(
        "Dashboard template not found. Place 'microbiome-health-report.html' "
        "in the same directory as this script."
    )


def generate_nb1_dashboard(json_paths: list[str], output_path: str) -> None:
    """
    Generate the NB1 dashboard HTML.

    Args:
        json_paths: List of _platform.json paths, one per timepoint (oldest first).
        output_path: Where to write the output HTML.
    """
    if not json_paths:
        raise ValueError("At least one platform JSON path is required.")

    # ── Load all timepoints ──────────────────────────────────────────────────
    timepoints = []
    for path in json_paths:
        with open(path, 'r', encoding='utf-8') as f:
            p = json.load(f)
        label = _extract_label(p)
        guilds = _extract_guilds(p)
        timepoints.append({'label': label, 'guilds': guilds, '_raw': p})

    if not timepoints:
        raise ValueError("No valid timepoints found.")

    # ── Load template ────────────────────────────────────────────────────────
    html = _load_template()

    # ── Build JS HR_TPS replacement ──────────────────────────────────────────
    tp_js_items = []
    for tp in timepoints:
        g = tp['guilds']
        guilds_js = (
            f"{{fd:{g['fd']:.4f}, bb:{g['bb']:.4f}, cf:{g['cf']:.4f}, "
            f"bp:{g['bp']:.4f}, pg:{g['pg']:.4f}, md:{g['md']:.4f}}}"
        )
        label_escaped = tp['label'].replace("'", "\\'")
        tp_js_items.append(
            f"  {{ label:'{label_escaped}', guilds:{guilds_js} }}"
        )
    new_hr_tps = "const HR_TPS = [\n" + ",\n".join(tp_js_items) + "\n];"

    # Replace existing HR_TPS definition (handles single or multi-line)
    hr_tps_pattern = re.compile(
        r'const HR_TPS\s*=\s*\[.*?\];',
        re.DOTALL
    )
    if hr_tps_pattern.search(html):
        html = hr_tps_pattern.sub(new_hr_tps, html, count=1)
    else:
        # Fallback: inject before first script closing tag
        html = html.replace('</script>', new_hr_tps + '\n</script>', 1)

    # ── Update max slider value ───────────────────────────────────────────────
    max_idx = len(timepoints) - 1
    # Update both sliders (banner + diagram)
    html = re.sub(
        r'(id="banner-tp-slider"[^>]*max=")[0-9]+"',
        lambda m: m.group(0).replace(m.group(1) + m.group(0).split(m.group(1))[1].split('"')[0], m.group(1) + str(max_idx)),
        html
    )
    # Simpler: replace all max="2" that belong to our sliders
    html = re.sub(
        r'(id="(?:banner-tp-slider|hr-tp-slider)"[^>]*)max="[0-9]+"',
        lambda m: m.group(1) + f'max="{max_idx}"',
        html
    )

    # ── Update metadata in cover ──────────────────────────────────────────────
    # Use the most recent (last) JSON for metadata
    latest = timepoints[-1]['_raw']
    meta = latest.get('metadata', {})
    sample_id = meta.get('sample_id', '')
    patient_name = meta.get('patient_name', meta.get('name', sample_id))
    report_date = meta.get('report_date', '')

    # Update the cover title (Inside Your Gut subtitle / name)
    if patient_name:
        html = re.sub(
            r"(id=['\"]banner-tp-label['\"]>)[^<]*(</div>)",
            f"\\g<1>{patient_name}\\2",
            html
        )

    # Update <title> tag
    if sample_id:
        html = re.sub(
            r'<title>[^<]*</title>',
            f'<title>Microbiome Report — {sample_id}</title>',
            html
        )

    # ── Write output ─────────────────────────────────────────────────────────
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"✓ Dashboard generated: {output_path}")
    print(f"  Timepoints: {len(timepoints)}")
    for i, tp in enumerate(timepoints):
        g = tp['guilds']
        print(f"  [{i}] {tp['label']}")
        print(f"       fd={g['fd']*100:.1f}%  bb={g['bb']*100:.1f}%  "
              f"cf={g['cf']*100:.1f}%  bp={g['bp']*100:.1f}%  "
              f"pg={g['pg']*100:.1f}%  md={g['md']*100:.1f}%")


def main():
    parser = argparse.ArgumentParser(
        description='Generate NB1 microbiome dashboard from platform JSON(s).',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single timepoint
  python3 generate_nb1_dashboard.py sample_platform.json

  # Single timepoint, custom output
  python3 generate_nb1_dashboard.py sample_platform.json output.html

  # Multiple timepoints (slider will show all)
  python3 generate_nb1_dashboard.py tp1_platform.json tp2_platform.json tp3_platform.json

  # Multiple timepoints, custom output
  python3 generate_nb1_dashboard.py tp1.json tp2.json tp3.json --output dashboard.html

Compatible with the original generate_dashboard.py interface:
  python3 generate_nb1_dashboard.py <platform_json> [output_path] [analysis_json]
  (analysis_json is accepted but ignored — all data comes from platform_json)
        """
    )

    parser.add_argument('jsons', nargs='+',
                        help='Platform JSON file(s). Multiple = one per timepoint.')
    parser.add_argument('--output', '-o', default=None,
                        help='Output HTML path. Defaults to first JSON with _nb1_dashboard.html.')

    args = parser.parse_args()

    # ── Separate JSON paths from output path ──────────────────────────────────
    # Support original interface: positional args where 2nd might be output .html
    json_paths = []
    output_path = args.output

    for arg in args.jsons:
        if arg.endswith('.html') and output_path is None:
            # Treat as output path (original interface compatibility)
            output_path = arg
        elif arg.endswith('.json'):
            json_paths.append(arg)
        elif os.path.isfile(arg):
            # Could be a JSON without .json extension
            json_paths.append(arg)
        else:
            print(f"Warning: skipping unrecognised argument '{arg}'")

    if not json_paths:
        parser.error("At least one platform JSON file is required.")

    if output_path is None:
        base = json_paths[0].replace('_platform.json', '').replace('.json', '')
        output_path = base + '_nb1_dashboard.html'

    generate_nb1_dashboard(json_paths, output_path)


if __name__ == '__main__':
    main()