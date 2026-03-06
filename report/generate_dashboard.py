#!/usr/bin/env python3
"""Generate HTML dashboard from platform JSON + analysis JSON."""
import json, sys, os

def _safe(val):
    """Safely convert to string, handle dicts with non_expert."""
    if isinstance(val, dict):
        if 'non_expert' in val:
            return str(val['non_expert'])
        return str(val)
    if val is None:
        return ''
    s = str(val)
    if s.startswith('[LLM') or s.startswith("{'scientific"):
        return ''
    return s

def generate_dashboard(platform_json_path, output_path, analysis_json_path=None):
    """Generate HTML dashboard from platform JSON only.
    All data comes from _platform.json — no analysis JSON needed."""
    with open(platform_json_path) as f:
        p = json.load(f)

    ov = p['overview_tab']
    bg = p['bacterial_groups_tab']
    rc = p['root_causes_tab']
    vt = p['vitamins_tab']
    ap = p['action_plan_tab']
    meta = p['metadata']
    score = ov['gut_health_glance']['overall_score']
    drivers = score.get('score_drivers', {})
    pillars = ov['gut_health_glance']['pillars']
    dials = ov['metabolic_dials']['dials']

    bc = {'Excellent':'#2ecc71','Good':'#27ae60','Fair':'#f39c12','Needs Attention':'#e67e22','Concerning':'#e74c3c'}.get(score['band'],'#95a5a6')
    sc = lambda s: {'carb_driven':'#2ecc71','balanced':'#f39c12','protein_driven':'#e74c3c','efficient':'#2ecc71','ok':'#f39c12','sluggish':'#e74c3c','diet_fed':'#2ecc71','backup':'#f39c12','heavy_mucus':'#e74c3c','scfa_dominant':'#2ecc71','protein_pressure':'#e74c3c'}.get(s,'#f39c12')
    prc = lambda pl: {'CRITICAL':'#e74c3c','1A':'#e67e22','1B':'#f39c12','Monitor':'#2ecc71'}.get(pl,'#95a5a6')

    h = []
    h.append('<!DOCTYPE html><html><head><meta charset="utf-8">')
    h.append('<title>Health Report — ' + meta['sample_id'] + '</title>')
    h.append('''<style>
*{margin:0;padding:0;box-sizing:border-box}body{font-family:'Helvetica Neue',Arial,sans-serif;background:#f5f6fa;color:#2d3436;line-height:1.6;font-size:13px}.container{max-width:1100px;margin:0 auto;padding:20px}.header{background:linear-gradient(135deg,#0a3d62,#1e3799);color:white;padding:30px;border-radius:12px;margin-bottom:20px}.header h1{font-size:22px}.header p{opacity:.8;font-size:13px}.tabs{display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap}.tab{padding:10px 20px;background:white;border-radius:8px;cursor:pointer;font-size:13px;font-weight:500;border:2px solid #dfe6e9}.tab.active{background:#0a3d62;color:white;border-color:#0a3d62}.tab-content{display:none}.tab-content.active{display:block}.card{background:white;border-radius:12px;padding:24px;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,.06)}.card h2{font-size:16px;color:#0a3d62;margin-bottom:12px;border-bottom:2px solid #dfe6e9;padding-bottom:8px}.card h3{font-size:14px;color:#1e3799;margin:12px 0 8px}.score-ring{width:130px;height:130px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-direction:column;margin:0 auto 12px}.score-ring .num{font-size:36px;font-weight:700}.score-ring .band{font-size:13px;font-weight:600}.pillar-row{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}.pillar{flex:1;min-width:140px;background:#f5f6fa;border-radius:8px;padding:12px;text-align:center}.pillar .name{font-size:11px;color:#636e72}.pillar .val{font-size:18px;font-weight:700;color:#0a3d62}.pillar .bar{height:6px;background:#dfe6e9;border-radius:3px;margin-top:6px}.pillar .bar-fill{height:100%;border-radius:3px;background:#1e3799}.dial-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.dial{background:#f5f6fa;border-radius:8px;padding:16px;border-left:4px solid #ccc}.dial .dlabel{font-size:14px;font-weight:600}.dial .desc{font-size:12px;color:#636e72;margin:6px 0}.dial .ctx{font-size:11px;color:#7f8c8d;margin-top:8px;line-height:1.5}.guild{padding:16px;border-bottom:1px solid #f0f0f0}.guild:last-child{border:none}.guild .guild-header{display:flex;align-items:center;gap:12px;margin-bottom:8px}.guild .step{width:32px;height:32px;border-radius:50%;background:#1e3799;color:white;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;flex-shrink:0}.guild .gname{font-weight:600;font-size:15px}.guild .capacity-bar{display:flex;align-items:center;gap:8px;margin:8px 0}.guild .workers{font-size:22px;font-weight:700;color:#0a3d62}.guild .guild-text{font-size:12px;color:#636e72;line-height:1.5}.flag{padding:10px 14px;border-radius:6px;margin:6px 0;font-size:13px;line-height:1.5}.flag.critical{background:#ffeaa7;border-left:4px solid #e74c3c}.flag.significant{background:#ffeaa7;border-left:4px solid #e67e22}.flag.moderate{background:#f5f6fa;border-left:4px solid #f39c12}.flag.mild{background:#f5f6fa;border-left:4px solid #2ecc71}.vitamin{display:flex;align-items:center;gap:12px;padding:14px;border-bottom:1px solid #f0f0f0}.vitamin .status-dot{width:14px;height:14px;border-radius:50%;flex-shrink:0}.vitamin .vname{font-weight:600;font-size:14px}.vitamin .vstatus{font-size:12px;color:#636e72}.step-card{background:#f5f6fa;border-radius:8px;padding:16px;margin:10px 0;border-left:4px solid #ccc}.step-card .step-header{display:flex;align-items:center;gap:8px;margin-bottom:8px}.step-card .priority-badge{padding:3px 10px;border-radius:4px;color:white;font-size:11px;font-weight:600}.step-card .step-title{font-weight:600;font-size:14px}.strength{padding:8px 0;font-size:13px;border-bottom:1px solid #f0f0f0;line-height:1.5}.two-col{display:grid;grid-template-columns:1fr 1fr;gap:16px}p.text{color:#636e72;margin:8px 0;line-height:1.6}ul.items{margin:8px 0 8px 20px;color:#636e72}ul.items li{margin:4px 0}.green-box{background:#d5f5e3;padding:16px;border-radius:8px;margin:12px 0}.trophic{background:#fef9e7;border-left:4px solid #f39c12;padding:12px;border-radius:6px;margin:8px 0}
</style></head><body><div class="container">''')

    # Header
    h.append('<div class="header"><h1>Microbiome Health Report</h1>')
    h.append('<p>Sample: ' + meta['sample_id'] + ' | Date: ' + meta['report_date'] + '</p></div>')

    # Educational intro
    h.append('<div class="card" style="background:linear-gradient(135deg,#f8f9fa,#eaf2f8);border-left:4px solid #1e3799">')
    h.append('<h2 style="border:none;padding:0;margin-bottom:8px">How to Read This Report</h2>')
    h.append('<p class="text">You\'ve probably heard that eating fiber is good and too much protein can be tough on your gut — but why exactly? Your gut bacteria are the reason. They form specialized teams that process what you eat, and their balance determines how well your body converts food into energy, protects your gut lining, and manages inflammation.</p>')
    h.append('<p class="text">This report analyzes the DNA of bacteria in your stool sample. We predicted the metabolic relationships between your bacterial teams and assessed how they\'re working together. What follows is our interpretation of what\'s happening in your gut right now — and what can be done about it.</p>')
    h.append('</div>')

    # Tabs
    h.append('<div class="tabs">')
    for i, name in enumerate(['Overview','Bacterial Groups','Root Causes','Vitamins','Action Plan']):
        active = ' active' if i == 0 else ''
        h.append('<div class="tab' + active + '" onclick="showTab(' + str(i) + ')">' + name + '</div>')
    h.append('</div>')

    # ─── TAB 0: OVERVIEW ───
    h.append('<div class="tab-content active" id="tab0">')
    h.append('<div class="card"><h2>Your Gut Health at a Glance</h2>')
    summary = _safe(ov['gut_health_glance'].get('summary_sentence',''))
    if summary: h.append('<p class="text">' + summary + '</p>')
    h.append('<div class="score-ring" style="border:8px solid ' + bc + '"><span class="num">' + str(score['total']) + '</span><span class="band" style="color:' + bc + '">' + score['band'] + '</span></div>')
    kn = drivers.get('key_note','')
    if kn: h.append('<p class="text" style="text-align:center;font-size:12px">' + kn + '</p>')
    h.append('<div class="pillar-row">')
    for pk, pv in pillars.items():
        pct = round(pv['score']/pv['max']*100) if pv['max'] else 0
        h.append('<div class="pillar"><div class="name">' + pk.replace('_',' ').title() + '</div><div class="val">' + str(pv['score']) + '/' + str(pv['max']) + '</div><div class="bar"><div class="bar-fill" style="width:' + str(pct) + '%"></div></div><div style="font-size:10px;color:#636e72;margin-top:4px">' + pv.get('description','') + '</div></div>')
    h.append('</div></div>')

    # What's happening
    h.append('<div class="two-col"><div class="card"><h2>What\'s Happening</h2>')
    ob = ov['whats_happening']['overall_balance']
    dr = ov['whats_happening']['diversity_resilience']
    h.append('<h3>Overall Balance: ' + ob['label'] + '</h3><p class="text">' + ob.get('description','') + '</p>')
    h.append('<h3>Diversity: ' + dr['label'] + '</h3><p class="text">' + dr.get('description','') + '</p>')
    h.append('</div><div class="card"><h2>Key Strengths</h2>')
    for s in ov['whats_happening'].get('key_strengths',[])[:5]:
        h.append('<div class="strength">✅ ' + _safe(s) + '</div>')
    h.append('<h3 style="margin-top:12px">Key Opportunities</h3>')
    for o in ov['whats_happening'].get('key_opportunities',[])[:5]:
        h.append('<div class="strength">🎯 ' + _safe(o) + '</div>')
    h.append('</div></div>')

    # Dials with visual range indicators
    dial_ranges = {
        'main_fuel': {'min': -1.5, 'max': 1.5, 'left': 'Protein-driven', 'right': 'Carb-driven', 'good': 'right'},
        'fermentation_efficiency': {'min': -1.5, 'max': 1.5, 'left': 'Sluggish', 'right': 'Efficient', 'good': 'right'},
        'gut_lining_dependence': {'min': -1.5, 'max': 1.5, 'left': 'Diet-fed', 'right': 'Mucus-dependent', 'good': 'left'},
        'harsh_byproducts': {'min': -1.5, 'max': 1.5, 'left': 'SCFA-dominant', 'right': 'Protein pressure', 'good': 'left'},
    }
    h.append('<div class="card"><h2>How Your Gut Is Processing Food</h2>')
    h.append('<p class="text">' + ov['metabolic_dials'].get('intro_text','') + '</p><div class="dial-grid">')
    for dk, dv in dials.items():
        color = sc(dv.get('state',''))
        val = dv.get('value', 0) or 0
        rng = dial_ranges.get(dk, {'min':-1.5,'max':1.5,'left':'Low','right':'High','good':'right'})
        # Calculate position (0-100%)
        pos = max(0, min(100, (val - rng['min']) / (rng['max'] - rng['min']) * 100))
        h.append('<div class="dial" style="border-left-color:' + color + '">')
        h.append('<div class="dlabel">' + dv.get('label','') + '</div>')
        # Visual gauge bar
        h.append('<div style="margin:10px 0 6px 0">')
        h.append('<div style="display:flex;justify-content:space-between;font-size:10px;color:#95a5a6;margin-bottom:3px"><span>' + rng['left'] + '</span><span>' + rng['right'] + '</span></div>')
        h.append('<div style="position:relative;height:8px;background:linear-gradient(to right,')
        if rng['good'] == 'right':
            h.append('#e74c3c,#f39c12 35%,#f39c12 50%,#2ecc71 75%,#27ae60)')
        else:
            h.append('#27ae60,#2ecc71 25%,#f39c12 50%,#f39c12 65%,#e74c3c)')
        h.append(';border-radius:4px">')
        h.append('<div style="position:absolute;left:' + str(pos) + '%;top:-3px;width:14px;height:14px;background:white;border:3px solid ' + color + ';border-radius:50%;transform:translateX(-7px);box-shadow:0 1px 3px rgba(0,0,0,.3)"></div>')
        h.append('</div></div>')
        h.append('<div class="desc">' + dv.get('description','') + '</div>')
        ctx = dv.get('context','')
        if ctx: h.append('<div class="ctx">' + ctx + '</div>')
        h.append('</div>')
    h.append('</div></div>')

    # What this means
    wtm = ov.get('what_this_means',{})
    isw = _safe(wtm.get('is_something_wrong',''))
    ctbf = _safe(wtm.get('can_this_be_fixed',''))
    if isw or ctbf:
        h.append('<div class="two-col">')
        if isw: h.append('<div class="card"><h2>Is Something Wrong?</h2><p class="text">' + isw + '</p></div>')
        if ctbf: h.append('<div class="card"><h2>Can This Be Fixed?</h2><p class="text">' + ctbf + '</p></div>')
        h.append('</div>')
    h.append('</div>')

    # ─── TAB 1: BACTERIAL GROUPS ─── NO truncation
    h.append('<div class="tab-content" id="tab1"><div class="card">')
    h.append('<h2>' + bg.get('title','Bacterial Groups') + '</h2>')
    h.append('<p class="text">' + bg.get('intro_text','') + '</p>')
    for g in bg['guilds']:
        cap = g['capacity']
        h.append('<div class="guild"><div class="guild-header"><div class="step">' + str(g['step']) + '</div>')
        h.append('<div class="gname">' + g['name'] + '</div></div>')
        h.append('<div class="guild-text">' + g['functional_summary'] + '</div>')
        h.append('<div class="capacity-bar"><div class="workers">' + str(cap['actual_players']) + '<span style="font-size:12px;color:#636e72;font-weight:400"> / ' + str(cap['optimal_players']) + ' optimal</span></div>')
        h.append('<span style="font-size:12px;padding:2px 8px;border-radius:4px;background:#f5f6fa">' + g['status'] + '</span></div>')
        impact = g.get('impact_explanation','')
        if impact: h.append('<div class="guild-text">' + impact + '</div>')
        note = g.get('additional_note','')
        if note: h.append('<div style="font-size:11px;color:#7f8c8d;margin-top:4px">' + note + '</div>')
        h.append('</div>')
    h.append('</div></div>')

    # ─── TAB 2: ROOT CAUSES ─── 4-Part Guided Narrative (per UX spec)
    h.append('<div class="tab-content" id="tab2">')

    # Primary pattern headline + causal narrative
    pp = rc.get('primary_pattern','')
    cn = rc.get('causal_narrative','')
    if pp:
        h.append('<div class="card" style="background:linear-gradient(135deg,#f8f9fa,#eaf2f8);border-left:4px solid #1e3799">')
        h.append('<h2 style="border:none;padding:0;margin-bottom:8px">Understanding Your Results</h2>')
        h.append('<p class="text" style="font-size:15px;font-weight:500;color:#2d3436;margin:0 0 12px 0">' + pp + '</p>')
        if cn:
            h.append('<div style="background:white;padding:14px 16px;border-radius:8px;margin-top:8px;border:1px solid #dfe6e9">')
            h.append('<p class="text" style="font-size:13px;line-height:1.7;color:#2d3436;margin:0">' + cn + '</p>')
            h.append('</div>')
        h.append('</div>')

    # ── PART 1: What We Found ──
    flags = rc.get('how_we_can_tell',{}).get('diagnostic_flags',[])
    if flags:
        h.append('<div class="card">')
        h.append('<h2>Part 1: What We Found in Your Test</h2>')
        h.append('<p class="text">When we measured your bacterial teams (see the <a href="#" onclick="showTab(1);return false" style="color:#1e3799">Bacterial Groups</a> tab for details), here\'s what stood out:</p>')
        for fl in flags:
            sev = fl.get('severity','mild')
            h.append('<div class="flag ' + sev + '">⚠️ ' + fl['flag'] + '</div>')
        h.append('</div>')

    # ── PART 2: What This Means ──
    met_ev = rc.get('metabolic_evidence',[])
    trophic = rc.get('trophic_impact',{})
    cascades = trophic.get('cascade_impacts',[])

    if met_ev or cascades:
        h.append('<div class="card">')
        h.append('<h2>Part 2: What This Means for How Your Gut Works</h2>')
        h.append('<p class="text">Your metabolic readings tell us how well your gut processes food (see <a href="#" onclick="showTab(0);return false" style="color:#1e3799">Overview → Processing Food</a> for the full picture).</p>')

        # Split into positive/neutral vs concerning
        # Green = clearly good. Neutral = ok/balanced (acceptable). Red = needs attention.
        green_states = {'carb_driven','efficient','diet_fed','scfa_dominant'}
        neutral_states = {'ok','balanced'}
        red_states = {'protein_driven','sluggish','heavy_mucus','protein_pressure','backup'}
        good_items = [ev for ev in met_ev if ev.get('state','') in green_states or ev.get('state','') in neutral_states]
        concern_items = [ev for ev in met_ev if ev.get('state','') in red_states]

        if good_items:
            h.append('<h3>What\'s working well</h3>')
            for ev in good_items:
                expl = ev.get('explanation','')
                label = ev.get('label','')
                h.append('<div style="padding:10px 14px;margin:6px 0;border-left:3px solid #2ecc71;background:#f0faf0;border-radius:6px">✅ <strong>' + label + ':</strong> <span style="color:#636e72">' + expl + '</span></div>')

        if concern_items:
            h.append('<h3>Areas that need attention</h3>')
            for ev in concern_items:
                expl = ev.get('explanation','')
                label = ev.get('label','')
                state = ev.get('state','')
                color = {'balanced':'#f39c12','protein_driven':'#e74c3c','ok':'#f39c12','sluggish':'#e74c3c','backup':'#f39c12','heavy_mucus':'#e74c3c','protein_pressure':'#e74c3c'}.get(state,'#636e72')
                h.append('<div style="padding:10px 14px;margin:6px 0;border-left:3px solid ' + color + ';background:#fef9e7;border-radius:6px">⚠️ <strong>' + label + ':</strong> <span style="color:#636e72">' + expl + '</span></div>')

        if cascades:
            if concern_items or flags:
                h.append('<h3 style="margin-top:16px">How these imbalances ripple through your gut</h3>')
                h.append('<p class="text">The issues found in Part 1 don\'t stay isolated — they create chain reactions:</p>')
            else:
                h.append('<h3 style="margin-top:16px">Minor downstream effects to watch</h3>')
            for imp in cascades:
                h.append('<div class="trophic"><strong>' + imp.get('title','') + '</strong>')
                h.append('<p class="text" style="margin:4px 0 0 0">' + imp.get('description','') + '</p></div>')
        h.append('</div>')

    # ── PART 3: How This Probably Happened ──
    loops = rc.get('feedback_loops',[])
    lifestyle = rc.get('lifestyle_inference',{})
    li_pattern = lifestyle.get('pattern','')

    if loops or li_pattern:
        h.append('<div class="card">')
        h.append('<h2>Part 3: The Domino Effect in Your Gut</h2>')
        h.append('<p class="text">Gut changes don\'t happen overnight — they build up gradually, with one shift triggering the next. Based on the patterns we see, here\'s how things likely unfolded:</p>')

        if li_pattern:
            li_evidence = lifestyle.get('evidence',[])
            h.append('<div style="background:#f0f4f8;padding:14px;border-radius:8px;margin:8px 0">')
            h.append('<p class="text" style="font-weight:500;margin:0 0 6px 0">' + li_pattern + '</p>')
            if li_evidence:
                h.append('<ul class="items" style="margin:4px 0 0 0">')
                for ev in li_evidence:
                    if ev: h.append('<li>' + ev + '</li>')
                h.append('</ul>')
            h.append('</div>')
            li_disc = lifestyle.get('disclaimer','')
            if li_disc:
                h.append('<p class="text" style="font-style:italic;font-size:11px;margin-top:4px">' + li_disc + '</p>')

        if loops:
            h.append('<h3 style="margin-top:14px">Patterns That Reinforce Themselves</h3>')
            h.append('<p class="text">Once these patterns start, each step makes the next one more likely — like dominoes. The good news: reversing the first domino can stop the whole chain:</p>')
            for loop in loops:
                name = loop.get('name','')
                status = loop.get('status','')
                chain = loop.get('chain',[])
                status_color = {'active':'#e74c3c','developing':'#f39c12','stable':'#2ecc71'}.get(status,'#636e72')
                h.append('<div style="background:#fef9e7;border-left:4px solid ' + status_color + ';padding:14px;border-radius:6px;margin:8px 0">')
                h.append('<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px"><strong style="font-size:14px">' + name + '</strong>')
                h.append('<span style="font-size:10px;padding:2px 8px;border-radius:3px;background:' + status_color + ';color:white">' + status + '</span></div>')
                if chain:
                    # Visual flow boxes
                    h.append('<div style="display:flex;flex-wrap:wrap;gap:4px;align-items:center">')
                    for i, step in enumerate(chain):
                        h.append('<div style="background:white;padding:6px 10px;border-radius:4px;font-size:11px;border:1px solid #dfe6e9">' + step + '</div>')
                        if i < len(chain) - 1:
                            h.append('<span style="color:#636e72;font-size:14px">→</span>')
                    h.append('</div>')
                # Health impact
                hi = loop.get('health_impact','')
                if hi:
                    h.append('<div style="margin-top:8px;padding:8px 12px;background:#f8f0f0;border-radius:4px;font-size:12px;color:#636e72"><strong>What this means for you:</strong> ' + hi + '</div>')
                h.append('</div>')
        h.append('</div>')

    # ── Guild Scenario Matrix (9-scenario classification) ──
    scenarios = p.get('guild_scenarios', [])
    if scenarios:
        h.append('<div class="card">')
        h.append('<h2>Guild Ecological Assessment</h2>')
        h.append('<p class="text">Each bacterial team is assessed on two axes: whether they have enough members (range position) and whether they\'re winning or losing competition with other groups (competitive signal).</p>')
        h.append('<table style="width:100%;border-collapse:collapse;font-size:12px;margin-top:8px">')
        h.append('<tr style="background:#f5f6fa"><th style="padding:8px;text-align:left">Guild</th><th style="padding:8px;text-align:center">Abundance</th><th style="padding:8px;text-align:left">Range Position</th><th style="padding:8px;text-align:left">Competition</th><th style="padding:8px;text-align:left">Assessment</th></tr>')
        for sc in scenarios:
            sev = sc.get('severity', '')
            row_bg = '#fef9e7' if sev == 'attention' else ('#fde8e8' if sev == 'critical' else '#f0faf0' if sev == 'healthy' else '#f5f6fa')
            h.append('<tr style="background:' + row_bg + '">')
            h.append('<td style="padding:6px 8px;font-weight:600">' + sc.get('guild_display', '') + '</td>')
            h.append('<td style="padding:6px 8px;text-align:center">' + str(sc.get('abundance_pct', '')) + '%</td>')
            h.append('<td style="padding:6px 8px">' + sc.get('range_label', '') + '</td>')
            h.append('<td style="padding:6px 8px">' + sc.get('clr_label', '') + '</td>')
            h.append('<td style="padding:6px 8px;font-weight:500">' + sc.get('combined_assessment', '') + '</td>')
            h.append('</tr>')
        h.append('</table>')
        h.append('</div>')

    # ── PART 4: Can This Be Reversed? ──
    rev = rc.get('conclusion',{}).get('reversibility',{})
    diag = _safe(rc.get('primary_diagnosis',''))
    insights = rc.get('key_insights',[])

    h.append('<div class="card">')
    h.append('<h2>Part 4: Can This Be Reversed?</h2>')
    rev_data = rev
    if rev_data.get('label'):
        h.append('<div class="green-box"><h3 style="margin:0 0 8px 0">✅ ' + rev_data.get('label','') + '</h3>')
        h.append('<p class="text" style="margin:0">' + rev_data.get('description','') + '</p>')
        tl = rev_data.get('estimated_timeline','')
        if tl: h.append('<p class="text" style="margin:6px 0 0 0"><strong>Estimated timeline:</strong> ' + tl + '</p>')
        # Transition note (from mucin degrader check)
        tn = rev_data.get('transition_note','')
        if tn: h.append('<p class="text" style="margin:6px 0 0 0;font-style:italic">' + tn + '</p>')
        h.append('</div>')

    # Method explanation
    method = rev_data.get('method_note','')
    if method:
        h.append('<div style="background:#f0f4f8;padding:12px;border-radius:6px;margin:8px 0"><p class="text" style="margin:0;font-size:12px"><strong>How we estimate this:</strong> ' + method + '</p></div>')

    h.append('<p class="text">Your personalized restoration plan targets these specific gaps. <a href="#" onclick="showTab(4);return false" style="color:#1e3799;font-weight:500">See your Action Plan →</a></p>')
    h.append('<p class="text" style="font-size:12px;color:#7f8c8d">Your custom supplement formula will be designed to address these imbalances directly.</p>')

    if insights:
        h.append('<h3 style="margin-top:12px">Additional Insights</h3>')
        for ins in insights:
            if isinstance(ins, dict):
                title = ins.get('title','')
                expl = ins.get('explanation','')
                if title: h.append('<p class="text"><strong>' + title + ':</strong> ' + expl + '</p>')

    disclaimer = rc.get('how_we_can_tell',{}).get('disclaimer','')
    if disclaimer: h.append('<p class="text" style="font-style:italic;font-size:11px;margin-top:12px;color:#95a5a6">' + disclaimer + '</p>')
    h.append('</div>')

    h.append('</div>')

    # ─── TAB 3: VITAMINS ───
    h.append('<div class="tab-content" id="tab3"><div class="card">')
    h.append('<h2>' + vt.get('title','Vitamins') + '</h2>')
    h.append('<p class="text">' + vt.get('intro','') + '</p>')
    robust = vt.get('good_news',{}).get('robust_production',[])
    if robust:
        h.append('<div class="green-box"><strong>Good production of:</strong> ' + ', '.join(robust) + '</div>')
    for v in vt['vitamins']:
        rl = v['risk_level']
        dot = ['#2ecc71','#f39c12','#e67e22','#e74c3c'][min(rl,3)]
        h.append('<div class="vitamin"><div class="status-dot" style="background:' + dot + '"></div>')
        h.append('<div style="flex:1"><div class="vname">' + v['display_name'] + '</div>')
        h.append('<div class="vstatus">' + v['status'] + '</div>')
        h.append('<div style="font-size:11px;color:#7f8c8d">' + v.get('role','') + '</div>')
        assessment = v.get('assessment','')
        if assessment: h.append('<div style="font-size:11px;color:#636e72;margin-top:4px">' + assessment + '</div>')
        h.append('</div></div>')
    h.append('</div></div>')

    # ─── TAB 4: ACTION PLAN ─── Show ALL guilds with priorities
    h.append('<div class="tab-content" id="tab4"><div class="card">')
    h.append('<h2>' + ap.get('title','Action Plan') + '</h2>')
    rev = ap.get('reversibility',{})
    if rev.get('label'):
        h.append('<div class="green-box"><strong>' + rev.get('label','') + '</strong> — ' + rev.get('estimated_timeline','') + '<br>')
        h.append('<span style="font-size:12px">' + rev.get('description','') + '</span></div>')

    # Active intervention steps
    steps = ap.get('steps',[])
    if steps:
        h.append('<h3>Priority Interventions</h3>')
        for s in steps:
            color = prc(s.get('priority_level',''))
            h.append('<div class="step-card" style="border-left-color:' + color + '"><div class="step-header">')
            h.append('<span class="priority-badge" style="background:' + color + '">' + s.get('priority_level','') + '</span>')
            h.append('<span class="step-title">Step ' + str(s['step_number']) + ': ' + s['title'] + ' (' + s['guild_display'] + ')</span></div>')
            h.append('<p class="text">' + s['why'] + '</p>')
            h.append('<div style="font-size:12px;color:#636e72">Current: ' + str(s['current_players']) + ' workers → Target: ' + str(s['target_players_min']) + '-' + str(s['target_players_max']) + ' workers | ' + s['timeline'] + '</div></div>')

    # Monitor guilds from platform JSON — only show guilds that are borderline
    # or have a notable condition (e.g., below range for contextual guilds).
    # Skip guilds clearly within range with no concerns to reduce visual clutter.
    monitor_guilds = ap.get('monitor_guilds', [])
    notable_monitors = [mg for mg in monitor_guilds if 'Below' in mg.get('status', '') or 'Above' in mg.get('status', '')]
    if notable_monitors:
        h.append('<h3 style="margin-top:16px">Monitoring</h3>')
        for mg in notable_monitors:
            status_note = mg.get('status', '')
            if 'Below' in status_note:
                monitor_text = 'Below reference range but not requiring active intervention. Will be monitored for changes.'
            elif 'Above' in status_note:
                monitor_text = 'Slightly above reference range. Will be monitored for trends.'
            else:
                monitor_text = 'Within healthy range. Continue current maintenance.'
            h.append('<div class="step-card" style="border-left-color:#2ecc71"><div class="step-header">')
            h.append('<span class="priority-badge" style="background:#2ecc71">Monitor</span>')
            h.append('<span class="step-title">' + mg['name'] + ' — ' + mg['status'] + ' (' + str(mg['abundance']) + '%)</span></div>')
            h.append('<div style="font-size:12px;color:#636e72">' + monitor_text + '</div></div>')

    # Vitamin check
    vc = ap.get('vitamin_check')
    if vc:
        h.append('<h3 style="margin-top:16px">Vitamin Support Check</h3>')
        h.append('<div class="step-card" style="border-left-color:#f39c12"><p class="text">' + vc.get('why','') + '</p></div>')

    # Forecast
    forecast = ap.get('forecast',[])
    if forecast:
        h.append('<h3 style="margin-top:16px">Expected Improvements</h3>')
        h.append('<table style="width:100%;border-collapse:collapse;font-size:12px"><tr style="background:#f5f6fa"><th style="padding:8px;text-align:left">Guild</th><th>Current</th><th>Target</th><th></th></tr>')
        for fc in forecast:
            h.append('<tr><td style="padding:6px">' + fc['guild_display'] + '</td><td style="text-align:center">' + str(fc['current_players']) + '</td><td style="text-align:center">' + str(fc['target_players_min']) + '-' + str(fc['target_players_max']) + '</td><td style="text-align:center">' + fc['direction'] + '</td></tr>')
        h.append('</table>')

    # Next steps
    ns_list = ap.get('next_steps',[])
    if ns_list:
        h.append('<h3 style="margin-top:16px">Next Steps</h3><ul class="items">')
        for ns in ns_list:
            h.append('<li>' + ns + '</li>')
        h.append('</ul>')

    rnote = ap.get('reversibility_note','')
    if rnote: h.append('<p class="text" style="font-style:italic;margin-top:12px;font-size:11px">' + rnote + '</p>')
    h.append('</div></div>')

    # JS
    h.append('''<script>function showTab(n){document.querySelectorAll('.tab-content').forEach((t,i)=>{t.classList.toggle('active',i===n)});document.querySelectorAll('.tab').forEach((t,i)=>{t.classList.toggle('active',i===n)});}</script>''')
    h.append('</div></body></html>')

    html = '\n'.join(h)
    with open(output_path, 'w') as f:
        f.write(html)
    print('Dashboard saved:', output_path)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 generate_dashboard.py <platform_json_path> [output_path] [analysis_json_path]")
        sys.exit(1)
    jp = sys.argv[1]
    op = sys.argv[2] if len(sys.argv) > 2 else jp.replace('_platform.json','_dashboard.html')
    ap = sys.argv[3] if len(sys.argv) > 3 else None
    generate_dashboard(jp, op, ap)
