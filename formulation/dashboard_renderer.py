#!/usr/bin/env python3
"""
Generate HTML Dashboards from Formulation JSON Outputs.

Produces two dashboards per sample:
1. Client Dashboard — "Personalized Supplement Guide" 
2. Scientific Board Dashboard — "Formulation Decision Trace"

Both read existing JSON files from the pipeline output.
"""

import json
import os
import re
from pathlib import Path
from datetime import datetime

from shared.formatting import format_dose as _format_dose


def _load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _esc(text):
    """HTML-escape a string."""
    if not text:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _md_to_html(text):
    """Convert basic markdown formatting to HTML for display in dashboard boxes.

    Handles:
    - **bold** → <strong>bold</strong>
    - *italic* → <em>italic</em>  (used for taxon names like *Akkermansia*)
    - Preserves all other text as-is (HTML-escaped)
    """
    if not text:
        return ""
    import re as _re
    # HTML-escape first to prevent injection
    escaped = str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    # Convert **bold** → <strong>bold</strong>
    escaped = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)
    # Convert *italic* → <em>italic</em>
    escaped = _re.sub(r'\*(.+?)\*', r'<em>\1</em>', escaped)
    return escaped


# ══════════════════════════════════════════════════════════════════════════════
#  DASHBOARD 1: CLIENT-FACING — "Your Personalized Supplement Guide"
# ══════════════════════════════════════════════════════════════════════════════

def build_client_dashboard(sample_id: str, output_dir: str) -> str:
    """Build client-facing supplement guide dashboard HTML."""
    
    output_dir = Path(output_dir)
    rationale = _load_json(output_dir / f"component_rationale_{sample_id}.json")
    recipe = _load_json(output_dir / f"manufacturing_recipe_{sample_id}.json")
    platform = _load_json(output_dir / f"formulation_platform_{sample_id}.json")
    master = _load_json(output_dir / f"formulation_master_{sample_id}.json")
    
    health_table = rationale["how_this_addresses_your_health"]
    source = rationale["source_attribution"]
    axes = rationale["health_axis_predictions"]
    units = recipe["units"]
    grand = recipe["grand_total"]
    overview = platform.get("overview", {})
    mix = platform.get("synbiotic_mix", {})
    q_coverage = master.get("questionnaire_coverage", {})
    
    # Group components by delivery
    by_delivery = {}
    for item in health_table:
        d = item.get("delivery", "other")
        if d not in by_delivery:
            by_delivery[d] = []
        by_delivery[d].append(item)
    
    # Build unit cards HTML
    unit_cards = ""
    for unit in units:
        ingredients_html = ""
        if "ingredients" in unit:
            for ing in unit["ingredients"]:
                amount = _format_dose(ing.get("amount_g", ing.get("amount_mg", ing.get("amount", ""))))
                unit_str = "g" if "amount_g" in ing else ("mg" if "amount_mg" in ing else "")
                cfu = f' — {ing["cfu_billions"]}B CFU' if "cfu_billions" in ing else ""
                cat = f' <span class="badge badge-{ing.get("category", "")}">{ing.get("category", "")}</span>' if ing.get("category") else ""
                ingredients_html += f'<tr><td>{ing["component"]}</td><td>{amount}{unit_str}{cfu}{cat}</td></tr>'
        elif "ingredients_per_unit" in unit:
            for ing in unit["ingredients_per_unit"]:
                dose = ing.get("dose_per_softgel", ing.get("amount_mg", ""))
                ingredients_html += f'<tr><td>{ing["component"]}</td><td>{dose}</td></tr>'
        
        timing_class = "morning" if "morning" in unit.get("timing", "") else "evening"
        uw_mg = unit.get("total_weight_mg") or unit.get("fill_weight_mg")
        uw_g = unit.get("total_weight_g") or unit.get("fill_weight_per_unit_mg")
        weight_str = ""
        if uw_mg:
            weight_str = f'<span style="font-size:12px;color:#888;margin-left:8px">{uw_mg}mg</span>'
        elif uw_g:
            weight_str = f'<span style="font-size:12px;color:#888;margin-left:8px">{uw_g}g</span>'
        unit_cards += f'''
        <div class="unit-card {timing_class}">
            <div class="unit-header">
                <span class="unit-number">Unit {unit["unit_number"]}</span>
                <span class="unit-label">{unit["label"]}{weight_str}</span>
                <span class="unit-timing">{unit.get("quantity", 1)}× {unit["timing"]}</span>
            </div>
            <table class="ingredients-table">
                <thead><tr><th>Component</th><th>Amount</th></tr></thead>
                <tbody>{ingredients_html}</tbody>
            </table>
        </div>'''
    
    # Build health table HTML
    health_rows = ""
    for item in health_table:
        source_class = item.get("source", "").replace("_", "-")
        source_badge = item["source"].replace("_", " ").title()
        health_rows += f'''
        <tr>
            <td class="component-name">{item["component"]}</td>
            <td>{item["what_it_targets"]}</td>
            <td>{item["based_on"]}</td>
            <td><span class="source-badge {source_class}">{source_badge}</span></td>
        </tr>'''
    
    axes_html = ""
    for ax in axes:
        status_class = "confirmed" if "CONFIRMED" in ax["questionnaire_verification"] else "predicted"
        axes_html += f'''
        <div class="axis-card {status_class}">
            <div class="axis-name">{ax["axis"]}</div>
            <div class="axis-detail"><strong>Pattern:</strong> {ax["microbiome_pattern"]}</div>
            <div class="axis-detail"><strong>Verification:</strong> {ax["questionnaire_verification"]}</div>
        </div>'''
    
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Supplement Guide — {sample_id}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; color: #303438; }}
.header {{ background: linear-gradient(135deg, #990000, #660000); color: white; padding: 40px 32px; }}
.header h1 {{ font-size: 28px; font-weight: 600; margin-bottom: 8px; }}
.header .subtitle {{ opacity: 0.85; font-size: 15px; }}
.header .protocol {{ display: flex; gap: 24px; margin-top: 20px; flex-wrap: wrap; }}
.header .stat {{ background: rgba(255,255,255,0.15); padding: 12px 20px; border-radius: 10px; }}
.header .stat-value {{ font-size: 24px; font-weight: 700; }}
.header .stat-label {{ font-size: 12px; opacity: 0.8; }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
.section {{ background: white; border-radius: 16px; padding: 28px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
.section h2 {{ font-size: 20px; color: #006676; margin-bottom: 16px; border-bottom: 2px solid #e0e0e0; padding-bottom: 8px; }}
.unit-card {{ border: 1px solid #e0e0e0; border-radius: 12px; padding: 16px; margin-bottom: 12px; }}
.unit-card.morning {{ border-left: 4px solid #f4a261; }}
.unit-card.evening {{ border-left: 4px solid #6c5ce7; }}
.unit-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }}
.unit-number {{ background: #006676; color: white; padding: 4px 10px; border-radius: 6px; font-size: 13px; font-weight: 600; }}
.unit-label {{ font-weight: 600; font-size: 16px; flex: 1; }}
.unit-timing {{ background: #f0f0f0; padding: 4px 10px; border-radius: 6px; font-size: 13px; }}
.ingredients-table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
.ingredients-table th {{ background: #f9f9f9; text-align: left; padding: 8px 12px; font-weight: 600; }}
.ingredients-table td {{ padding: 6px 12px; border-top: 1px solid #f0f0f0; }}
.health-table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
.health-table th {{ background: #006676; color: white; padding: 10px 12px; text-align: left; }}
.health-table td {{ padding: 10px 12px; border-bottom: 1px solid #f0f0f0; vertical-align: top; }}
.component-name {{ font-weight: 600; min-width: 200px; }}
.source-badge {{ padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; white-space: nowrap; }}
.microbiome-primary {{ background: #d4edda; color: #155724; }}
.microbiome-linked {{ background: #cce5ff; color: #004085; }}
.questionnaire-only {{ background: #fff3cd; color: #856404; }}
.fixed-component {{ background: #e2e3e5; color: #383d41; }}
.source-summary {{ display: flex; gap: 16px; flex-wrap: wrap; margin-top: 12px; }}
.source-stat {{ text-align: center; padding: 12px 16px; border-radius: 8px; min-width: 120px; }}
.source-stat .num {{ font-size: 24px; font-weight: 700; }}
.source-stat .lbl {{ font-size: 12px; color: #666; }}
.axis-card {{ border: 1px solid #e0e0e0; border-radius: 8px; padding: 12px 16px; margin-bottom: 8px; }}
.axis-card.confirmed {{ border-left: 4px solid #28a745; }}
.axis-card.predicted {{ border-left: 4px solid #ffc107; }}
.axis-name {{ font-weight: 700; font-size: 15px; margin-bottom: 4px; }}
.axis-detail {{ font-size: 13px; color: #555; margin-top: 4px; }}
.footer {{ text-align: center; padding: 20px; color: #999; font-size: 12px; }}
</style>
</head>
<body>
<div class="header">
    <h1>🧬 Personalized Supplement Guide</h1>
    <div class="subtitle">Client Code: {sample_id} | Protocol: 16 weeks | Generated: {datetime.now().strftime('%B %d, %Y')}</div>
    <div class="protocol">
        <div class="stat"><div class="stat-value">{grand["total_units"]}</div><div class="stat-label">Daily Units</div></div>
        <div class="stat"><div class="stat-value">{grand["total_daily_weight_g"]}g</div><div class="stat-label">Total Weight</div></div>
        <div class="stat"><div class="stat-value">{grand["morning_units"]}</div><div class="stat-label">Morning</div></div>
        <div class="stat"><div class="stat-value">{grand["evening_units"]}</div><div class="stat-label">Evening</div></div>
        <div class="stat"><div class="stat-value">{source["microbiome_informed_pct"]}%</div><div class="stat-label">Microbiome-Informed</div></div>
    </div>
</div>
<div class="container">
    <div class="section"><h2>📦 Daily Protocol</h2>{unit_cards}</div>
    <div class="section">
        <h2>🎯 How This Formulation Addresses Health Goals</h2>
        <table class="health-table">
            <thead><tr><th>Component</th><th>What It Targets</th><th>Based On</th><th>Source</th></tr></thead>
            <tbody>{health_rows}</tbody>
        </table>
    </div>
    <div class="section">
        <h2>📊 Evidence Source Attribution</h2>
        <div class="source-summary">
            <div class="source-stat" style="background:#d4edda"><div class="num">{source["microbiome_primary"]}</div><div class="lbl">Microbiome Primary</div></div>
            <div class="source-stat" style="background:#cce5ff"><div class="num">{source["microbiome_linked"]}</div><div class="lbl">Microbiome Linked</div></div>
            <div class="source-stat" style="background:#fff3cd"><div class="num">{source["questionnaire_only"]}</div><div class="lbl">Questionnaire Only</div></div>
            <div class="source-stat" style="background:#e2e3e5"><div class="num">{source["fixed_component"]}</div><div class="lbl">Fixed Component</div></div>
        </div>
    </div>
    <div class="section"><h2>🔬 Health Axis Predictions</h2>{axes_html}</div>
</div>
<div class="footer">
    This personalized supplement formulation is based on microbiome analysis and health questionnaire.<br>
    Not intended to diagnose, treat, cure, or prevent any disease. Consult healthcare provider.
</div>
</body>
</html>'''
    
    return html


# ══════════════════════════════════════════════════════════════════════════════
#  DASHBOARD 2: SCIENTIFIC BOARD — "Formulation Decision Trace"
# ══════════════════════════════════════════════════════════════════════════════

# ── CSS ──────────────────────────────────────────────────────────────────────

BOARD_CSS = """
:root {
  --sand: #F5F0E8;
  --warm: #FDFAF5;
  --dark: #1E1E2A;
  --mid: #4A4858;
  --soft: #9A95A8;
  --rule: #E4DDD0;
  --green: #2E8B6E;
  --green-lt: #E8F5F1;
  --red: #C24B3A;
  --red-lt: #FCECEA;
  --amber: #C97C2A;
  --amber-lt: #FBF1E4;
  --blue: #3A6EA8;
  --blue-lt: #EAF0F8;
  --purple: #6B5EA8;
  --purple-lt: #F0EEF8;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:'Nunito',sans-serif; background:var(--sand); color:var(--dark); font-size:15px; line-height:1.7; }

/* Cover */
.cover { background:var(--dark); padding:40px 56px; position:relative; overflow:hidden; }
.cover-blob1 { position:absolute; width:500px; height:500px; top:-120px; right:-100px; border-radius:50%; background:radial-gradient(circle,rgba(46,139,110,.18) 0%,transparent 65%); pointer-events:none; }
.cover-blob2 { position:absolute; width:350px; height:350px; bottom:-80px; left:-60px; border-radius:50%; background:radial-gradient(circle,rgba(107,94,168,.14) 0%,transparent 65%); pointer-events:none; }
.cover-top { display:flex; justify-content:space-between; align-items:center; position:relative; z-index:1; margin-bottom:32px; }
.cover-brand { font-family:'Playfair Display',serif; font-size:14px; color:rgba(255,255,255,.45); letter-spacing:.15em; }
.cover-tag { font-size:11px; letter-spacing:.2em; text-transform:uppercase; color:var(--green); background:rgba(46,139,110,.12); padding:6px 14px; border-radius:20px; }
.cover-body { position:relative; z-index:1; }
.cover-eyebrow { font-size:11px; letter-spacing:.3em; text-transform:uppercase; color:rgba(255,255,255,.38); margin-bottom:12px; }
.cover-h1 { font-family:'Playfair Display',serif; font-size:clamp(32px,5vw,56px); font-weight:400; color:white; line-height:1.1; margin-bottom:10px; }
.cover-h1 span { font-style:italic; color:rgba(255,255,255,.35); }
.cover-sub { font-size:14px; color:rgba(255,255,255,.45); margin-bottom:32px; max-width:480px; }
.cover-stats { display:flex; gap:28px; flex-wrap:wrap; margin-top:20px; }
.cover-stat .cs-label { font-size:10px; letter-spacing:.18em; text-transform:uppercase; color:rgba(255,255,255,.25); margin-bottom:3px; }
.cover-stat .cs-val { font-size:13px; color:rgba(255,255,255,.65); }

/* Sections */
.section { padding:48px 56px; background:var(--warm); border-bottom:1px solid var(--rule); }
.section.sand { background:var(--sand); }
.sec-label { font-size:12px; letter-spacing:.3em; text-transform:uppercase; color:var(--soft); margin-bottom:8px; }
.sec-title { font-family:'Playfair Display',serif; font-size:36px; font-weight:400; margin-bottom:8px; }
.sec-intro { color:var(--mid); max-width:700px; line-height:1.85; margin-bottom:36px; font-size:14px; }

/* Guild bars */
.guild-bars { display:flex; flex-direction:column; gap:12px; }
.gbar { background:var(--warm); border:1px solid var(--rule); border-radius:10px; padding:14px 18px; }
.gbar.critical { background:var(--red-lt); border-color:rgba(194,75,58,.3); }
.gbar.below { background:var(--blue-lt); border-color:rgba(58,110,168,.3); }
.gbar.above { background:var(--amber-lt); border-color:rgba(201,124,42,.3); }
.gbar.ok { background:var(--green-lt); border-color:rgba(46,139,110,.2); }
.gbar-top { display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }
.gbar-name { font-size:13px; font-weight:600; }
.gbar-badge { font-size:10px; font-weight:600; letter-spacing:.1em; text-transform:uppercase; padding:3px 10px; border-radius:20px; }
.badge-critical { background:var(--red); color:white; }
.badge-below { background:var(--blue); color:white; }
.badge-above { background:var(--amber); color:white; }
.badge-ok { background:var(--green); color:white; }
.gbar-track { height:5px; background:rgba(0,0,0,.07); border-radius:3px; }
.gbar-fill { height:100%; border-radius:3px; }
.fill-critical { background:var(--red); }
.fill-below { background:var(--blue); }
.fill-above { background:var(--amber); }
.fill-ok { background:var(--green); }

/* CLR chips */
.clr-chip { display:inline-block; padding:4px 12px; border-radius:20px; margin:3px 3px 3px 0; font-size:11px; background:var(--sand); color:var(--mid); border:1px solid var(--rule); }

/* Step cards */
.step-card { background:var(--warm); border:1px solid var(--rule); border-radius:10px; padding:20px 24px; margin-bottom:12px; }

/* Delivery units */
.supp-unit { background:var(--warm); border:1px solid var(--rule); border-radius:12px; overflow:hidden; margin-bottom:16px; }
.supp-header { display:grid; grid-template-columns:56px 1fr auto; align-items:center; border-bottom:1px solid var(--rule); }
.supp-num-block { width:56px; height:56px; display:flex; align-items:center; justify-content:center; font-family:'Playfair Display',serif; font-size:22px; color:white; }
.supp-head-text { padding:12px 16px; }
.supp-unit-name { font-size:14px; font-weight:600; }
.supp-unit-when { font-size:11px; color:var(--soft); }
.supp-unit-tag { padding:12px 16px; font-size:12px; color:var(--soft); font-weight:600; }
.supp-pills { display:flex; flex-wrap:wrap; gap:6px; padding:14px 18px 18px; }
.pill { background:var(--sand); border:1px solid var(--rule); border-radius:20px; padding:4px 12px; font-size:12px; color:var(--mid); }
.pill strong { color:var(--dark); font-weight:600; }

/* Source chips */
.src-mb { background:var(--green-lt); color:var(--green); padding:2px 9px; border-radius:10px; font-size:10px; font-weight:600; }
.src-q  { background:var(--amber-lt); color:var(--amber); padding:2px 9px; border-radius:10px; font-size:10px; font-weight:600; }

/* Goal chips */
.goal-chip { background:var(--warm); border:1px solid var(--rule); border-radius:20px; padding:4px 14px; font-size:12px; color:var(--mid); display:inline-block; margin:2px 2px 2px 0; }
.goal-chip.highlighted { border-color:var(--green); background:var(--green-lt); color:var(--green); font-weight:600; }

/* Claim chip */
.claim-chip { background:var(--warm); color:var(--blue); padding:3px 10px; border-radius:20px; font-size:11px; margin:2px; display:inline-block; font-weight:600; border:1px solid var(--blue); }

/* Delivery group */
.delivery-group { border:1px solid var(--rule); border-radius:10px; padding:14px 18px; margin-bottom:10px; }
.delivery-group-label { font-size:10px; letter-spacing:.15em; text-transform:uppercase; color:var(--soft); margin-bottom:8px; font-weight:700; }

/* Footer */
.footer { background:var(--dark); color:rgba(255,255,255,.3); padding:28px 56px; display:flex; justify-content:space-between; align-items:center; font-size:11px; }
.footer-brand { font-family:'Playfair Display',serif; font-size:15px; color:rgba(255,255,255,.55); }

@media(max-width:900px){
  .cover { padding:32px 24px; }
  .section { padding:36px 24px; }
  .footer { flex-direction:column; gap:10px; text-align:center; }
}
"""


# ── Helper: Guild bars ──────────────────────────────────────────────────────

CONTEXTUAL_GUILDS = {"proteolytic", "mucin_degraders", "proteolytic guild", "proteolytic dysbiosis guild", "mucin degraders"}

_STATUS_TO_GBAR = {
    "Above range": ("above", "badge-above", "↑ High"),
    "Below range": ("below", "badge-below", "↓ Low"),
    "Within range": ("ok", "badge-ok", "✓ Healthy"),
    "Absent": ("critical", "badge-critical", "✗ Absent"),
}
_CONTEXTUAL_STATUS_TO_GBAR = {
    "Above range": ("critical", "badge-critical", "↑ Elevated"),
    "Below range": ("ok", "badge-ok", "✓ Low — Good"),
    "Within range": ("ok", "badge-ok", "✓ Controlled"),
    "Absent": ("ok", "badge-ok", "✓ Absent — Good"),
}
_PRIO_BADGE_COLORS = {"CRITICAL": "#C24B3A", "1A": "#C97C2A", "1B": "#C97C2A", "Monitor": "#2E8B6E"}


def _build_guild_bars(guilds):
    html = ""
    for g in guilds:
        name = g.get("name", "?")
        status = g.get("status", "?")
        pct = g.get("abundance_pct", 0) or 0
        prio = g.get("priority_level", "")
        guild_clr = g.get("clr")
        is_ctx = name.lower() in CONTEXTUAL_GUILDS
        sm = _CONTEXTUAL_STATUS_TO_GBAR if is_ctx else _STATUS_TO_GBAR
        gbar_cls, badge_cls, badge_prefix = sm.get(status, ("ok", "badge-ok", "·"))
        pct_str = f"{pct:.1f}%" if isinstance(pct, (int, float)) else str(pct)
        prio_badge = ""
        if prio and prio != "Monitor":
            pc = _PRIO_BADGE_COLORS.get(prio, "#C97C2A")
            prio_badge = f' <span style="background:{pc};color:white;padding:2px 7px;border-radius:10px;font-size:9px;font-weight:700;letter-spacing:.05em">{prio}</span>'
        clr_note = ""
        if guild_clr is not None:
            try:
                cv = float(guild_clr)
                cc = "var(--green)" if cv > 0.1 else ("var(--red)" if cv < -0.3 else "var(--mid)")
                clr_note = f' &nbsp;<span style="font-size:11px;color:{cc}">CLR {cv:+.2f}</span>'
            except (ValueError, TypeError):
                pass
        bar_pct = min(100, max(2, pct / 0.55)) if isinstance(pct, (int, float)) else 2
        html += f'''<div class="gbar {gbar_cls}">
          <div class="gbar-top">
            <span class="gbar-name">{_esc(name)}{prio_badge}</span>
            <span class="gbar-badge {badge_cls}">{badge_prefix} · {pct_str}{clr_note}</span>
          </div>
          <div class="gbar-track"><div class="gbar-fill fill-{gbar_cls}" style="width:{bar_pct:.1f}%"></div></div>
        </div>'''
    return html


# ── Helper: Delivery unit card ──────────────────────────────────────────────

_UNIT_COLORS = ["#1E1E2A", "#C97C2A", "#2E8B6E", "#6B5EA8", "#3A6EA8", "#C24B3A", "#4A4858"]

def _build_unit_card(unit, recipe_units=None):
    """Build a single delivery unit HTML card from a recipe unit dict."""
    u_color = _UNIT_COLORS[(unit["unit_number"] - 1) % len(_UNIT_COLORS)]
    is_evening = "evening" in unit.get("timing", "").lower()
    timing_accent = "var(--purple)" if is_evening else "var(--amber)"
    timing_emoji = "🌙" if is_evening else "🌅"
    tw_mg = unit.get("total_weight_mg") or unit.get("total_fill_weight_mg")
    tw_g = unit.get("total_weight_g")
    if not tw_mg and not tw_g:
        fill = unit.get("fill_weight_per_unit_mg") or unit.get("fill_weight_mg")
        if fill:
            tw_mg = fill * unit.get("quantity", 1)
    elemental_note = ""
    if "magnesium" in unit.get("label", "").lower():
        elem = (unit.get("daily_totals") or {}).get("elemental_mg_mg", 0)
        if elem:
            elemental_note = f" <span style='font-size:11px;color:var(--soft)'>({elem}mg elemental)</span>"
    weight_str = f"{tw_mg}mg{elemental_note}" if tw_mg else (f"{tw_g}g" if tw_g else "")

    pills_html = ""
    qty = unit.get("quantity", 1)
    ingredients_raw = unit.get("ingredients", [])
    ingredients_per = unit.get("ingredients_per_unit", [])
    capsule_layout = unit.get("capsule_layout", [])

    if capsule_layout and len(capsule_layout) > 1:
        for cap in capsule_layout:
            cap_num = cap.get("capsule_number", "?")
            fill_mg = cap.get("fill_mg", 0)
            cap_comps = cap.get("components", [])
            cap_items = " · ".join(
                f'<strong>{c.get("substance","?")}</strong> {c.get("dose_mg","?") or c.get("dose","?")}{"mg" if c.get("dose_mg") else ""}'
                for c in cap_comps
            )
            pills_html += f'<div style="width:100%;margin-bottom:6px"><span style="background:var(--purple-lt);color:var(--purple);padding:2px 10px;border-radius:20px;font-size:10px;font-weight:700;margin-right:8px">Capsule {cap_num} · {fill_mg}mg</span><span style="font-size:12px;color:var(--mid)">{cap_items}</span></div>'
    elif ingredients_per:
        qty_label = f"Per softgel (×{qty})" if "softgel" in unit.get("label","").lower() else f"Per unit (×{qty})"
        pills_html += f'<div style="font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:var(--soft);margin-bottom:8px;width:100%">{qty_label}</div>'
        for ing in ingredients_per:
            dose_str = ing.get("dose_per_softgel", ing.get("amount", ""))
            elemental = f" ({ing['elemental_mg']}mg elemental)" if ing.get("elemental_mg") else ""
            pills_html += f'<div class="pill"><strong>{ing.get("component","?")}</strong> · {dose_str}{elemental}</div>'
    elif ingredients_raw:
        for ing in ingredients_raw:
            amount_g = ing.get("amount_g")
            amount_mg = ing.get("amount_mg")
            dose = ing.get("dose_per_softgel", ing.get("amount", ""))
            cfu = f" · {ing['cfu_billions']}B CFU" if "cfu_billions" in ing else ""
            elemental = f" ({ing['elemental_mg']}mg elemental)" if ing.get("elemental_mg") else ""
            if amount_g: amt = f"{_format_dose(amount_g)}g"
            elif amount_mg: amt = f"{_format_dose(amount_mg)}mg{elemental}"
            elif dose: amt = str(_format_dose(dose))
            else: amt = "—"
            pills_html += f'<div class="pill"><strong>{ing.get("component","?")}</strong> · {amt}{cfu}</div>'

    phased = unit.get("phased_dosing", {})
    phased_note = ""
    if phased and phased.get("weeks_1_2_g") and phased.get("weeks_3_plus_g"):
        phased_note = f'''<div style="width:100%;margin-top:8px;background:var(--amber-lt);border-left:3px solid var(--amber);border-radius:0 6px 6px 0;padding:8px 14px;font-size:12px;color:var(--mid)">
          ⚠ <strong style="color:var(--amber)">Phased dosing:</strong>
          Weeks 1–2: <strong>{phased["weeks_1_2_g"]}g/day</strong> (half dose) →
          Week 3+: <strong>{phased["weeks_3_plus_g"]}g/day</strong> (full dose)
        </div>'''

    return f'''<div class="supp-unit">
      <div class="supp-header">
        <div class="supp-num-block" style="background:{u_color}">{unit["unit_number"]}</div>
        <div class="supp-head-text">
          <div class="supp-unit-name">{_esc(unit["label"])}</div>
          <div class="supp-unit-when">{timing_emoji} {qty}× {unit["timing"]}</div>
        </div>
        <div class="supp-unit-tag" style="color:{timing_accent};font-weight:600">{weight_str}</div>
      </div>
      <div class="supp-pills">{pills_html}{phased_note}</div>
    </div>'''


# ── Helper: Build delivery label lookup from recipe units ───────────────────

def _build_delivery_label_lookup(recipe: dict) -> dict:
    """Build a mapping from component_rationale delivery keys → (emoji, display_label).

    Derived entirely from the manufacturing recipe units so Section 7 (Supplement
    Selection) always matches Section 3 (Final Formulation). If timing changes in
    the pipeline the label here updates automatically — one source of truth.

    Delivery key → recipe unit label + timing mapping:
      "probiotic capsule"       ← Probiotic Hard Capsule
      "softgel"                 ← Omega + Antioxidant Softgel
      "sachet"                  ← Prebiotic & Botanical Powder Jar
      "morning wellness capsule" ← Morning Wellness Capsule (non-polyphenol)
      "polyphenol capsule"      ← Morning Wellness Capsule (timing contains "food")
      "evening capsule"         ← Evening Wellness Capsule
      "magnesium capsule"       ← Magnesium Bisglycinate Capsule
    """
    lookup = {}
    for unit in recipe.get("units", []):
        label = unit.get("label", "")
        timing = unit.get("timing", "")
        is_morning = "evening" not in timing.lower()
        is_evening = "evening" in timing.lower()
        timing_emoji = "🌙" if is_evening else "🌅"

        label_lower = label.lower()

        if "probiotic" in label_lower:
            lookup["probiotic capsule"] = f"💊 Probiotic Capsule"

        elif "softgel" in label_lower or "omega" in label_lower:
            lookup["softgel"] = f"🐟 Softgel Unit"

        elif "jar" in label_lower or "powder" in label_lower:
            lookup["sachet"] = f"🫙 Jar Unit"

        elif "morning wellness" in label_lower or ("morning" in timing.lower() and "capsule" in label_lower):
            if "food" in timing.lower():
                # Polyphenol capsule — "morning, with food"
                lookup["polyphenol capsule"] = f"🌅 Polyphenol Capsule (with food)"
            else:
                lookup.setdefault("morning wellness capsule", f"🌅 Morning Capsules")

        elif "evening wellness" in label_lower:
            lookup["evening capsule"] = f"🌙 Evening Capsules"

        elif "magnesium" in label_lower:
            _mg_timing_emoji = timing_emoji  # always evening per timing engine
            lookup["magnesium capsule"] = f"{_mg_timing_emoji} Magnesium Capsules"

    # Fallback defaults for anything not found in recipe
    lookup.setdefault("probiotic capsule", "💊 Probiotic Capsule")
    lookup.setdefault("softgel", "🐟 Softgel Unit")
    lookup.setdefault("sachet", "🫙 Jar Unit")
    lookup.setdefault("morning wellness capsule", "🌅 Morning Capsules")
    lookup.setdefault("polyphenol capsule", "🌅 Polyphenol Capsule (with food)")
    lookup.setdefault("evening capsule", "🌙 Evening Capsules")
    lookup.setdefault("magnesium capsule", "🌙 Magnesium Capsules")

    return lookup


# ── Helper: Highlight claim keywords in text ─────────────────────────────────

def _highlight_claims_in_text(text, claims_set):
    """Highlight keywords in text that match questionnaire claims.

    IMPORTANT: Operates on plain text first (before any HTML injection),
    then wraps matches with <mark> tags. This prevents corrupting existing
    HTML attributes like style="font-weight:600".
    """
    if not text or not claims_set:
        return _esc(text)
    # Work on escaped (plain) text only — no HTML attributes to corrupt
    plain = _esc(text)
    # Collect unique keywords, deduplicate, filter out very short words
    keywords = set()
    for claim in claims_set:
        kw = claim.lower().strip()
        if len(kw) > 3:
            keywords.add(kw)
        if len(kw.split()) > 1:
            for w in kw.split():
                if len(w) > 4:  # only words >4 chars to avoid false positives
                    keywords.add(w)
    # Sort longest-first to avoid partial match clobbering
    sorted_kw = sorted(keywords, key=len, reverse=True)
    for kw in sorted_kw:
        # Only match whole words, case-insensitive — use re.IGNORECASE flag (not inline (?i))
        # Inline (?i) must be at position 0; lookbehind before it causes "global flags not at start" error
        pattern = re.compile(r'\b(' + re.escape(kw) + r')\b', re.IGNORECASE)
        plain = pattern.sub(
            r'<mark style="background:var(--purple-lt);color:var(--purple);padding:0 3px;border-radius:3px;font-weight:600">\1</mark>',
            plain
        )
    return plain


# ── Main builder ─────────────────────────────────────────────────────────────

def build_board_dashboard(sample_id: str, output_dir: str) -> str:
    """Build scientific board decision trace dashboard HTML."""
    
    output_dir = Path(output_dir)
    trace = _load_json(output_dir / f"decision_trace_{sample_id}.json")
    rationale = _load_json(output_dir / f"component_rationale_{sample_id}.json")
    master = _load_json(output_dir / f"formulation_master_{sample_id}.json")
    recipe = _load_json(output_dir / f"manufacturing_recipe_{sample_id}.json")
    
    from formulation.platform_mapping import _evening_capsule_label
    
    inputs = trace["inputs"]
    steps = trace["decision_chain"]
    final = trace["final_formulation"]
    evidence = trace.get("evidence_sources", {})
    axes = rationale["health_axis_predictions"]
    _ht_seen = set()
    _ht_deduped = []
    for _ht_item in rationale.get("how_this_addresses_your_health", []):
        _ht_key = _ht_item.get("component", "").lower()
        if _ht_key not in _ht_seen:
            _ht_seen.add(_ht_key)
            _ht_deduped.append(_ht_item)
    health_table = _ht_deduped
    source = rationale["source_attribution"]
    eco_rationale = trace.get("ecological_rationale", {})
    q_data = master.get("input_summary", {}).get("questionnaire_driven", {})
    q_coverage_data = master.get("questionnaire_coverage", {})
    q_narrative = master.get("input_narratives", {}).get("questionnaire_narrative", "")
    clinical_summary = master.get("clinical_summary", {})
    
    # ── Pre-compute shared data ──────────────────────────────────────────
    _cs_profile = clinical_summary.get("profile_narrative", [])
    _cs_flags = clinical_summary.get("clinical_review_flags", [])
    _cs_inferred = clinical_summary.get("inferred_health_signals", [])
    _inferred_map = {}
    for _sig in _cs_inferred:
        if isinstance(_sig, dict):
            _inferred_map[_sig.get("signal", "")] = _sig.get("reason", "")
        elif isinstance(_sig, str):
            _inferred_map[_sig] = ""
    
    q_goals = q_data.get("goals_ranked", [])
    q_claims = master.get("decisions", {}).get("rule_outputs", {}).get("health_claims", {}).get("supplement_claims", [])
    q_cov_level = q_coverage_data.get("coverage_level", "?")
    q_cov_pct = q_coverage_data.get("completion_pct", 0)
    q_cov_color = "var(--green)" if q_cov_level == "GOOD" else ("var(--amber)" if q_cov_level in ("LOW", "MINIMAL") else "var(--blue)")
    _demo = master.get("input_summary", {}).get("questionnaire_driven", {})
    sex_val = _demo.get("biological_sex", _demo.get("sex", "N/A"))
    age_val = _demo.get("age", "N/A")
    diet_val = _demo.get("diet", "N/A")
    _other_raw = _demo.get("other_raw_text") or ""
    _other_resolved = _demo.get("other_resolved_key") or ""

    # Claims set for keyword highlighting — normalize underscores to spaces
    _all_claims_set = set()
    for c in q_claims:
        normalized = c.replace("_", " ").lower()
        _all_claims_set.add(normalized)
        # Also add individual words >3 chars for partial matching
        _all_claims_set.update(w for w in normalized.split() if len(w) > 3)
    for g in q_goals:
        _all_claims_set.update(w.lower() for w in g.replace("_", " ").split() if len(w) > 3)

    # ══════════════════════════════════════════════════════════════════════
    #  BUILD HTML SECTIONS
    # ══════════════════════════════════════════════════════════════════════

    # ── COVER ────────────────────────────────────────────────────────────
    cover_html = f'''<div class="cover">
  <div class="cover-blob1"></div><div class="cover-blob2"></div>
  <div class="cover-top">
    <div class="cover-brand">NB1 Health · Formulation Engine</div>
    <div class="cover-tag">Scientific Board · Internal Review</div>
  </div>
  <div class="cover-body">
    <div class="cover-eyebrow">Formulation decision trace</div>
    <h1 class="cover-h1">Decision <span>Trace</span></h1>
    <p class="cover-sub">Full audit trail of every formulation decision — inputs, rules applied, LLM calls, and final output. For scientific board review only.</p>
    <div class="cover-stats">
      <div class="cover-stat"><div class="cs-label">Sample</div><div class="cs-val">{sample_id}</div></div>
      <div class="cover-stat"><div class="cs-label">Validation</div><div class="cs-val">{trace["validation"]}</div></div>
      <div class="cover-stat"><div class="cs-label">Decision Steps</div><div class="cs-val">{len(steps)}</div></div>
      <div class="cover-stat"><div class="cs-label">Total Units</div><div class="cs-val">{final["total_units"]}</div></div>
      <div class="cover-stat"><div class="cs-label">Total Weight</div><div class="cs-val">{final["total_weight_g"]}g</div></div>
      <div class="cover-stat"><div class="cs-label">MB-Informed</div><div class="cs-val">{source["microbiome_informed_pct"]}%</div></div>
      <div class="cover-stat"><div class="cs-label">Generated</div><div class="cs-val">{trace.get("generated_at","")[:10]}</div></div>
    </div>
  </div>
</div>'''

    # ── CLINICAL REVIEW FLAGS ────────────────────────────────────────────
    flags_html = ""
    # Extract medications from master JSON for display in flags section
    _q_driven = master.get("input_summary", {}).get("questionnaire_driven", {})
    _medications_list = _q_driven.get("medications", [])
    if _cs_flags:
        flag_cards = ""
        # Medication summary bar — show which medications the patient is taking
        med_bar_html = ""
        if _medications_list:
            med_pills = ""
            for _med in _medications_list:
                if isinstance(_med, dict):
                    _med_name = _med.get("name", "")
                    _med_dosage = _med.get("dosage", "")
                    _med_display = f"{_esc(_med_name)}"
                    if _med_dosage:
                        _med_display += f" <span style='color:rgba(255,255,255,.5);font-size:10px'>{_esc(_med_dosage)}</span>"
                elif isinstance(_med, str) and _med.strip():
                    _med_display = _esc(_med.strip())
                else:
                    continue
                med_pills += f'<span style="background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.25);border-radius:20px;padding:4px 12px;font-size:12px;color:white;display:inline-block;margin:2px 4px 2px 0">{_med_display}</span>'
            if med_pills:
                med_bar_html = f'''<div style="margin-bottom:16px;padding:14px 18px;background:rgba(0,0,0,.2);border-radius:10px;border:1px solid rgba(255,255,255,.15)">
                  <div style="font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:rgba(255,255,255,.5);font-weight:700;margin-bottom:8px">💊 Reported Medications</div>
                  <div style="display:flex;flex-wrap:wrap;gap:4px">{med_pills}</div>
                </div>'''
        for fl in _cs_flags:
            sev = fl.get("severity", "medium").lower()
            flag_cards += f'''<div style="background:transparent;border:2px solid var(--dark);border-radius:10px;padding:16px 20px;margin-bottom:10px">
              <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
                <span style="font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--dark);font-weight:700">{_esc(sev)}</span>
                <span style="font-size:14px;font-weight:700;color:white">{_esc(fl.get("title",""))}</span>
              </div>
              <div style="font-size:13px;color:rgba(255,255,255,.7);line-height:1.7;padding-left:0">{_esc(fl.get("detail",""))}</div>
            </div>'''
        flags_html = f'''<div style="background:linear-gradient(135deg,rgba(194,75,58,.85) 0%,rgba(140,40,30,.95) 100%);padding:36px 56px 36px;position:relative;overflow:hidden">
            <div style="position:relative;z-index:1;display:flex;align-items:flex-start;justify-content:space-between;gap:24px;flex-wrap:wrap;margin-bottom:20px">
              <div>
                <div style="font-size:10px;letter-spacing:.3em;text-transform:uppercase;color:rgba(255,255,255,.6);font-weight:700;margin-bottom:10px">⚠ Action Required Before Dispensing</div>
                <div style="font-family:'Playfair Display',serif;font-size:30px;font-weight:400;color:white;margin-bottom:6px">Clinical Review Flags</div>
                <div style="font-size:13px;color:rgba(255,255,255,.55);max-width:480px">These conditions require clinician evaluation before dispensing.</div>
              </div>
              <span style="background:rgba(255,255,255,.2);color:white;padding:8px 18px;border-radius:24px;font-size:13px;font-weight:700">{len(_cs_flags)} flag{"s" if len(_cs_flags) != 1 else ""}</span>
            </div>
            {med_bar_html}
            {flag_cards}
          </div>'''

    # ── MEDICATION TIMING OVERRIDE NOTICE ────────────────────────────────
    # Shown right after clinical review flags for samples with medication-driven
    # timing changes (e.g., thyroid meds → all units to dinner)
    med_override_html = ""
    med_rules = master.get("medication_rules", {})
    timing_override = med_rules.get("timing_override")
    if timing_override and timing_override.get("rule_id"):
        med_name = timing_override.get("medication_normalized", timing_override.get("medication", "Unknown"))
        med_raw = timing_override.get("medication", med_name)
        move_to = timing_override.get("move_to", "dinner")
        rule_id = timing_override.get("rule_id", "")
        reason = timing_override.get("reason", "")
        clinical_note = timing_override.get("clinical_note", "")
        severity = timing_override.get("severity", "high")
        med_override_html = f'''<div style="background:linear-gradient(135deg,rgba(58,110,168,.9) 0%,rgba(40,75,130,.95) 100%);padding:28px 56px;position:relative;overflow:hidden">
          <div style="position:relative;z-index:1">
            <div style="display:flex;align-items:center;gap:14px;margin-bottom:16px;flex-wrap:wrap">
              <span style="font-size:28px">💊</span>
              <div>
                <div style="font-size:10px;letter-spacing:.3em;text-transform:uppercase;color:rgba(255,255,255,.5);font-weight:700;margin-bottom:4px">Medication Timing Override · {_esc(rule_id)}</div>
                <div style="font-family:'Playfair Display',serif;font-size:24px;font-weight:400;color:white">All supplement units → {_esc(move_to)}</div>
              </div>
              <span style="margin-left:auto;background:rgba(255,255,255,.2);color:white;padding:6px 14px;border-radius:20px;font-size:12px;font-weight:700;letter-spacing:.1em;text-transform:uppercase">{_esc(severity)} priority</span>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
              <div style="background:rgba(255,255,255,.1);border-radius:8px;padding:14px 18px">
                <div style="font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:rgba(255,255,255,.45);margin-bottom:6px;font-weight:700">Medication</div>
                <div style="font-size:15px;color:white;font-weight:600">{_esc(med_name.title())}</div>
                <div style="font-size:12px;color:rgba(255,255,255,.6);margin-top:4px">Reported as: {_esc(med_raw)}</div>
              </div>
              <div style="background:rgba(255,255,255,.1);border-radius:8px;padding:14px 18px">
                <div style="font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:rgba(255,255,255,.45);margin-bottom:6px;font-weight:700">Action Taken</div>
                <div style="font-size:13px;color:rgba(255,255,255,.85);line-height:1.7">{_esc(reason)}</div>
              </div>
            </div>
            {"" if not clinical_note else f'<div style="margin-top:12px;background:rgba(255,255,255,.08);border-left:3px solid rgba(255,255,255,.3);border-radius:0 8px 8px 0;padding:10px 16px;font-size:12px;color:rgba(255,255,255,.7);line-height:1.7"><strong style="color:rgba(255,255,255,.9)">Clinical note:</strong> {_esc(clinical_note)}</div>'}
          </div>
        </div>'''

    # ── SECTION 0: PATIENT CLINICAL SUMMARY ──────────────────────────────
    def _bold_bullet(b):
        if ':' in b:
            pre, rest = b.split(':', 1)
            return f'<strong style="color:var(--dark)">{_esc(pre)}:</strong>{_esc(rest)}'
        return _esc(b)

    bullets_html = "".join(
        f'<div style="padding:5px 0;border-bottom:1px solid var(--rule);font-size:13px;color:var(--mid);line-height:1.7">{_bold_bullet(b)}</div>'
        for b in _cs_profile
    ) if _cs_profile else ""

    # Goals
    def _render_goal(i, g):
        label = g.replace("_", " ").title()
        tag = '<span style="background:var(--green-lt);color:var(--green);padding:1px 7px;border-radius:10px;font-size:9px;font-weight:700;margin-left:6px">Self-reported</span>'
        raw_tag = ""
        if _other_raw and _other_resolved and g.lower() == _other_resolved.lower():
            raw_tag = f'<span style="font-size:10px;color:var(--soft);font-style:italic;margin-left:6px">from: &ldquo;{_esc(_other_raw)}&rdquo;</span>'
        return f'<div style="padding:3px 0;color:var(--mid);font-size:13px;display:flex;align-items:baseline;gap:4px"><span style="color:var(--soft);font-size:11px">{i+1}.</span><span>{_esc(label)}</span>{tag}{raw_tag}</div>'
    goals_list = "".join(_render_goal(i, g) for i, g in enumerate(q_goals))

    inferred_goals_html = ""
    _inferred_start = len(q_goals) + 1
    for _inf_idx, (sig_key, sig_reason) in enumerate(_inferred_map.items()):
        sig_label = sig_key.replace("_", " ").title()
        reason_tag = f'<span style="font-size:10px;color:var(--soft);font-style:italic;margin-left:6px">{_esc(sig_reason)}</span>' if sig_reason else ""
        inferred_goals_html += f'<div style="padding:3px 0;font-size:13px;display:flex;align-items:baseline;gap:4px"><span style="color:var(--soft);font-size:11px">{_inferred_start + _inf_idx}.</span><span style="background:var(--blue-lt);color:var(--blue);padding:1px 7px;border-radius:10px;font-size:9px;font-weight:700">Inferred</span><span style="color:var(--mid)">{_esc(sig_label)}</span>{reason_tag}</div>'

    claims_list = "".join(f'<span class="claim-chip">{_esc(c.replace("_", " ").title())}</span>' for c in q_claims)

    # Coverage
    cov_html = f'<div style="margin-top:14px;background:var(--green-lt);border:1px solid var(--green);border-radius:8px;padding:10px 14px;font-size:12px;color:var(--green);font-weight:600">📋 Coverage: {q_cov_level} ({round(q_cov_pct)}%) — {_esc(q_coverage_data.get("summary",""))}</div>' if q_coverage_data else ""

    # Questionnaire narrative (moves here from Section 1)
    q_narrative_html = ""
    if q_narrative or inputs.get("questionnaire", {}).get("summary", ""):
        q_narrative_html = f'<div style="margin-top:20px;padding-top:16px;border-top:1px solid var(--rule);font-size:13px;color:var(--mid);line-height:1.8">{_esc(q_narrative or inputs.get("questionnaire",{}).get("summary",""))}</div>'

    # Medications block for Section 0
    _s0_meds_html = ""
    if _medications_list:
        _s0_med_rows = ""
        for _med in _medications_list:
            if isinstance(_med, dict):
                _med_name = _med.get("name", "")
                _med_dosage = _med.get("dosage", "")
                _med_how_long = _med.get("how_long", "")
                if not _med_name:
                    continue
                dosage_str = f'<span style="color:var(--mid);font-size:12px">{_esc(_med_dosage)}</span>' if _med_dosage else '<span style="color:var(--soft);font-size:12px">—</span>'
                duration_str = f'<span style="color:var(--mid);font-size:12px">{_esc(_med_how_long)}</span>' if _med_how_long else '<span style="color:var(--soft);font-size:12px">—</span>'
                _s0_med_rows += f'''<tr style="border-bottom:1px solid var(--rule)">
                  <td style="padding:6px 10px;font-weight:600;color:var(--dark);font-size:13px">💊 {_esc(_med_name)}</td>
                  <td style="padding:6px 10px">{dosage_str}</td>
                  <td style="padding:6px 10px">{duration_str}</td>
                </tr>'''
            elif isinstance(_med, str) and _med.strip():
                _s0_med_rows += f'''<tr style="border-bottom:1px solid var(--rule)">
                  <td style="padding:6px 10px;font-weight:600;color:var(--dark);font-size:13px">💊 {_esc(_med.strip())}</td>
                  <td style="padding:6px 10px"><span style="color:var(--soft);font-size:12px">—</span></td>
                  <td style="padding:6px 10px"><span style="color:var(--soft);font-size:12px">—</span></td>
                </tr>'''
        if _s0_med_rows:
            _s0_meds_html = f'''<div style="margin-top:14px">
              <div style="font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:var(--soft);margin-bottom:8px;font-weight:700">Medications</div>
              <div style="border:1px solid var(--rule);border-radius:8px;overflow:hidden">
                <table style="width:100%;border-collapse:collapse">
                  <thead style="background:var(--sand)"><tr>
                    <th style="padding:5px 10px;text-align:left;color:var(--soft);font-size:10px;letter-spacing:.1em;text-transform:uppercase">Name</th>
                    <th style="padding:5px 10px;text-align:left;color:var(--soft);font-size:10px;letter-spacing:.1em;text-transform:uppercase">Dosage</th>
                    <th style="padding:5px 10px;text-align:left;color:var(--soft);font-size:10px;letter-spacing:.1em;text-transform:uppercase">Duration</th>
                  </tr></thead>
                  <tbody style="background:var(--warm)">{_s0_med_rows}</tbody>
                </table>
              </div>
            </div>'''
    else:
        _s0_meds_html = '''<div style="margin-top:14px">
          <div style="font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:var(--soft);margin-bottom:8px;font-weight:700">Medications</div>
          <div style="font-size:13px;color:var(--soft);font-style:italic">None reported</div>
        </div>'''

    # Excluded substances block for Section 0 (right column, above goals)
    _exclusion_reasons = master.get("medication_rules", {}).get("exclusion_reasons", [])
    _s0_exclusions_html = ""
    if _exclusion_reasons:
        _excl_rows = ""
        for _excl in _exclusion_reasons:
            if not isinstance(_excl, dict):
                continue
            _excl_substance = _excl.get("substance", "")
            _excl_medication = _excl.get("medication", "")
            _excl_mechanism = _excl.get("mechanism", "")
            if not _excl_substance:
                continue
            _excl_rows += f'''<tr style="border-bottom:1px solid rgba(194,75,58,.15)">
              <td style="padding:6px 10px;font-weight:600;color:var(--red);font-size:12px">🚫 {_esc(_excl_substance)}</td>
              <td style="padding:6px 10px;font-size:11px;color:var(--mid)">{_esc(_excl_medication)}</td>
              <td style="padding:6px 10px;font-size:11px;color:var(--mid)">{_esc(_excl_mechanism)}</td>
            </tr>'''
        if _excl_rows:
            _s0_exclusions_html = f'''<div style="margin-bottom:16px">
              <div style="font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:var(--red);margin-bottom:8px;font-weight:700">⚠️ Possible Contradictions Between Client's Medication and Potential Supplements</div>
              <div style="border:2px solid rgba(194,75,58,.3);border-radius:8px;overflow:hidden;background:var(--red-lt)">
                <table style="width:100%;border-collapse:collapse">
                  <thead style="background:rgba(194,75,58,.1)"><tr>
                    <th style="padding:5px 10px;text-align:left;color:var(--red);font-size:10px;letter-spacing:.1em;text-transform:uppercase">Substance</th>
                    <th style="padding:5px 10px;text-align:left;color:var(--red);font-size:10px;letter-spacing:.1em;text-transform:uppercase">Medication</th>
                    <th style="padding:5px 10px;text-align:left;color:var(--red);font-size:10px;letter-spacing:.1em;text-transform:uppercase">Mechanism</th>
                  </tr></thead>
                  <tbody>{_excl_rows}</tbody>
                </table>
              </div>
            </div>'''

    section0_html = f'''<div style="padding:24px 56px 32px;background:var(--warm);border-bottom:1px solid var(--rule)">
      <div class="sec-label">Section 0 · Clinical Profile</div>
      <div style="font-family:'Playfair Display',serif;font-size:28px;font-weight:400;color:var(--dark);margin-bottom:16px">Patient Clinical Summary</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;align-items:start">
        <div>{bullets_html}{_s0_meds_html}</div>
        <div>
          {_s0_exclusions_html}
          <div style="font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:var(--soft);margin-bottom:8px">Health Goals</div>
          {goals_list}
          {inferred_goals_html}
          <div style="margin-top:14px">
            <div style="font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:var(--soft);margin-bottom:8px">Claims Triggered</div>
            <div>{claims_list}</div>
          </div>
          {cov_html}
        </div>
      </div>
      {q_narrative_html}
    </div>'''

    # ── SECTION 1: MICROBIOME & QUESTIONNAIRE ────────────────────────────
    guild_bars_html = _build_guild_bars(inputs["microbiome"]["guilds"])
    
    clr_html = ""
    clr_data = inputs["microbiome"].get("clr_ratios", {})
    for ratio_name in ["CUR", "FCR", "MDR", "PPR"]:
        val = clr_data.get(ratio_name, "")
        if not val:
            clr_html += f'<span class="clr-chip" style="opacity:.45">{ratio_name}: N/A</span>'
        else:
            clr_html += f'<span class="clr-chip">{ratio_name}: {val}</span>'

    # Formulation logic — simple bordered box (not blue)
    formulation_logic_html = ""
    if inputs["microbiome"].get("formulation_narrative"):
        formulation_logic_html = f'''<div style="margin-top:16px;background:var(--warm);border:1px solid var(--rule);padding:14px 18px;border-radius:10px;font-size:13px;color:var(--mid);line-height:1.7"><strong style="color:var(--dark)">Formulation Logic:</strong> {_esc(inputs["microbiome"]["formulation_narrative"])}</div>'''

    # Stats bar — horizontal chips, full width
    stat_items = [
        ("Sex / Age", f"{sex_val} · {age_val}"),
        ("Sensitivity", q_data.get("sensitivity_classification", _demo.get("sensitivity_classification", "?")).title() if isinstance(q_data.get("sensitivity_classification", _demo.get("sensitivity_classification", "?")), str) else "?"),
        ("Stress", f'{q_data.get("stress_level", _demo.get("stress_level","?"))} / 10'),
        ("Sleep", f'{q_data.get("sleep_quality", _demo.get("sleep_quality","?"))} / 10'),
        ("Bloating", f'{q_data.get("bloating_severity", _demo.get("bloating_severity","N/R"))} / 10'),
        ("Diet", diet_val),
    ]
    stats_bar_html = "".join(
        f'<div style="background:var(--warm);border:1px solid var(--rule);border-radius:10px;padding:10px 16px;display:flex;flex-direction:column;gap:2px">'
        f'<span style="font-size:9px;letter-spacing:.15em;text-transform:uppercase;color:var(--soft);font-weight:700">{k}</span>'
        f'<span style="font-size:13px;color:var(--dark);font-weight:600">{v}</span>'
        f'</div>'
        for k, v in stat_items
    )
    stats_bar_full = f'<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-bottom:24px">{stats_bar_html}</div>'

    # Executive summary boxes (4 structured sections from narrative report) — 2×2 grid
    exec_sum = inputs["microbiome"].get("executive_summary", {})
    exec_blocks = [
        ("Overall Pattern Classification", exec_sum.get("overall_pattern", "")),
        ("Dysbiosis-Associated Markers",   exec_sum.get("dysbiosis_markers", "")),
        ("Critical Finding",               exec_sum.get("critical_finding", "")),
        ("Health Implications",            exec_sum.get("health_implications", "")),
    ]
    exec_blocks_nonempty = [(t, b) for t, b in exec_blocks if b]
    if exec_blocks_nonempty:
        exec_items = ""
        for title, body in exec_blocks_nonempty:
            exec_items += f'''<div style="background:var(--warm);border:1px solid var(--rule);border-radius:10px;padding:16px 20px">
              <div style="font-size:13px;font-weight:600;color:var(--dark);margin-bottom:6px">{title}</div>
              <div style="font-size:13px;color:var(--mid);line-height:1.8">{_md_to_html(body)}</div>
            </div>'''
        exec_grid_html = f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:24px">{exec_items}</div>'
    else:
        exec_grid_html = f'<p style="font-size:13px;color:var(--mid);line-height:1.8;margin-bottom:20px">{_md_to_html(inputs["microbiome"].get("summary",""))}</p>'

    section1_html = f'''<div class="section sand">
  <div class="sec-label">Section 1 · Inputs</div>
  <div class="sec-title">Microbiome &amp; Questionnaire</div>
  <p class="sec-intro">The data used to drive every formulation decision below.</p>
  {stats_bar_full}
  {exec_grid_html}
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:32px;align-items:start">
    <div>
      <div style="font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--soft);margin-bottom:12px">Bacterial Ranges</div>
      <div class="guild-bars">{guild_bars_html}</div>
    </div>
    <div>
      <div style="font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--soft);margin-bottom:12px">Metabolic Ratios</div>
      <div style="display:flex;flex-direction:column;gap:8px">{clr_html}</div>
    </div>
  </div>
  {formulation_logic_html}
</div>'''

    # ── SECTION 2: DECISION CHAIN ────────────────────────────────────────
    steps_html = ""
    for step in steps:
        method_cls = "det" if step["method"] == "deterministic" else "llm"
        method_label = "Deterministic" if step["method"] == "deterministic" else "LLM"
        method_color = "var(--green)" if method_cls == "det" else "var(--purple)"
        method_bg = "var(--green-lt)" if method_cls == "det" else "var(--purple-lt)"

        components_html = ""
        if step.get("components"):
            if step.get("decision") == "Supplement Selection":
                # Build table with delivery grouping
                # Group by delivery
                delivery_groups = {}
                for ht in health_table:
                    dlv = ht.get("delivery", "other")
                    delivery_groups.setdefault(dlv, []).append(ht)
                
                # Build group labels from recipe — same source as Section 3 Final Formulation
                # This ensures Section 7 labels always match the actual delivery format and timing
                _GROUP_LABELS = _build_delivery_label_lookup(recipe)
                _GROUP_ORDER = ["probiotic capsule", "softgel", "sachet", "morning wellness capsule", "polyphenol capsule", "evening capsule", "magnesium capsule"]
                
                table_rows = ""
                for dlv_key in _GROUP_ORDER:
                    items = delivery_groups.get(dlv_key, [])
                    if not items:
                        continue
                    group_label = _GROUP_LABELS.get(dlv_key, dlv_key.title())
                    table_rows += f'<tr><td colspan="3" style="padding:10px 10px 4px;font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:var(--soft);font-weight:700;border-bottom:none">{group_label}</td></tr>'
                    for ht in items:
                        src_cls = ht.get("source", "").replace("_", "-")
                        src_label = ht.get("source", "").replace("_", " ").title()
                        hc = ht.get("health_claim", "")
                        _src_colors = {
                            "microbiome-primary": ("var(--green)", "var(--green-lt)"),
                            "microbiome-linked":  ("var(--blue)", "var(--blue-lt)"),
                            "questionnaire-only": ("var(--amber)", "var(--amber-lt)"),
                        }
                        sc, sbg = _src_colors.get(src_cls, ("var(--mid)", "var(--sand)"))
                        hc_badge = f'<span style="background:var(--purple-lt);color:var(--purple);padding:1px 7px;border-radius:10px;font-size:9px;margin-left:6px;font-weight:600">{_esc(hc)}</span>' if hc else ""
                        targets_text = _highlight_claims_in_text(ht["what_it_targets"], _all_claims_set)
                        table_rows += f'''<tr style="border-bottom:1px solid var(--rule)">
                          <td style="padding:6px 10px;font-weight:600;color:var(--dark);font-size:12px">{_esc(ht["component"])}{hc_badge}</td>
                          <td style="padding:6px 10px;color:var(--mid);font-size:11px">{targets_text}</td>
                          <td style="padding:6px 10px;white-space:nowrap"><span style="background:{sbg};color:{sc};padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600">{src_label}</span></td>
                        </tr>'''
                # Handle any ungrouped
                for dlv_key, items in delivery_groups.items():
                    if dlv_key not in _GROUP_ORDER and items:
                        table_rows += f'<tr><td colspan="3" style="padding:10px 10px 4px;font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:var(--soft);font-weight:700;border-bottom:none">{dlv_key.title()}</td></tr>'
                        for ht in items:
                            src_cls = ht.get("source", "").replace("_", "-")
                            src_label = ht.get("source", "").replace("_", " ").title()
                            _src_colors2 = {"microbiome-primary": ("var(--green)", "var(--green-lt)"), "microbiome-linked": ("var(--blue)", "var(--blue-lt)"), "questionnaire-only": ("var(--amber)", "var(--amber-lt)")}
                            sc2, sbg2 = _src_colors2.get(src_cls, ("var(--mid)", "var(--sand)"))
                            targets_text2 = _highlight_claims_in_text(ht["what_it_targets"], _all_claims_set)
                            table_rows += f'<tr style="border-bottom:1px solid var(--rule)"><td style="padding:6px 10px;font-weight:600;color:var(--dark);font-size:12px">{_esc(ht["component"])}</td><td style="padding:6px 10px;color:var(--mid);font-size:11px">{targets_text2}</td><td style="padding:6px 10px;white-space:nowrap"><span style="background:{sbg2};color:{sc2};padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600">{src_label}</span></td></tr>'

                components_html = f'''<div style="margin-top:12px;border:1px solid var(--rule);border-radius:8px;overflow:hidden">
                  <table style="width:100%;border-collapse:collapse">
                    <thead style="background:var(--sand)"><tr>
                      <th style="padding:6px 10px;text-align:left;color:var(--soft);font-size:10px;letter-spacing:.15em;text-transform:uppercase;width:40%">Component</th>
                      <th style="padding:6px 10px;text-align:left;color:var(--soft);font-size:10px;letter-spacing:.15em;text-transform:uppercase;width:40%">What It Targets</th>
                      <th style="padding:6px 10px;text-align:left;color:var(--soft);font-size:10px;letter-spacing:.15em;text-transform:uppercase;width:20%">Source</th>
                    </tr></thead>
                    <tbody style="background:var(--warm)">{table_rows}</tbody>
                  </table>
                </div>'''
            elif step.get("decision") != "Prebiotic Design":
                # Prebiotic Design renders its components via extra_detail below;
                # rendering step["components"] here would leak raw dict reprs.
                items = "".join(f'<div style="font-size:12px;color:var(--mid);padding:2px 0;border-bottom:1px solid var(--rule)">→ {_esc(c)}</div>' for c in step["components"])
                components_html = f'<div style="margin-top:10px;background:var(--sand);border-radius:6px;padding:10px 12px">{items}</div>'

        extra_detail = ""
        if step.get("decision") == "Prebiotic Design":
            prebiotic_design = master.get("decisions", {}).get("prebiotic_design", {})
            pb_data = prebiotic_design.get("prebiotics", [])
            csa_data = prebiotic_design.get("condition_specific_additions", [])
            _fodmap_span = '<span style="color:var(--amber);font-size:10px">[FODMAP]</span>'
            if pb_data or csa_data:
                pb_items = ""
                for p in pb_data:
                    pb_items += f'<div style="font-size:12px;color:var(--mid);padding:2px 0">→ {_esc(p["substance"])}: {p["dose_g"]}g {_fodmap_span if p.get("fodmap") else ""}</div>'
                for c in csa_data:
                    dose_str = _esc(str(c.get("dose_g_or_mg", "")))
                    condition = _esc(c.get("condition", "condition-specific"))
                    rationale = _esc(c.get("rationale", ""))
                    csa_label = f'<span style="background:var(--blue-lt);color:var(--blue);padding:1px 6px;border-radius:8px;font-size:9px;font-weight:700;margin-left:4px">sensitivity-adjusted</span>'
                    pb_items += f'<div style="font-size:12px;color:var(--mid);padding:2px 0">→ {_esc(c["substance"])}: {dose_str} {csa_label} <span style="font-size:10px;color:var(--soft)">{condition}</span></div>'
                extra_detail = f'<div style="margin-top:8px;background:var(--sand);border-radius:6px;padding:10px 12px">{pb_items}</div>'
            # Overrides — rendered here (extra_detail) only; overrides_html below is
            # suppressed for Prebiotic Design to avoid showing them twice.
            # Strategy is intentionally omitted — it's identical to step["reasoning"]
            # already displayed in the Reasoning: field above.
            overrides = prebiotic_design.get("overrides_applied", [])
            if overrides:
                for ov in overrides:
                    extra_detail += f'<div style="font-size:12px;color:var(--amber)">⚠️ Override: {_esc(ov)}</div>'
            # Substrate necessity guard (v2.1)
            sg = prebiotic_design.get("substrate_guard", {})
            if sg:
                if sg.get("applied"):
                    guard_items = '<div style="font-size:12px;font-weight:600;color:var(--dark)">🛡️ Substrate Guard: Applied</div>'
                    for corr in sg.get("corrections", []):
                        guard_items += f'<div style="font-size:11px;color:var(--mid)">→ {_esc(corr.get("substance",""))}: {corr.get("from_g",0)}g → {corr.get("to_g",0)}g (P{corr.get("priority","?")}, {_esc(corr.get("action",""))})</div>'
                    for rb in sg.get("rebalance_log", []):
                        guard_items += f'<div style="font-size:11px;color:var(--mid)">→ {_esc(rb.get("substance",""))}: {rb.get("from_g",0)}g → {rb.get("to_g",0)}g (reduced for headroom)</div>'
                    for warn in sg.get("warnings", []):
                        guard_items += f'<div style="font-size:11px;color:var(--amber)">⚠️ {_esc(warn)}</div>'
                    ceil = sg.get("effective_ceiling_g")
                    tol = sg.get("tolerance_pct", 0)
                    sg_final = sg.get("final_total_g")
                    respected = sg.get("ceiling_respected", True)
                    if ceil and sg_final:
                        icon = "✅" if respected else "⚠️"
                        guard_items += f'<div style="font-size:11px;color:var(--mid)">{icon} Ceiling: {sg_final}g {"≤" if respected else ">"} {ceil}g (max + {tol}% tolerance)</div>'
                    extra_detail += f'<div style="margin-top:8px;background:#f0f4f0;border-radius:6px;padding:8px 10px">{guard_items}</div>'
                elif sg.get("reason"):
                    sibo_src = sg.get("sibo_source", "")
                    src_tag = f' (source: {sibo_src})' if sibo_src else ''
                    extra_detail += f'<div style="margin-top:8px;font-size:12px;color:var(--mid)">🛡️ Substrate Guard: Skipped — {_esc(sg["reason"])}{src_tag}</div>'

        # Mix selection extra detail
        mix_extra = ""
        if step.get("decision") == "Mix Selection":
            mix_data = master.get("decisions", {}).get("mix_selection", {})
            rule_applied = step.get("rule_applied", mix_data.get("clr_context", ""))
            confidence = step.get("confidence", mix_data.get("confidence", ""))
            alternative = step.get("alternative", mix_data.get("alternative_considered", ""))
            strains = mix_data.get("strains", [])
            
            mix_lines = ""
            if rule_applied:
                mix_lines += f'<div style="font-size:12px;color:var(--mid);padding:2px 0"><strong>Rule:</strong> {_esc(rule_applied)}</div>'
            if confidence:
                mix_lines += f'<div style="font-size:12px;color:var(--mid);padding:2px 0"><strong>Confidence:</strong> {_esc(confidence)}</div>'
            if alternative:
                mix_lines += f'<div style="font-size:12px;color:var(--mid);padding:2px 0"><strong>Alternative considered:</strong> {_esc(alternative)}</div>'
            if strains:
                mix_lines += '<div style="font-size:12px;color:var(--mid);padding:4px 0 0"><strong>Component Decision Lines:</strong></div>'
                for s in strains:
                    is_lp = "LP815" in s.get("name", "")
                    role_tag = "stress/gut-brain" if is_lp else _esc(mix_data.get("mix_name", ""))
                    mix_lines += f'<div style="font-size:11px;color:var(--mid);padding:1px 0;padding-left:12px">→ {_esc(s.get("name","?"))} {s.get("cfu_billions","?")}B | {role_tag}</div>'
            if mix_lines:
                mix_extra = f'<div style="margin-top:8px;background:var(--sand);border-radius:6px;padding:10px 12px">{mix_lines}</div>'

        # Sensitivity extra
        sens_extra = ""
        if step.get("decision") == "Sensitivity Classification":
            sens_data = master.get("decisions", {}).get("rule_outputs", {}).get("sensitivity", {})
            cls = sens_data.get("classification", "?")
            sens_extra = f'<div style="margin-top:6px;font-size:12px;color:var(--mid)"><strong>Strategy:</strong> {cls.title()} sensitivity — prebiotic max {sens_data.get("max_prebiotic_g", "?")}g</div>'

        # LP815 extra from mix strains
        lp815_extra = ""
        if step.get("decision") == "LP815 Enhancement":
            mix_data2 = master.get("decisions", {}).get("mix_selection", {})
            for s2 in mix_data2.get("strains", []):
                if "LP815" in s2.get("name", ""):
                    lp815_extra = f'<div style="margin-top:6px;font-size:12px;color:var(--mid)">→ {_esc(s2.get("name","?"))} {s2.get("cfu_billions","?")}B CFU | stress/gut-brain | add-on (separate from base 50B)</div>'

        overrides_html = ""
        # Prebiotic Design overrides are already rendered inside extra_detail above —
        # skip here to avoid showing them twice.
        if step.get("overrides") and step.get("decision") != "Prebiotic Design":
            o_items = "".join(f'<div style="font-size:12px;color:var(--amber)">⚠️ {_esc(o)}</div>' for o in step["overrides"])
            overrides_html = f'<div style="margin-top:8px">{o_items}</div>'

        # Input text — clean up array formatting but NO keyword highlighting
        # (highlighting in Input causes visual noise; only highlight in Targets column)
        raw_input = step.get("input", "")
        # Parse Python-style lists to clean comma-delimited text
        def _clean_input_lists(txt):
            """Convert ['item1', 'item2'] to item1, item2 and normalize underscores."""
            import ast
            def _replace_list(m):
                try:
                    items = ast.literal_eval(m.group(0))
                    if isinstance(items, list):
                        cleaned = [str(i).replace("_", " ").title() for i in items]
                        # Deduplicate (some claims appear as both Title Case and snake_case)
                        seen = set()
                        deduped = []
                        for c in cleaned:
                            key = c.lower().strip()
                            if key not in seen:
                                seen.add(key)
                                deduped.append(c)
                        return ", ".join(deduped)
                except (ValueError, SyntaxError):
                    pass
                return m.group(0)
            return re.sub(r'\[.*?\]', _replace_list, txt)
        input_text = _esc(_clean_input_lists(raw_input))

        steps_html += f'''<div class="step-card">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
            <div style="width:30px;height:30px;border-radius:50%;background:var(--dark);color:white;display:flex;align-items:center;justify-content:center;font-family:'Playfair Display',serif;font-size:15px;flex-shrink:0">{step["step"]}</div>
            <div style="font-family:'Playfair Display',serif;font-size:17px;color:var(--dark);flex:1">{_esc(step["decision"])}</div>
            <span style="background:{method_bg};color:{method_color};padding:3px 10px;border-radius:20px;font-size:11px;font-weight:600;letter-spacing:.05em">{method_label}</span>
          </div>
          <div style="font-size:13px;line-height:1.65">
            <div style="color:var(--mid);margin-bottom:5px"><strong style="color:var(--dark)">Input:</strong> {input_text}</div>
            <div style="color:var(--green);font-weight:600;margin-bottom:5px"><strong style="color:var(--dark)">Result:</strong> {_esc(step["result"])}</div>
            <div style="color:var(--mid);font-style:italic"><strong style="color:var(--dark);font-style:normal">Reasoning:</strong> {_esc(step.get("reasoning",""))}</div>
            {sens_extra}{mix_extra}{lp815_extra}{components_html}{extra_detail}{overrides_html}
          </div>
        </div>'''

    section2_html = f'''<div class="section">
  <div class="sec-label">Section 2 · Decision Chain</div>
  <div class="sec-title">{len(steps)} Formulation Decisions</div>
  <p class="sec-intro">Every decision — what data was used, which rule or LLM call was applied, and what it produced.</p>
  {steps_html}
</div>'''

    # ── SECTION 3: FINAL FORMULATION ─────────────────────────────────────
    recipe_units = recipe.get("units", [])
    
    # Group morning wellness + polyphenol capsules
    morning_cap_units = [u for u in recipe_units if "Morning Wellness" in u.get("label", "") or ("morning" in u.get("timing", "").lower() and "capsule" in u.get("format", {}).get("type", "").lower() and "probiotic" not in u.get("label", "").lower())]
    evening_cap_units = [u for u in recipe_units if "Evening" in u.get("label", "") or "Magnesium" in u.get("label", "")]
    other_units = [u for u in recipe_units if u not in morning_cap_units and u not in evening_cap_units]
    
    delivery_html = ""
    # Other units first (probiotic, softgel, jar)
    for unit in other_units:
        delivery_html += _build_unit_card(unit)
    
    # Morning Wellness Capsules grouped
    if morning_cap_units:
        inner_cards = ""
        total_morning_mg = 0
        cap_counter = 0
        for mu in morning_cap_units:
            cap_layout = mu.get("capsule_layout", [])
            if cap_layout:
                for cap in cap_layout:
                    cap_counter += 1
                    fill = cap.get("fill_mg", 0)
                    total_morning_mg += fill
                    comps = cap.get("components", [])
                    comp_pills = " · ".join(f'<strong>{c.get("substance","?")}</strong> {c.get("dose", str(c.get("dose_mg","?"))+"mg")}' for c in comps)
                    inner_cards += f'<div style="padding:10px 14px;border:1px solid var(--rule);border-radius:8px;margin-bottom:6px"><span style="background:var(--purple-lt);color:var(--purple);padding:2px 10px;border-radius:20px;font-size:10px;font-weight:700;margin-right:8px">Capsule {cap_counter} · {fill}mg</span><span style="font-size:12px;color:var(--mid)">{comp_pills}</span></div>'
            else:
                # Single capsule unit (e.g. polyphenol)
                cap_counter += 1
                fill = mu.get("total_weight_mg", mu.get("fill_weight_mg", 0))
                total_morning_mg += fill
                ings = mu.get("ingredients", [])
                comp_pills = " · ".join(f'<strong>{ing.get("component","?")}</strong> {ing.get("amount_mg", ing.get("amount","?"))}{"mg" if ing.get("amount_mg") else ""}' for ing in ings)
                timing_note = ""
                if "with food" in mu.get("timing", ""):
                    timing_note = ' <span style="font-size:10px;color:var(--amber);font-weight:600">⚠ with food</span>'
                inner_cards += f'<div style="padding:10px 14px;border:1px solid var(--rule);border-radius:8px;margin-bottom:6px"><span style="background:var(--purple-lt);color:var(--purple);padding:2px 10px;border-radius:20px;font-size:10px;font-weight:700;margin-right:8px">Capsule {cap_counter} · {fill}mg</span><span style="font-size:12px;color:var(--mid)">{comp_pills}</span>{timing_note}</div>'
        
        total_qty = sum(mu.get("quantity", 1) for mu in morning_cap_units)
        delivery_html += f'''<div class="supp-unit">
          <div class="supp-header">
            <div class="supp-num-block" style="background:#6B5EA8">☀</div>
            <div class="supp-head-text">
              <div class="supp-unit-name">Morning Wellness Capsules</div>
              <div class="supp-unit-when">🌅 {total_qty}× morning</div>
            </div>
            <div class="supp-unit-tag" style="color:var(--amber);font-weight:600">{total_morning_mg}mg</div>
          </div>
          <div style="padding:14px 18px 18px">{inner_cards}</div>
        </div>'''

    # Evening Wellness Capsules grouped
    if evening_cap_units:
        inner_cards = ""
        total_evening_mg = 0
        cap_counter = 0
        for eu in evening_cap_units:
            cap_layout = eu.get("capsule_layout", [])
            if cap_layout:
                for cap in cap_layout:
                    cap_counter += 1
                    fill = cap.get("fill_mg", 0)
                    total_evening_mg += fill
                    comps = cap.get("components", [])
                    comp_pills = " · ".join(f'<strong>{c.get("substance","?")}</strong> {c.get("dose_mg","?")}mg' for c in comps)
                    inner_cards += f'<div style="padding:10px 14px;border:1px solid var(--rule);border-radius:8px;margin-bottom:6px"><span style="background:var(--purple-lt);color:var(--purple);padding:2px 10px;border-radius:20px;font-size:10px;font-weight:700;margin-right:8px">Capsule {cap_counter} · {fill}mg</span><span style="font-size:12px;color:var(--mid)">{comp_pills}</span></div>'
            elif eu.get("ingredients_per_unit"):
                # Mg capsule
                cap_counter += 1
                qty = eu.get("quantity", 1)
                per_unit = eu["ingredients_per_unit"]
                per_pills = " · ".join(f'<strong>{ing.get("component","?")}</strong> ({ing.get("elemental_mg","?")}mg elemental)' for ing in per_unit)
                daily_totals = eu.get("daily_totals", {})
                elem = daily_totals.get("elemental_mg_mg", 0)
                bisg = daily_totals.get("mg_bisglycinate_mg", 0)
                total_evening_mg += bisg
                inner_cards += f'<div style="padding:10px 14px;border:1px solid var(--rule);border-radius:8px;margin-bottom:6px"><span style="background:var(--purple-lt);color:var(--purple);padding:2px 10px;border-radius:20px;font-size:10px;font-weight:700;margin-right:8px">{qty}× Capsules · {bisg}mg</span><span style="font-size:12px;color:var(--mid)">{per_pills} — {elem}mg elemental total</span></div>'
            else:
                cap_counter += 1
                fill = eu.get("total_weight_mg", eu.get("fill_weight_mg", 0))
                total_evening_mg += fill
                ings = eu.get("ingredients", [])
                comp_pills = " · ".join(f'<strong>{ing.get("component","?")}</strong> {ing.get("amount_mg","?")}mg' for ing in ings)
                inner_cards += f'<div style="padding:10px 14px;border:1px solid var(--rule);border-radius:8px;margin-bottom:6px"><span style="background:var(--purple-lt);color:var(--purple);padding:2px 10px;border-radius:20px;font-size:10px;font-weight:700;margin-right:8px">Capsule {cap_counter} · {fill}mg</span><span style="font-size:12px;color:var(--mid)">{comp_pills}</span></div>'

        total_eve_qty = sum(eu.get("quantity", 1) for eu in evening_cap_units)
        delivery_html += f'''<div class="supp-unit">
          <div class="supp-header">
            <div class="supp-num-block" style="background:#C24B3A">🌙</div>
            <div class="supp-head-text">
              <div class="supp-unit-name">Evening Wellness Capsules</div>
              <div class="supp-unit-when">🌙 {total_eve_qty}× evening (30-60 min before bed)</div>
            </div>
            <div class="supp-unit-tag" style="color:var(--purple);font-weight:600">{total_evening_mg}mg</div>
          </div>
          <div style="padding:14px 18px 18px">{inner_cards}</div>
        </div>'''

    section3_html = f'''<div class="section sand">
  <div class="sec-label">Section 3 · Output</div>
  <div class="sec-title">Final Formulation</div>
  <p class="sec-intro">The manufacturing recipe produced by the pipeline.</p>
  <div>{delivery_html}</div>
  <div style="margin-top:8px;font-size:14px;color:var(--mid)">
    Total: <strong style="color:var(--dark)">{final["total_units"]} units</strong> · 
    <strong style="color:var(--dark)">{final["total_weight_g"]}g</strong> · 
    Validation: <strong style="color:var(--green)">{final["validation"]}</strong>
  </div>
</div>'''

    # ── SECTION 4: HEALTH AXES & ATTRIBUTION ─────────────────────────────
    axes_html = ""
    for ax in axes:
        confirmed = "CONFIRMED" in ax["questionnaire_verification"]
        ax_color = "var(--green)" if confirmed else "var(--amber)"
        axes_html += f'''<div style="background:var(--warm);border:1px solid var(--rule);border-radius:10px;padding:14px 18px;margin-bottom:10px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
            <span style="width:8px;height:8px;border-radius:50%;background:{ax_color};flex-shrink:0"></span>
            <span style="font-weight:600;color:var(--dark)">{_esc(ax["axis"])}</span>
            <span style="font-size:10px;color:{ax_color};font-weight:600">{"CONFIRMED" if confirmed else "PREDICTED"}</span>
          </div>
          <div style="font-size:12px;color:var(--mid);margin-bottom:4px"><strong>Microbiome pattern:</strong> {_esc(ax["microbiome_pattern"])}</div>
          <div style="font-size:12px;color:var(--mid);margin-bottom:4px"><strong>Expected manifestation:</strong> {_esc(ax["predicted_manifestations"])}</div>
          <div style="font-size:12px;color:var(--mid)"><strong>Questionnaire:</strong> {_esc(ax["questionnaire_verification"])} — {_esc(ax["severity"])}</div>
        </div>'''

    section4_html = f'''<div class="section">
  <div class="sec-label">Section 4 · Evidence</div>
  <div class="sec-title">Health Axes &amp; Source Distribution</div>
  <p class="sec-intro">Health axes show how microbiome patterns connect to specific health outcomes. Each axis represents a pathway where gut bacteria influence a body system — confirmed by questionnaire data or predicted from microbiome patterns alone. Source distribution shows what proportion of the formulation is driven by microbiome data vs questionnaire goals.</p>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:32px;align-items:start">
    <div>
      <div style="font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--soft);margin-bottom:12px">Health Axis Predictions</div>
      <div style="font-size:12px;color:var(--mid);margin-bottom:12px">Each axis below represents a microbiome-to-health pathway. <strong style="color:var(--green)">Confirmed</strong> axes match questionnaire symptoms. <strong style="color:var(--amber)">Predicted</strong> axes are subclinical patterns the formulation addresses preventively.</div>
      {axes_html}
    </div>
    <div>
      <div style="font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--soft);margin-bottom:12px">Source Distribution</div>
      <div style="font-size:12px;color:var(--mid);margin-bottom:12px">Shows where each component's selection decision originated — microbiome analysis, questionnaire goals, or both. Higher microbiome-informed % means more of the formulation is driven by objective gut data.</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div style="background:var(--warm);border:1px solid var(--rule);border-radius:10px;padding:16px;text-align:center"><div style="font-family:'Playfair Display',serif;font-size:28px;color:var(--green)">{source["microbiome_primary"]}</div><div style="font-size:11px;color:var(--mid);margin-top:4px">Microbiome Primary</div></div>
        <div style="background:var(--warm);border:1px solid var(--rule);border-radius:10px;padding:16px;text-align:center"><div style="font-family:'Playfair Display',serif;font-size:28px;color:var(--blue)">{source["microbiome_linked"]}</div><div style="font-size:11px;color:var(--mid);margin-top:4px">Microbiome Linked</div></div>
        <div style="background:var(--warm);border:1px solid var(--rule);border-radius:10px;padding:16px;text-align:center"><div style="font-family:'Playfair Display',serif;font-size:28px;color:var(--amber)">{source["questionnaire_only"]}</div><div style="font-size:11px;color:var(--mid);margin-top:4px">Questionnaire Only</div></div>
        <div style="background:var(--warm);border:1px solid var(--rule);border-radius:10px;padding:16px;text-align:center"><div style="font-family:'Playfair Display',serif;font-size:28px;color:var(--soft)">{source["fixed_component"]}</div><div style="font-size:11px;color:var(--mid);margin-top:4px">Fixed Component</div></div>
      </div>
      <div style="margin-top:16px;background:var(--warm);border:1px solid var(--rule);border-radius:10px;padding:12px 16px;font-size:13px;color:var(--mid)">
        <strong style="color:var(--dark)">{source["microbiome_informed_pct"]}% Microbiome-Informed</strong> — Components driven by microbiome data or linked to microbiome findings.
      </div>
    </div>
  </div>
</div>'''

    # ── SECTION 5: MIX SELECTION RATIONALE ───────────────────────────────
    eco_html = ""
    if eco_rationale and eco_rationale.get("selected_rationale"):
        eco_blocks = [
            ("Why this mix was selected", eco_rationale.get("selected_rationale", "")),
            ("Alternative mix considered", eco_rationale.get("alternative_analysis", "")),
            ("Combined strategy assessment", eco_rationale.get("combined_assessment", "")),
            ("Recommendation", eco_rationale.get("recommendation", "")),
        ]
        eco_items = ""
        for title, body in eco_blocks:
            if body:
                eco_items += f'''<div style="background:var(--warm);border:1px solid var(--rule);border-radius:10px;padding:20px 24px;margin-bottom:14px">
                  <div style="font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--soft);margin-bottom:8px;font-weight:700">{title}</div>
                  <div style="font-size:14px;color:var(--mid);line-height:1.8">{_esc(body)}</div>
                </div>'''
        eco_html = f'''<div class="section">
          <div class="sec-label">Ecological Analysis</div>
          <div class="sec-title" style="font-size:28px;margin-bottom:8px">Mix Selection Rationale</div>
          <p class="sec-intro">Scientific reasoning behind the chosen synbiotic mix and alternative strategies considered.</p>
          {eco_items}
        </div>'''

    # ── FOOTER ───────────────────────────────────────────────────────────
    footer_html = f'''<div class="footer">
  <div class="footer-brand">NB1 Health</div>
  <div>Scientific Board Review · For internal use only</div>
  <div>{trace.get("generated_at","")[:10]}</div>
</div>'''

    # ── ASSEMBLE ─────────────────────────────────────────────────────────
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Decision Trace — {sample_id}</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,500;1,400&family=Nunito:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>{BOARD_CSS}</style>
</head>
<body>
{cover_html}
{flags_html}
{med_override_html}
{section0_html}
{section1_html}
{section2_html}
{section3_html}
{section4_html}
{eco_html}
{footer_html}
</body>
</html>'''

    return html


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def generate_dashboards(sample_id: str, output_dir: str = None, sample_dir: str = None):
    """Generate the scientific board decision trace dashboard for a sample.

    The client-facing supplement guide (supplement_guide_{id}.html) is NOT
    generated here — it is a separate deliverable produced outside the
    formulation pipeline. Call build_client_dashboard() directly if needed.
    """
    if output_dir is None:
        output_dir = str(Path(__file__).parent / "output")
    
    if sample_dir:
        html_dir = Path(sample_dir) / "reports" / "reports_html"
    else:
        html_dir = Path(output_dir)
    html_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Building board dashboard for {sample_id}...")
    board_html = build_board_dashboard(sample_id, output_dir)
    board_path = html_dir / f"formulation_decision_trace_{sample_id}.html"
    with open(board_path, 'w', encoding='utf-8') as f:
        f.write(board_html)
    print(f"  📊 Board: {board_path}")
    
    return board_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate formulation dashboards")
    parser.add_argument("--sample-id", required=True, help="Sample ID")
    parser.add_argument("--output-dir", help="Output directory with JSON files")
    args = parser.parse_args()
    
    generate_dashboards(args.sample_id, args.output_dir)
