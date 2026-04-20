#!/usr/bin/env python3
"""
Mix Selector — Deterministic probiotic mix selection.

Extracted from the monolithic llm_decisions.py to decouple the modular pipeline.
Mix selection is ALWAYS deterministic (never LLM) — it uses canonical priority
scores from shared/guild_priority.py and ecological reasoning.

Also contains:
  - lookup_strains_for_mix() — KB strain lookup
  - _should_add_lpc37() — deterministic Lpc-37 enhancement logic
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Resolve paths for KB and shared modules
_SCRIPT_DIR = Path(__file__).parent.parent
_KB_DIR = _SCRIPT_DIR / "knowledge_base"
_SHARED_DIR = _SCRIPT_DIR.parent / "shared"

if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

from guild_priority import compute_guild_priority


def _load_kb(filename: str) -> Dict:
    path = _KB_DIR / filename
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _should_add_lpc37(stress: float, goals: list) -> bool:
    """Lpc-37 enhancement: add 5B CFU when stress/mood conditions met.
    Rules:
      - Stress ≥ 6/10 → always add
      - Stress ≥ 4/10 AND mood/anxiety is a stated goal → always add
    """
    mood_goals = {"improve_mood_reduce_anxiety", "reduce_stress_anxiety"}
    if stress is not None and stress >= 6:
        return True
    if stress is not None and stress >= 4 and any(g in mood_goals for g in goals):
        return True
    return False


def lookup_strains_for_mix(mix_id: int) -> list:
    """Look up canonical strains for a mix from synbiotic_mixes.json."""
    try:
        mixes_kb = _load_kb("synbiotic_mixes.json")
        mix_data = mixes_kb["mixes"].get(str(mix_id), {})
        return mix_data.get("strains", [])
    except Exception:
        return []


def select_mix_offline(unified_input: Dict, rule_outputs: Dict) -> Dict:
    """Ecological decision tree for probiotic mix selection.

    Uses canonical priority scores from shared/guild_priority.py (v2.0) and
    ecological reasoning (trophic cascades, competitive displacement, bottleneck
    identification) to select the optimal mix.

    Architecture:
      Branch A: Broad collapse (≥3 beneficial guilds compromised) → Mix 1/4/5/8
      Branch B: Targeted intervention (1-2 beneficial guilds) → Mix 2/3/1
      Branch C: Contextual-only or healthy → Mix 4/5/8/6

    See formulation_automation/documentation/ for full scenario mapping.
    """
    guilds = unified_input["microbiome"]["guilds"]
    clr_ratios = unified_input["microbiome"]["clr_ratios"]
    questionnaire = unified_input.get("questionnaire", {})
    goals = questionnaire.get("goals", {}).get("ranked", [])
    stress = questionnaire.get("lifestyle", {}).get("stress_level")

    # Extract guild data
    bifido = guilds.get("bifidobacteria", {})
    butyrate = guilds.get("butyrate_producers", {})
    fiber = guilds.get("fiber_degraders", {})
    proteolytic = guilds.get("proteolytic", {})
    mucin = guilds.get("mucin_degraders", {})
    cross = guilds.get("cross_feeders", {})

    # Compute canonical priority scores for each guild
    def _score(guild_key, guild_data):
        abund = guild_data.get("abundance_pct", 0) or 0
        status = guild_data.get("status", "")
        clr = guild_data.get("clr")
        evenness = guild_data.get("evenness")
        p = compute_guild_priority(guild_key, abund, status, clr, evenness)
        return p["priority_score"]

    scores = {
        "bifido": _score("bifidobacteria", bifido),
        "butyrate": _score("butyrate_producers", butyrate),
        "fiber": _score("fiber_degraders", fiber),
        "cross": _score("cross_feeders", cross),
        "proteolytic": _score("proteolytic", proteolytic),
        "mucin": _score("mucin_degraders", mucin),
    }

    # Abundances and CLRs for trigger messages
    bifido_pct = bifido.get("abundance_pct", 0) or 0
    butyrate_pct = butyrate.get("abundance_pct", 0) or 0
    fiber_pct = fiber.get("abundance_pct", 0) or 0
    proteolytic_pct = proteolytic.get("abundance_pct", 0) or 0
    mucin_pct = mucin.get("abundance_pct", 0) or 0
    fiber_clr = fiber.get("clr")
    bifido_clr = bifido.get("clr")
    mdr = clr_ratios.get("MDR")
    ppr = clr_ratios.get("PPR")

    # Count compromised guilds (score ≥ 2.0 = 1B or higher)
    beneficial_scores = [scores["fiber"], scores["bifido"], scores["cross"], scores["butyrate"]]
    compromised_beneficial = sum(1 for s in beneficial_scores if s >= 2.0)

    def _make_result(mix_id, mix_name, trigger, clr_ctx="", confidence="medium", alt=""):
        lpc37 = _should_add_lpc37(stress, goals)
        total_cfu = 50
        strains = []
        if lpc37:
            strains.append({"name": "Lacticaseibacillus paracasei Lpc-37", "cfu_billions": 5, "role": "HPA-axis modulator (psychobiotic enhancement)"})
            total_cfu += 5
        return {
            "mix_id": mix_id, "mix_name": mix_name,
            "primary_trigger": trigger, "clr_context": clr_ctx,
            "strains": strains, "total_cfu_billions": total_cfu,
            "lpc37_added": lpc37,
            "confidence": confidence, "alternative_considered": alt,
        }

    # ══════════════════════════════════════════════════════════════════════
    # BRANCH A: Broad Collapse (≥3 beneficial guilds compromised)
    # ══════════════════════════════════════════════════════════════════════
    if compromised_beneficial >= 3:
        below_names = [g.get("name", "?") for g in guilds.values()
                       if ("Below" in g.get("status", "") or "Absent" in g.get("status", ""))
                       and g.get("name", "").lower() not in {"mucin degraders", "proteolytic guild", "proteolytic dysbiosis guild"}]

        # A1: Collapse + Proteolytic takeover
        if scores["proteolytic"] >= 5.0:
            if ppr is not None and ppr > 0:
                return _make_result(4, "Proteolytic Suppression",
                    f"Broad collapse ({compromised_beneficial} beneficial guilds compromised) + Proteolytic overgrowth ({proteolytic_pct:.1f}%, score={scores['proteolytic']:.1f}) — protein-driven (PPR={ppr:+.2f})",
                    clr_ctx=f"PPR={ppr:+.2f} (protein-driven → suppress first)",
                    confidence="high",
                    alt="Mix 1 if PPR turns negative on retest")
            else:
                return _make_result(1, "Dysbiosis Recovery",
                    f"Broad collapse ({compromised_beneficial} beneficial guilds compromised) + Proteolytic elevated ({proteolytic_pct:.1f}%) but PPR={'%+.2f' % ppr if ppr is not None else 'N/A'} (not protein-driven) → ecosystem reset prioritized",
                    clr_ctx=f"PPR={'%+.2f' % ppr if ppr is not None else 'N/A'} (fiber/neutral-driven → broad recovery)",
                    confidence="high",
                    alt="Mix 4 if PPR becomes positive on retest")

        # A2: Collapse + Mucin overgrowth
        mucin_evenness = mucin.get("evenness")
        if scores["mucin"] >= 5.0:
            if mdr is not None and mdr > 0.5:
                return _make_result(8, "Fiber Expansion & Competitive Displacement",
                    f"Broad collapse + Mucin overgrowth ({mucin_pct:.1f}%, MDR={mdr:+.2f} mucus-dependent)",
                    clr_ctx=f"MDR={mdr:+.2f}, Fiber CLR={fiber_clr}",
                    confidence="high")
            elif mucin_evenness is not None and mucin_evenness < 0.4:
                return _make_result(8, "Fiber Expansion & Competitive Displacement",
                    f"Broad collapse + Mucin overgrowth ({mucin_pct:.1f}%, evenness={mucin_evenness:.2f} monoculture) — aggressive displacement despite diet-fed MDR",
                    clr_ctx=f"MDR={'%+.2f' % mdr if mdr is not None else 'N/A'}, evenness={mucin_evenness:.2f} (Akk monoculture → Mix 8)",
                    confidence="high",
                    alt="Mix 5 if evenness improves above 0.4")
            else:
                return _make_result(5, "Mucus Barrier Restoration",
                    f"Broad collapse + Mucin overgrowth ({mucin_pct:.1f}%, MDR={mdr:+.2f} diet-fed)" if mdr is not None else f"Broad collapse + Mucin overgrowth ({mucin_pct:.1f}%)",
                    clr_ctx=f"MDR={mdr}" if mdr is not None else "",
                    confidence="high")

        # A3: Pure broad collapse
        return _make_result(1, "Dysbiosis Recovery",
            f"Broad ecosystem dysfunction: {compromised_beneficial} beneficial guilds compromised ({', '.join(below_names[:4])})",
            clr_ctx=f"CUR={clr_ratios.get('CUR')}, PPR={ppr}" if any(v is not None for v in [clr_ratios.get('CUR'), ppr]) else "",
            confidence="high",
            alt="Mix 4 if proteolytic becomes 1A+; Mix 2 if only Bifido critical")

    # ══════════════════════════════════════════════════════════════════════
    # BRANCH B: Targeted Intervention (1-2 beneficial guilds compromised)
    # ══════════════════════════════════════════════════════════════════════
    if 1 <= compromised_beneficial <= 2:
        beneficial_ranked = sorted(
            [("bifido", scores["bifido"], bifido),
             ("fiber", scores["fiber"], fiber),
             ("butyrate", scores["butyrate"], butyrate),
             ("cross", scores["cross"], cross)],
            key=lambda x: -x[1]
        )
        top_key, top_score, top_guild = beneficial_ranked[0]
        top_prio = top_guild.get("priority_level", "")
        top_pct = top_guild.get("abundance_pct", 0) or 0

        # B1: Bifidobacteria is highest priority
        if top_key == "bifido" and top_score >= 2.0:
            if bifido_pct <= 0.5:
                return _make_result(2, "Bifidogenic Restore",
                    f"Bifidobacteria {bifido.get('status', 'depleted')} ({bifido_pct:.1f}%, score={top_score:.1f}) — keystone guild failure",
                    clr_ctx=f"Bifido CLR={bifido_clr:+.2f}" if bifido_clr is not None else "",
                    confidence="high",
                    alt="Mix 1 if multiple guilds also depleted")
            if bifido_pct < 3.0:
                return _make_result(2, "Bifidogenic Restore",
                    f"Bifidobacteria depleted ({bifido_pct:.1f}%, score={top_score:.1f}) — lactate amplifier compromised",
                    clr_ctx=f"Bifido CLR={bifido_clr:+.2f}" if bifido_clr is not None else "",
                    confidence="high" if top_score >= 5.0 else "medium")
            return _make_result(2, "Bifidogenic Restore",
                f"Bifidobacteria under pressure ({bifido_pct:.1f}%, CLR={bifido_clr:+.2f}, score={top_score:.1f})" if bifido_clr else f"Bifidobacteria under pressure ({bifido_pct:.1f}%, score={top_score:.1f})",
                confidence="medium",
                alt="Monitor if CLR stabilizes")

        # B2: Fiber is highest priority
        if top_key == "fiber" and top_score >= 2.0:
            if fiber_clr is not None and fiber_clr > -0.3:
                return _make_result(3, "Fiber & SCFA Restoration",
                    f"Fiber below range ({fiber_pct:.1f}%, score={top_score:.1f}, CLR={fiber_clr:+.2f}) — substrate provisioning needed",
                    clr_ctx=f"Fiber CLR={fiber_clr:+.2f} ({'winning but starved' if fiber_clr > 0.3 else 'neutral — can benefit from substrate'})",
                    confidence="high" if top_score >= 5.0 else "medium",
                    alt="Mix 8 if Mucin also elevated")
            if fiber_clr is not None and fiber_clr < -0.3:
                if scores["mucin"] >= 5.0 and mdr is not None and mdr > 0.5:
                    return _make_result(8, "Fiber Expansion & Competitive Displacement",
                        f"Fiber depleted ({fiber_pct:.1f}%, CLR={fiber_clr:+.2f}) + Mucin overgrowth (MDR={mdr:+.2f})",
                        clr_ctx=f"Fiber CLR={fiber_clr:+.2f}, MDR={mdr:+.2f}",
                        confidence="high")
                return _make_result(1, "Dysbiosis Recovery",
                    f"Fiber below range ({fiber_pct:.1f}%, CLR={fiber_clr:+.2f}) — being outcompeted, broad recovery needed",
                    clr_ctx=f"Fiber CLR={fiber_clr:+.2f} (losing competition)",
                    confidence="high" if top_score >= 5.0 else "medium")
            return _make_result(3, "Fiber & SCFA Restoration",
                f"Fiber below range ({fiber_pct:.1f}%, score={top_score:.1f}) — substrate support",
                confidence="medium")

        # B3: Butyrate is highest priority
        if top_key == "butyrate" and top_score >= 2.0:
            if scores["fiber"] >= 2.0 and scores["cross"] >= 2.0:
                return _make_result(1, "Dysbiosis Recovery",
                    f"Butyrate depleted ({butyrate_pct:.1f}%, score={top_score:.1f}) + upstream guilds also compromised",
                    confidence="high")
            if scores["bifido"] >= 5.0:
                return _make_result(2, "Bifidogenic Restore",
                    f"Butyrate depleted ({butyrate_pct:.1f}%) — upstream Bifido bottleneck (score={scores['bifido']:.1f})",
                    confidence="high",
                    alt="Fix lactate amplifier → butyrate recovers downstream")
            return _make_result(3, "Fiber & SCFA Restoration",
                f"Butyrate producers below range ({butyrate_pct:.1f}%, score={top_score:.1f}) — terminal SCFA pathway",
                clr_ctx=f"FCR={clr_ratios.get('FCR')}" if clr_ratios.get('FCR') is not None else "",
                confidence="high" if top_score >= 5.0 else "medium")

        # B4: Cross-feeders highest priority
        if top_key == "cross" and top_score >= 2.0:
            return _make_result(1, "Dysbiosis Recovery",
                f"Cross-feeders below range ({cross.get('abundance_pct', 0):.1f}%, score={top_score:.1f}) — trophic bridge broken",
                confidence="high" if top_score >= 5.0 else "medium",
                alt="Mix 3 if fiber is also the bottleneck")

    # ══════════════════════════════════════════════════════════════════════
    # BRANCH C: Contextual-only issues OR healthy
    # ══════════════════════════════════════════════════════════════════════
    if compromised_beneficial == 0:
        # C1: Proteolytic overgrowth
        if scores["proteolytic"] >= 5.0:
            return _make_result(4, "Proteolytic Suppression",
                f"Proteolytic overgrowth ({proteolytic_pct:.1f}%, score={scores['proteolytic']:.1f}) — healthy beneficial base",
                clr_ctx=f"PPR={ppr}" if ppr is not None else "",
                confidence="high")

        # C2: Mucin overgrowth
        if scores["mucin"] >= 5.0:
            mucin_evenness = mucin.get("evenness")
            if mdr is not None and mdr > 0.5:
                return _make_result(8, "Fiber Expansion & Competitive Displacement",
                    f"Mucin overgrowth ({mucin_pct:.1f}%, MDR={mdr:+.2f} mucus-dependent) — beneficial guilds healthy",
                    clr_ctx=f"MDR={mdr:+.2f}",
                    confidence="high")
            elif mucin_evenness is not None and mucin_evenness < 0.4:
                return _make_result(8, "Fiber Expansion & Competitive Displacement",
                    f"Mucin overgrowth ({mucin_pct:.1f}%, evenness={mucin_evenness:.2f} monoculture) — beneficial guilds healthy",
                    clr_ctx=f"MDR={'%+.2f' % mdr if mdr is not None else 'N/A'}, evenness={mucin_evenness:.2f}",
                    confidence="high")
            else:
                return _make_result(5, "Mucus Barrier Restoration",
                    f"Mucin overgrowth ({mucin_pct:.1f}%) — beneficial guilds healthy, diet-fed",
                    clr_ctx=f"MDR={mdr}" if mdr is not None else "",
                    confidence="high")

        # C3: Mild proteolytic or mucin concern
        if scores["proteolytic"] >= 2.0:
            return _make_result(4, "Proteolytic Suppression",
                f"Proteolytic mildly elevated ({proteolytic_pct:.1f}%, score={scores['proteolytic']:.1f}) — all beneficial guilds healthy",
                confidence="medium",
                alt="Mix 6 if proteolytic normalizes")
        if scores["mucin"] >= 2.0:
            return _make_result(5, "Mucus Barrier Restoration",
                f"Mucin mildly elevated ({mucin_pct:.1f}%, score={scores['mucin']:.1f}) — all beneficial guilds healthy",
                confidence="medium",
                alt="Mix 6 if mucin normalizes")

        # C4: All healthy
        return _make_result(6, "Maintenance Gold Standard",
            "All guilds at Monitor priority — ecosystem healthy",
            clr_ctx="Balanced pattern",
            confidence="high",
            alt="Review if any guild approaching 1B threshold")

    # ══════════════════════════════════════════════════════════════════════
    # DEFAULT: Safest fallback
    # ══════════════════════════════════════════════════════════════════════
    return _make_result(1, "Dysbiosis Recovery",
        f"Complex pattern ({compromised_beneficial} beneficial compromised) — broad recovery as safe default",
        confidence="low",
        alt="Review ecological pattern manually")
