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
from pathlib import Path
from datetime import datetime

import sys as _sys
_sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
from formatting import format_dose as _format_dose


def _load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


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
        # Unit weight
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
    
    # Build axes HTML
    axes_html = ""
    for ax in axes:
        status_class = "confirmed" if "CONFIRMED" in ax["questionnaire_verification"] else "predicted"
        axes_html += f'''
        <div class="axis-card {status_class}">
            <div class="axis-name">{ax["axis"]}</div>
            <div class="axis-detail">
                <strong>Pattern:</strong> {ax["microbiome_pattern"]}
            </div>
            <div class="axis-detail">
                <strong>Verification:</strong> {ax["questionnaire_verification"]}
            </div>
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
.health-table tr:hover {{ background: #f9fafb; }}
.component-name {{ font-weight: 600; min-width: 200px; }}
.source-badge {{ padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; white-space: nowrap; }}
.microbiome-primary {{ background: #d4edda; color: #155724; }}
.microbiome-linked {{ background: #cce5ff; color: #004085; }}
.questionnaire-only {{ background: #fff3cd; color: #856404; }}
.fixed-component {{ background: #e2e3e5; color: #383d41; }}
.badge {{ padding: 2px 6px; border-radius: 3px; font-size: 11px; }}
.badge-prebiotic {{ background: #e8f5e9; color: #2e7d32; }}
.badge-vitamin_mineral {{ background: #e3f2fd; color: #1565c0; }}
.badge-supplement {{ background: #fff8e1; color: #f57f17; }}
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
    {'<div class="section" style="background:' + ('#fff3cd' if q_coverage.get('coverage_level') in ('LOW','MINIMAL') else '#d4edda' if q_coverage.get('coverage_level') == 'GOOD' else '#cce5ff') + ';border-left:4px solid ' + ('#856404' if q_coverage.get('coverage_level') in ('LOW','MINIMAL') else '#155724' if q_coverage.get('coverage_level') == 'GOOD' else '#004085') + ';padding:16px 20px"><strong>📋 Questionnaire Coverage: ' + q_coverage.get('coverage_level','?') + ' (' + str(round(q_coverage.get('completion_pct',0))) + '%)</strong> — ' + q_coverage.get('summary','') + '</div>' if q_coverage else ''}
    <div class="section">
        <h2>📦 Daily Protocol</h2>
        {unit_cards}
    </div>
    
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
    
    <div class="section">
        <h2>🔬 Health Axis Predictions</h2>
        {axes_html}
    </div>
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

def build_board_dashboard(sample_id: str, output_dir: str) -> str:
    """Build scientific board decision trace dashboard HTML."""
    
    output_dir = Path(output_dir)
    trace = _load_json(output_dir / f"decision_trace_{sample_id}.json")
    rationale = _load_json(output_dir / f"component_rationale_{sample_id}.json")
    master = _load_json(output_dir / f"formulation_master_{sample_id}.json")
    recipe = _load_json(output_dir / f"manufacturing_recipe_{sample_id}.json")
    
    # Import dynamic label helper
    from platform_mapping import _evening_capsule_label
    
    inputs = trace["inputs"]
    steps = trace["decision_chain"]
    final = trace["final_formulation"]
    evidence = trace.get("evidence_sources", {})
    axes = rationale["health_axis_predictions"]
    health_table = rationale.get("how_this_addresses_your_health", [])
    source = rationale["source_attribution"]
    eco_rationale = trace.get("ecological_rationale", {})
    q_data = master.get("input_summary", {}).get("questionnaire_driven", {})
    q_coverage_data = master.get("questionnaire_coverage", {})
    q_narrative = master.get("input_narratives", {}).get("questionnaire_narrative", "")
    
    # Build guild table — full data with abundance, CLR, priority, and color coding
    # Color scheme:
    #   Green: guild is healthy (within range, no CLR issues)
    #   Amber: guild has a notable condition (below range for contextual guilds,
    #          or within range but CLR-suppressed for beneficial guilds)
    #   Red:   guild needs intervention (below range for beneficial, above for contextual)
    CONTEXTUAL_GUILDS = {"proteolytic", "mucin_degraders", "proteolytic guild", "proteolytic dysbiosis guild", "mucin degraders"}
    
    # Use canonical priority_interventions from master JSON if available (SINGLE SOURCE OF TRUTH)
    priority_interventions = master.get("priority_interventions", [])
    _prio_color_map = {}  # guild_name.lower() → color from canonical ordering
    for pi in priority_interventions:
        _prio_color_map[pi.get("guild_name", "").lower()] = pi.get("color", "green")
        _prio_color_map[pi.get("guild_key", "").lower()] = pi.get("color", "green")
    
    guild_table_rows = ""
    for g in inputs["microbiome"]["guilds"]:
        name = g.get("name", "?")
        status = g.get("status", "?")
        pct = g.get("abundance_pct", "?")
        prio = g.get("priority_level", "")
        guild_clr = g.get("clr")
        
        is_contextual = name.lower() in CONTEXTUAL_GUILDS
        
        # Color logic v3 — PRIORITY-DRIVEN (from canonical priority_interventions)
        # This ensures colors match across ALL outputs (decision trace, narrative, health report)
        # CRITICAL / 1A → red, 1B → amber, Monitor → green
        # 4-color scheme: CRITICAL=red, 1A=orange, 1B=amber, Monitor=teal
        _COLOR_TO_HEX = {
            "red": ("#e74c3c", "#e74c3c33"),
            "orange": ("#e67e22", "#e67e2233"),
            "amber": ("#f39c12", "#f39c1233"),
            "teal": ("#2ecc71", "#2ecc7133"),
        }
        canonical_color = _prio_color_map.get(name.lower(), None)
        if canonical_color and canonical_color in _COLOR_TO_HEX:
            status_color, row_color = _COLOR_TO_HEX[canonical_color]
        else:
            # Fallback: derive from priority_level string if canonical not available
            prio_upper = str(prio).upper()
            if "CRITICAL" in prio_upper:
                status_color, row_color = _COLOR_TO_HEX["red"]
            elif "1A" in prio_upper:
                status_color, row_color = _COLOR_TO_HEX["orange"]
            elif "1B" in prio_upper:
                status_color, row_color = _COLOR_TO_HEX["amber"]
            else:
                status_color, row_color = _COLOR_TO_HEX["teal"]
        
        # Priority badge — 4-color scheme matching canonical system
        _PRIO_BADGE_COLORS = {"CRITICAL": "#e74c3c", "1A": "#e67e22", "1B": "#f39c12", "Monitor": "#2ecc71"}
        prio_badge = ""
        if prio and prio != "Monitor":
            prio_color = _PRIO_BADGE_COLORS.get(prio, "#f39c12")
            prio_badge = f'<span style="background:{prio_color}33;color:{prio_color};padding:1px 5px;border-radius:3px;font-size:10px;font-weight:700">{prio}</span>'
        elif prio == "Monitor":
            prio_badge = f'<span style="color:#2ecc71;font-size:10px">Monitor</span>'
        
        # CLR display
        clr_str = ""
        if guild_clr is not None and guild_clr != "None":
            try:
                clr_val = float(guild_clr)
                clr_color = "#1dd1a1" if (clr_val > 0.3 and not is_contextual) or (clr_val < -0.3 and is_contextual) else ("#ff6b6b" if (clr_val < -0.3 and not is_contextual) or (clr_val > 0.3 and is_contextual) else "#888")
                clr_str = f'<span style="color:{clr_color};font-weight:600">{clr_val:+.2f}</span>'
            except (ValueError, TypeError):
                clr_str = '<span style="color:#555">N/A</span>'
        else:
            clr_str = '<span style="color:#555">N/A</span>'
        
        pct_str = f"{pct:.1f}%" if isinstance(pct, (int, float)) else str(pct)
        
        guild_table_rows += f'''<tr style="background:{row_color}">
            <td style="padding:5px 8px;color:{status_color};font-weight:600;font-size:12px">{name}</td>
            <td style="padding:5px 8px;color:#ccc;font-size:12px;text-align:right">{pct_str}</td>
            <td style="padding:5px 8px;color:{status_color};font-size:11px">{status}</td>
            <td style="padding:5px 8px;text-align:center">{prio_badge}</td>
            <td style="padding:5px 8px;text-align:center;font-size:12px">{clr_str}</td>
        </tr>'''
    
    guild_html = f'''<table style="width:100%;border-collapse:collapse;margin-top:8px">
        <thead><tr style="border-bottom:1px solid #009fb7">
            <th style="padding:4px 8px;text-align:left;color:#009fb7;font-size:11px">Guild</th>
            <th style="padding:4px 8px;text-align:right;color:#009fb7;font-size:11px">Abundance</th>
            <th style="padding:4px 8px;text-align:left;color:#009fb7;font-size:11px">Status</th>
            <th style="padding:4px 8px;text-align:center;color:#009fb7;font-size:11px">Priority</th>
            <th style="padding:4px 8px;text-align:center;color:#009fb7;font-size:11px">CLR</th>
        </tr></thead>
        <tbody>{guild_table_rows}</tbody>
    </table>'''
    
    # Build CLR diagnostic ratios — show ALL 4, even undefined
    clr_html = ""
    clr_data = inputs["microbiome"].get("clr_ratios", {})
    for ratio_name in ["CUR", "FCR", "MDR", "PPR"]:
        val = clr_data.get(ratio_name, "N/A (undefined)")
        if val == "N/A (undefined)" or not val:
            clr_html += f'<div class="clr-chip" style="opacity:0.5">{ratio_name}: N/A (guilds absent)</div>'
        else:
            clr_html += f'<div class="clr-chip">{ratio_name}: {val}</div>'
    
    # Build decision steps — enhanced with health claims and prebiotic doses
    steps_html = ""
    for i, step in enumerate(steps):
        method_cls = "deterministic" if step["method"] == "deterministic" else "llm"
        components_html = ""
        if step.get("components"):
            # For supplement selection, show ALL components with tags
            if step.get("decision") == "Supplement Selection":
                table_rows = ""
                for ht in health_table:
                    src_cls = ht.get("source","").replace("_","-")
                    src_label = ht.get("source","").replace("_"," ").title()
                    # Build tag — use health_claim if available, otherwise derive from delivery/source
                    hc = ht.get("health_claim", "")
                    delivery = ht.get("delivery", "")
                    if not hc:
                        if "probiotic" in delivery:
                            hc = "Probiotic Mix"
                        elif delivery == "sachet" and ht.get("source") == "microbiome_primary":
                            hc = "Prebiotic"
                        elif delivery == "softgel":
                            hc = "Softgel"
                        elif "evening" in delivery and "magnesium" in ht.get("component","").lower():
                            hc = "Sleep/Stress"
                        elif "evening" in delivery:
                            hc = "Sleep"
                        elif ht.get("source") == "microbiome_linked":
                            hc = "Microbiome"
                    tag_colors = {
                        "Probiotic Mix": ("#1dd1a1", "#1dd1a133"),
                        "Prebiotic": ("#1dd1a1", "#1dd1a133"),
                        "Microbiome": ("#54a0ff", "#54a0ff33"),
                        "Softgel": ("#a55eea", "#a55eea33"),
                        "Sleep": ("#6c5ce7", "#6c5ce733"),
                        "Sleep/Stress": ("#6c5ce7", "#6c5ce733"),
                    }
                    tc, tbg = tag_colors.get(hc, ("#009fb7", "#009fb733"))
                    hc_badge = f'<span style="background:{tbg};color:{tc};padding:1px 6px;border-radius:3px;font-size:9px;margin-left:6px;font-weight:600">{hc}</span>' if hc else ""
                    # Source badge colors
                    src_bg = '#1dd1a133' if 'microbiome' in src_cls else ('#feca5733' if 'questionnaire' in src_cls else '#e2e3e533')
                    src_color = '#1dd1a1' if 'microbiome' in src_cls else ('#feca57' if 'questionnaire' in src_cls else '#ccc')
                    table_rows += f'''<tr style="border-bottom:1px solid #2a2a4a">
                        <td style="padding:5px 8px;font-weight:600;color:#e0e0e0;font-size:12px">{ht["component"]}{hc_badge}</td>
                        <td style="padding:5px 8px;color:#aaa;font-size:11px">{ht["what_it_targets"]}</td>
                        <td style="padding:5px 8px;white-space:nowrap"><span style="background:{src_bg};color:{src_color};padding:2px 6px;border-radius:3px;font-size:10px">{src_label}</span></td>
                    </tr>'''
                components_html = f'''<div class="step-components" style="padding:0;overflow:hidden">
                    <table style="width:100%;border-collapse:collapse">
                        <thead><tr style="border-bottom:1px solid #009fb7">
                            <th style="padding:5px 8px;text-align:left;color:#009fb7;font-size:11px;width:40%">Component</th>
                            <th style="padding:5px 8px;text-align:left;color:#009fb7;font-size:11px;width:40%">What It Targets</th>
                            <th style="padding:5px 8px;text-align:left;color:#009fb7;font-size:11px;width:20%">Source</th>
                        </tr></thead>
                        <tbody>{table_rows}</tbody>
                    </table>
                </div>'''
            else:
                components_html = '<div class="step-components">' + ''.join(f'<div class="comp-item">→ {c}</div>' for c in step["components"]) + '</div>'
        
        # For prebiotic design, add individual doses
        extra_detail = ""
        if step.get("decision") == "Prebiotic Design":
            pb_data = master.get("decisions", {}).get("prebiotic_design", {}).get("prebiotics", [])
            if pb_data:
                pb_items = ''.join(f'<div class="comp-item">→ {p["substance"]}: {p["dose_g"]}g {"[FODMAP]" if p.get("fodmap") else ""}</div>' for p in pb_data)
                extra_detail = f'<div class="step-components" style="margin-top:6px">{pb_items}</div>'
        
        overrides_html = ""
        if step.get("overrides"):
            overrides_html = '<div class="step-overrides">' + ''.join(f'<div class="override-item">⚠️ {o}</div>' for o in step["overrides"]) + '</div>'
        
        steps_html += f'''
        <div class="step-card">
            <div class="step-header">
                <span class="step-num">{step["step"]}</span>
                <span class="step-name">{step["decision"]}</span>
                <span class="method-badge {method_cls}">{step["method"]}</span>
            </div>
            <div class="step-body">
                <div class="step-row"><strong>Input:</strong> {step.get("input", "")}</div>
                <div class="step-result"><strong>Result:</strong> {step["result"]}</div>
                <div class="step-reasoning"><strong>Reasoning:</strong> {step.get("reasoning", "")}</div>
                {components_html}
                {extra_detail}
                {overrides_html}
            </div>
        </div>'''
    
    # Build delivery summary — full unit cards with ingredients (like client dashboard)
    delivery_html = ""
    recipe_units = recipe.get("units", [])
    for unit in recipe_units:
        timing_cls = "border-left:4px solid #f4a261" if "morning" in unit.get("timing","").lower() else "border-left:4px solid #6c5ce7"
        # Weight — check total_weight_mg, total_weight_g, or compute from fill_weight × quantity
        tw_mg = unit.get("total_weight_mg")
        tw_g = unit.get("total_weight_g")
        if not tw_mg and not tw_g:
            fill = unit.get("fill_weight_per_unit_mg") or unit.get("fill_weight_mg")
            qty = unit.get("quantity", 1)
            if fill:
                tw_mg = fill * qty
        # For Mg capsules, show elemental Mg in brackets
        elemental_note = ""
        if "magnesium" in unit.get("label","").lower():
            daily_totals = unit.get("daily_totals", {})
            elem = daily_totals.get("elemental_mg_mg", 0)
            if elem:
                elemental_note = f" ({elem}mg elemental)"
        weight_str = f'<span class="delivery-weight">{tw_mg}mg{elemental_note}</span>' if tw_mg else (f'<span class="delivery-weight">{tw_g}g</span>' if tw_g else "")
        
        # Ingredients rows
        ing_rows = ""
        ingredients = unit.get("ingredients", unit.get("ingredients_per_unit", []))
        for ing in ingredients:
            amount_g = ing.get("amount_g")
            amount_mg = ing.get("amount_mg")
            dose = ing.get("dose_per_softgel", ing.get("amount", ""))
            cfu = f' — {ing["cfu_billions"]}B CFU' if "cfu_billions" in ing else ""
            if amount_g: amt = f"{_format_dose(amount_g)}g"
            elif amount_mg: amt = f"{_format_dose(amount_mg)}mg"
            elif dose: amt = _format_dose(dose)
            else: amt = "—"
            ing_rows += f'<tr><td style="padding:2px 8px;color:#ccc;font-size:12px">{ing.get("component","?")}</td><td style="padding:2px 8px;color:#aaa;font-size:12px">{amt}{cfu}</td></tr>'
        
        delivery_html += f'''
        <div style="background:#0f3460;border-radius:8px;padding:12px;margin-bottom:8px;{timing_cls}">
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
                <span style="background:#009fb7;color:#1a1a2e;padding:2px 8px;border-radius:4px;font-weight:700;font-size:12px">Unit {unit["unit_number"]}</span>
                <span style="font-weight:600;flex:1">{unit["label"]}</span>
                <span style="color:#aaa;font-size:12px">{unit.get("quantity",1)}× {unit["timing"]}</span>
                {weight_str}
            </div>
            <table style="width:100%;border-collapse:collapse">{ing_rows}</table>
        </div>'''
    
    # Build axes
    axes_html = ""
    for ax in axes:
        status_class = "confirmed" if "CONFIRMED" in ax["questionnaire_verification"] else "predicted"
        axes_html += f'<div class="axis-row {status_class}"><strong>{ax["axis"]}:</strong> {ax["questionnaire_verification"]} — {ax["severity"]}</div>'
    
    # Build unified questionnaire section (merged from 3 redundant panels)
    q_goals = q_data.get("goals_ranked", [])
    q_claims = master.get("decisions", {}).get("rule_outputs", {}).get("health_claims", {}).get("supplement_claims", [])
    q_cov_level = q_coverage_data.get("coverage_level", "?")
    q_cov_pct = q_coverage_data.get("completion_pct", 0)
    q_cov_color = "#1dd1a1" if q_cov_level == "GOOD" else ("#feca57" if q_cov_level in ("LOW", "MINIMAL") else "#54a0ff")
    questionnaire_html = f'''
    <div class="panel">
        <h2>📋 Questionnaire Input <span style="float:right;font-size:12px;color:{q_cov_color}">Coverage: {q_cov_level} ({round(q_cov_pct)}%)</span></h2>
        <p style="font-size:13px;color:#aaa;margin-bottom:10px">{q_narrative or inputs.get("questionnaire",{}).get("summary","")}</p>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;font-size:13px">
            <div>
                <div style="color:#009fb7;font-weight:600;margin-bottom:4px">Demographics & Lifestyle</div>
                <div>Sex: {master.get("input_summary",{}).get("questionnaire_driven",{}).get("biological_sex", master.get("input_summary",{}).get("questionnaire_driven",{}).get("sex","N/A"))} | Age: {master.get("input_summary",{}).get("questionnaire_driven",{}).get("age","N/A")}</div>
                <div>Sensitivity: {q_data.get("sensitivity_classification","?").title()}</div>
                <div>Stress: {q_data.get("stress_level","?")}/10 | Sleep: {q_data.get("sleep_quality","?")}/10</div>
                <div>Bloating: {q_data.get("bloating_severity","N/R")}/10</div>
                <div>Diet: {master.get("input_summary",{}).get("questionnaire_driven",{}).get("diet","None")}</div>
            </div>
            <div>
                <div style="color:#009fb7;font-weight:600;margin-bottom:4px">Goals (ranked)</div>
                {''.join(f"<div>{i+1}. {g.replace('_',' ').title()}</div>" for i,g in enumerate(q_goals))}
            </div>
            <div>
                <div style="color:#009fb7;font-weight:600;margin-bottom:4px">Health Claims Triggered</div>
                {''.join(f'<div><span style="background:#009fb733;padding:2px 6px;border-radius:3px;font-size:11px">{c}</span></div>' for c in q_claims)}
            </div>
        </div>
    </div>'''
    
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Decision Trace — {sample_id}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #e0e0e0; }}
.header {{ background: linear-gradient(135deg, #16213e, #0f3460); padding: 32px; border-bottom: 3px solid #009fb7; }}
.header h1 {{ font-size: 24px; color: #009fb7; }}
.header .subtitle {{ color: #aaa; font-size: 13px; margin-top: 4px; }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
.panel {{ background: #16213e; border-radius: 12px; padding: 20px; margin-bottom: 16px; border: 1px solid #2a2a4a; }}
.panel h2 {{ color: #009fb7; font-size: 16px; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 1px; }}
.inputs-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
.guild-chip {{ display: inline-block; padding: 4px 10px; border-radius: 4px; margin: 3px; font-size: 12px; }}
.guild-chip.below {{ background: #ff6b6b33; color: #ff6b6b; border: 1px solid #ff6b6b; }}
.guild-chip.above {{ background: #feca5733; color: #feca57; border: 1px solid #feca57; }}
.guild-chip.normal {{ background: #1dd1a133; color: #1dd1a1; border: 1px solid #1dd1a1; }}
.clr-chip {{ display: inline-block; padding: 4px 10px; border-radius: 4px; margin: 3px; font-size: 12px; background: #2a2a4a; color: #ccc; }}
.step-card {{ background: #0f3460; border-radius: 10px; padding: 16px; margin-bottom: 12px; border-left: 4px solid #009fb7; }}
.step-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }}
.step-num {{ background: #009fb7; color: #1a1a2e; width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 14px; }}
.step-name {{ font-weight: 600; font-size: 15px; flex: 1; }}
.method-badge {{ padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
.method-badge.deterministic {{ background: #1dd1a133; color: #1dd1a1; }}
.method-badge.llm {{ background: #a55eea33; color: #a55eea; }}
.step-body {{ font-size: 13px; line-height: 1.6; }}
.step-row {{ color: #aaa; margin-bottom: 4px; }}
.step-result {{ color: #1dd1a1; font-weight: 600; margin-bottom: 4px; }}
.step-reasoning {{ color: #bbb; font-style: italic; }}
.step-components {{ margin-top: 8px; padding: 8px; background: #1a1a2e; border-radius: 6px; }}
.comp-item {{ font-size: 12px; color: #ccc; padding: 2px 0; }}
.step-overrides {{ margin-top: 6px; }}
.override-item {{ font-size: 12px; color: #feca57; }}
.delivery-row {{ display: flex; gap: 12px; padding: 8px 0; border-bottom: 1px solid #2a2a4a; font-size: 13px; align-items: center; }}
.delivery-type {{ font-weight: 600; min-width: 180px; }}
.delivery-count {{ background: #009fb7; color: #1a1a2e; padding: 2px 8px; border-radius: 4px; font-weight: 700; }}
.delivery-timing {{ min-width: 80px; color: #aaa; }}
.delivery-contents {{ color: #ccc; flex: 1; }}
.delivery-weight {{ color: #009fb7; font-weight: 700; font-size: 14px; min-width: 80px; text-align: right; }}
.axis-row {{ padding: 6px 0; font-size: 13px; }}
.axis-row.confirmed {{ color: #1dd1a1; }}
.axis-row.predicted {{ color: #feca57; }}
.evidence-grid {{ display: flex; gap: 12px; flex-wrap: wrap; }}
.evidence-tag {{ padding: 4px 10px; border-radius: 4px; font-size: 12px; }}
.evidence-tag.mb {{ background: #1dd1a133; color: #1dd1a1; }}
.evidence-tag.q {{ background: #feca5733; color: #feca57; }}
.evidence-tag.both {{ background: #54a0ff33; color: #54a0ff; }}
.footer {{ text-align: center; padding: 20px; color: #555; font-size: 11px; }}
@media (max-width: 768px) {{ .inputs-grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="header">
    <h1>🔬 Formulation Decision Trace</h1>
    <div class="subtitle">Sample: {sample_id} | Validation: {trace["validation"]} | Generated: {trace.get("generated_at", "")[:19]}</div>
</div>

<div class="container">
    <div class="inputs-grid">
        <div class="panel">
            <h2>🧬 Microbiome Input</h2>
            <p style="font-size:13px;color:#aaa;margin-bottom:8px">{inputs["microbiome"]["summary"]}</p>
            <div>{guild_html}</div>
            <div style="margin-top:8px">{clr_html}</div>
            {'<div style="margin-top:10px;padding:10px;background:#0f3460;border-radius:6px;border-left:3px solid #009fb7;font-size:12px;color:#ccc;line-height:1.5"><strong style="color:#009fb7">Formulation Logic:</strong> ' + inputs["microbiome"].get("formulation_narrative", "") + '</div>' if inputs["microbiome"].get("formulation_narrative") else ''}
        </div>
        {questionnaire_html}
    </div>
    
    <div class="panel">
        <h2>⚙️ Decision Chain ({len(steps)} steps)</h2>
        {steps_html}
    </div>
    
    <div class="panel">
        <h2>📦 Final Formulation</h2>
        {delivery_html}
        <div style="margin-top:12px;font-size:14px;color:#009fb7;font-weight:600">
            Total: {final["total_units"]} units | {final["total_weight_g"]}g | {final["validation"]}
        </div>
    </div>
    
    <div class="inputs-grid">
        <div class="panel">
            <h2>🔬 Health Axes</h2>
            {axes_html}
        </div>
        <div class="panel">
            <h2>📊 Source Attribution</h2>
            <div style="font-size:14px">
                <div>MB-Primary: {source["microbiome_primary"]} | MB-Linked: {source["microbiome_linked"]}</div>
                <div>Questionnaire: {source["questionnaire_only"]} | Fixed: {source["fixed_component"]}</div>
                <div style="color:#009fb7;font-weight:600;margin-top:8px">Microbiome-Informed: {source["microbiome_informed_pct"]}%</div>
                <div style="color:#888;font-size:11px;margin-top:6px">Fixed = softgel components that weren't the trigger for adding the capsule but come bundled with it. The softgel itself IS personalized — only added when at least one client need matches (omega-3, vitamin D, vitamin E, or astaxanthin).</div>
            </div>
        </div>
    </div>
    
    {'<div class="panel" style="border:1px solid #a55eea"><h2 style="color:#a55eea">🧬 Ecological Rationale</h2><div style="font-size:13px;line-height:1.6"><div style="margin-bottom:8px"><strong style="color:#1dd1a1">Why this mix was selected:</strong><br>' + eco_rationale.get("selected_rationale","") + '</div><div style="margin-bottom:8px"><strong style="color:#feca57">Alternative analysis:</strong><br>' + eco_rationale.get("alternative_analysis","") + '</div><div style="margin-bottom:8px"><strong style="color:#54a0ff">Combined strategy:</strong><br>' + eco_rationale.get("combined_assessment","") + '</div><div><strong style="color:#009fb7">Recommendation:</strong><br>' + eco_rationale.get("recommendation","") + '</div></div></div>' if eco_rationale and eco_rationale.get("selected_rationale") else ''}
</div>

<div class="footer">
    Scientific Board Review Dashboard | Pipeline v1.0 | For internal review only
</div>
</body>
</html>'''
    
    return html


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def generate_dashboards(sample_id: str, output_dir: str = None, sample_dir: str = None):
    """Generate both dashboards for a sample.
    
    Reads JSON inputs from output_dir (reports/reports_json/).
    Writes HTML dashboards to {sample_dir}/reports/reports_html/.
    """
    if output_dir is None:
        output_dir = str(Path(__file__).parent / "output")
    
    # Determine HTML output directory
    if sample_dir:
        html_dir = Path(sample_dir) / "reports" / "reports_html"
    else:
        html_dir = Path(output_dir)
    html_dir.mkdir(parents=True, exist_ok=True)
    
    # Dashboard 1: Client
    print(f"Building client dashboard for {sample_id}...")
    client_html = build_client_dashboard(sample_id, output_dir)
    client_path = html_dir / f"supplement_guide_{sample_id}.html"
    with open(client_path, 'w', encoding='utf-8') as f:
        f.write(client_html)
    print(f"  📊 Client: {client_path}")
    
    # Dashboard 2: Board
    print(f"Building board dashboard for {sample_id}...")
    board_html = build_board_dashboard(sample_id, output_dir)
    board_path = html_dir / f"formulation_decision_trace_{sample_id}.html"
    with open(board_path, 'w', encoding='utf-8') as f:
        f.write(board_html)
    print(f"  📊 Board: {board_path}")
    
    return client_path, board_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate formulation dashboards")
    parser.add_argument("--sample-id", required=True, help="Sample ID")
    parser.add_argument("--output-dir", help="Output directory with JSON files")
    args = parser.parse_args()
    
    generate_dashboards(args.sample_id, args.output_dir)
 