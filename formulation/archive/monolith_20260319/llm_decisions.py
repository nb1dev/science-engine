#!/usr/bin/env python3
"""
LLM Decision Modules — Clinical decisions via AWS Bedrock.

Three focused LLM calls:
  1. Mix selection: Guild data + CLR → synbiotic mix + strains
  2. Supplement selection: Health claims + deficiencies → vitamins + supplements
  3. Prebiotic design: Mix + sensitivity + symptoms → prebiotic blend

Each call sends structured JSON context and expects structured JSON response.
Same Bedrock setup as report_automation pipeline.
"""

import json
import re
from pathlib import Path
from typing import Dict, Optional

try:
    import boto3
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False

# ─── CONFIG ───────────────────────────────────────────────────────────────────

BEDROCK_MODEL = "eu.anthropic.claude-sonnet-4-20250514-v1:0"
BEDROCK_REGION = "eu-west-1"
MAX_TOKENS = 4096

KB_DIR = Path(__file__).parent / "knowledge_base"


def _load_kb(filename: str) -> Dict:
    path = KB_DIR / filename
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _call_bedrock(system_prompt: str, user_prompt: str, max_tokens: int = MAX_TOKENS,
                  model_id: str = None, temperature: float = 0.2) -> str:
    """Call AWS Bedrock Claude API and return response text.
    
    Args:
        temperature: Sampling temperature. Default 0.2 for clinical consistency.
                     Use 0.05 for supplement selection (maximum reproducibility).
    """
    if not HAS_BOTO3:
        raise RuntimeError("boto3 not installed. Run: pip install boto3")

    from botocore.config import Config
    config = Config(read_timeout=300, connect_timeout=10, retries={'max_attempts': 3})
    client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION, config=config)

    response = client.invoke_model(
        modelId=model_id or BEDROCK_MODEL,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": temperature,
        })
    )

    result = json.loads(response["body"].read())
    return result["content"][0]["text"]


def _extract_json_from_response(text: str) -> Dict:
    """Extract JSON from LLM response (handles markdown code blocks)."""
    # Try to find JSON in code blocks
    json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group(1))

    # Try direct JSON parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in text
    brace_match = re.search(r'\{.*\}', text, re.DOTALL)
    if brace_match:
        return json.loads(brace_match.group(0))

    raise ValueError(f"Could not extract JSON from LLM response:\n{text[:500]}")


# ─── NOTE: Mix selection is FULLY DETERMINISTIC (select_mix_offline) ──────────
# LLMs are unreliable for numerical threshold evaluation (sign errors, hallucinated
# values). Mix selection uses guild priority_levels + CLR ratios via Python rules.
# See select_mix_offline() below.


# ─── LLM CALL 1 (of 2): SUPPLEMENT SELECTION ─────────────────────────────────

SUPPLEMENT_SELECTION_SYSTEM = """You are a clinical nutritionist selecting vitamins, minerals, and supplements.

You will receive:
1. Health claim categories (from client goals + microbiome signals)
2. Reported vitamin deficiencies and therapeutic dose triggers
3. Vitamin/mineral reference database (with authoritative doses)
4. Non-vitamin supplement database (with ranked choices)
5. Client demographics and current supplements

Your task: Select appropriate vitamins, minerals, and non-vitamin supplements.

CRITICAL RULES:
- Use EXACT doses from the reference databases (max_intake_in_supplements for vitamins)
- Use therapeutic doses when deficiency is reported (from therapeutic_triggers)
- Check interaction_risk for every selected item
- Respect rank order (prefer 1st choice supplements)
- Do NOT select supplements the client is already taking

⚠️ DO NOT SELECT THE FOLLOWING — they are handled by other pipeline steps:
- Magnesium (handled by separate Mg bisglycinate evening capsule)
- Vitamin D (already in fixed softgel: 10mcg × 2 = 20mcg daily)
- Vitamin E (already in fixed softgel: 7.5mg × 2 = 15mg daily)
- Omega-3 / DHA / EPA (already in fixed softgel: 712.5mg × 2 = 1425mg daily)
- Astaxanthin (already in fixed softgel)
- Melatonin (handled by deterministic sleep supplement selection)
- L-Theanine (handled by deterministic sleep supplement selection)
- Valerian / Valerian Root (handled by deterministic sleep supplement selection)
- PHGG, Psyllium, Inulin, FOS, GOS, Beta-glucans, Resistant Starch, Glucomannan, or ANY prebiotic fiber
  (handled by the separate prebiotic design step — prebiotic doses are microbiome-informed
  and sensitivity-calibrated within strict gram limits. Do NOT select fibers as supplements.)
If any of these appear in your selection, they will be REMOVED by the pipeline.

DELIVERY STRUCTURE (v3.0 — powder jar architecture):
- Powder jar (≤19g daily target, SOFT limit): prebiotics only (~5-8g). The jar is managed by the prebiotic design step.
- Morning Wellness Capsule(s) (650mg per capsule × N capsules, morning): ALL vitamins + minerals (except Mg) AND
  all non-bitter, non-capsule-only supplements with dose ≤ 650mg per serving. Examples: Glutathione, Banaba,
  Guarana, Panax Ginseng, GLA, Amla, Phosphatidyl Choline (if ≤650mg), Creatine (single-serve ≤650mg).
  The pipeline stacks these into the minimum number of capsules via the CapsuleStackingOptimizer.
- Powder jar (heavy non-bitter botanicals, dose > 650mg/day): Fennel, Chamomile, Lemon Balm, Peppermint,
  L-Glutamine, Creatine (if large dose), Glucomannan, Fenugreek, Safflower, Eleutherococcus when dose > 650mg.
  Use delivery = "jar" for these.
- Evening Wellness Capsule(s) (650mg per capsule × M capsules, evening): calming adaptogens, sleep aids,
  Tier 1 polyphenols. Capsule-only substances (Ashwagandha, Rhodiola, Curcumin, Quercetin, Propolis, Bacopa,
  Ginger Root, Star Anise, Bergamot, Capsicum) go here, NOT in jar or morning capsule.
  Use the UPPER end of the KB therapeutic range when clinically appropriate (e.g., Ashwagandha 600mg preferred
  over 300mg when stress ≥6/10).
- Polyphenol capsule (650mg, morning): Curcumin+Piperine, Bergamot only — these get their own dedicated capsule.

TOTAL UNIT COUNT: preferred maximum = 9 total daily units (all formats combined), absolute maximum = 13.
The CapsuleStackingOptimizer minimises capsule count — do not artificially split ingredients across multiple
capsules in your response. Just provide the correct delivery target per ingredient.

DELIVERY FIELD VALUES (use exactly one of these per ingredient):
  "morning_wellness_capsule" — vitamins, minerals, light botanicals (dose ≤ 650mg)
  "jar" — heavy non-bitter botanicals (dose > 650mg)
  "evening_capsule" — sleep aids, calming adaptogens, capsule-only substances
  "polyphenol_capsule" — Curcumin+Piperine, Bergamot only
  "softgel" — fat-soluble vitamins only (already handled, do not assign unless instructed)

SMART SELECTION INTELLIGENCE:
- Multiple supplements addressing DIFFERENT health claims is expected and correct
- For the SAME health claim: prefer one strong 1st choice over stacking 2nd/3rd choices
- A supplement that addresses MULTIPLE health claims is more valuable than single-claim agents
- DO NOT add a supplement if its primary benefit is already fully covered by another selected supplement
  with the same mechanism, UNLESS it has a genuinely complementary mechanism
- Always explain in the rationale WHY each supplement was selected and what UNIQUE benefit it adds
- Check interaction_note for EACH selected item against the client's current medications
- Check for supplement-to-supplement absorption conflicts (e.g., Zinc and Calcium compete for absorption)

RESPOND WITH ONLY A JSON OBJECT:
{
  "vitamins_minerals": [
    {
      "substance": "<name>",
      "dose": "<exact dose from reference>",
      "dose_value": <number>,
      "dose_unit": "<mg|mcg>",
      "therapeutic": <true|false>,
      "standard_dose": "<if therapeutic, what standard would be>",
      "delivery": "<morning_wellness_capsule|softgel>",
      "informed_by": "<microbiome|questionnaire|both>",
      "rationale": "<why selected>",
      "interaction_note": "<any interaction concerns>"
    }
  ],
  "supplements": [
    {
      "substance": "<name>",
      "dose_mg": <number>,
      "health_claim": "<which health claim category>",
      "rank": "<1st|2nd|3rd Choice>",
      "delivery": "<morning_wellness_capsule|jar|evening_capsule|polyphenol_capsule>",
      "informed_by": "<questionnaire>",
      "rationale": "<why selected>"
    }
  ],
  "omega3": {
    "dose_daily_mg": 1425,
    "dose_per_softgel_mg": 712.5,
    "rationale": "<why omega included>"
  },
  "existing_supplements_advice": [
    {"name": "<supplement>", "action": "continue|stop|adjust", "note": "<guidance>"}
  ]
}"""


def select_supplements(unified_input: Dict, rule_outputs: Dict) -> Dict:
    """LLM Call 2: Select vitamins, minerals, and supplements."""
    health_claims = rule_outputs["health_claims"]
    therapeutic = rule_outputs["therapeutic_triggers"]
    questionnaire = unified_input["questionnaire"]

    # Load knowledge bases
    vitamins_kb = _load_kb("vitamins_minerals.json")
    supplements_kb = _load_kb("supplements_nonvitamins.json")

    # Calculate evening capsule headroom AFTER deterministic sleep supplements
    # Sleep supplements (L-Theanine, Melatonin, Valerian) are added AFTER the LLM
    # selects supplements, so we need to tell the LLM how much space is actually available
    sleep_supps = rule_outputs.get("sleep_supplements", {}).get("supplements", [])
    evening_timing = rule_outputs.get("timing", {}).get("timing_assignments", {})
    reserved_evening_mg = 0
    reserved_evening_items = []
    for ss in sleep_supps:
        substance_key = ss["substance"].lower().replace("-", "_").replace(" ", "_")
        timing_info = evening_timing.get(substance_key, {})
        if timing_info.get("timing") == "evening":
            reserved_evening_mg += ss.get("dose_mg", 0)
            reserved_evening_items.append(f"{ss['substance']} {ss['dose_mg']}mg")
    # Evening capacity is per-capsule (CapsuleStackingOptimizer handles multi-capsule); inform LLM of reserved items
    available_evening_mg = 650 - reserved_evening_mg
    evening_headroom_note = f"Evening Wellness Capsule: {available_evening_mg}mg available per capsule"
    if reserved_evening_items:
        evening_headroom_note += f" (650mg per capsule minus {reserved_evening_mg}mg reserved for deterministic sleep supplements: {', '.join(reserved_evening_items)}). If your evening selections + sleep supplements exceed 650mg, the CapsuleStackingOptimizer will split into additional evening capsules automatically."
    else:
        evening_headroom_note += " (650mg per capsule, no sleep supplements reserved). CapsuleStackingOptimizer handles multi-capsule overflow automatically."

    user_prompt = f"""## Health Claim Categories to Address
Supplement claims: {json.dumps(health_claims["supplement_claims"])}
Vitamin claims: {json.dumps(health_claims["vitamin_claims"])}
Microbiome vitamin needs: {json.dumps(health_claims["microbiome_vitamin_needs"])}

## Therapeutic Dose Triggers
{json.dumps(therapeutic, indent=2)}

## Client Demographics
Age: {questionnaire["demographics"].get("age")}
Sex: {questionnaire["demographics"].get("biological_sex")}
Goals (ranked): {json.dumps(questionnaire["goals"]["ranked"])}

## Current Supplements (DO NOT DUPLICATE)
{json.dumps(questionnaire.get("current_supplements", []))}

## Current Medications (check interactions)
{json.dumps(questionnaire["medical"].get("medications", []))}

## EVENING CAPSULE HEADROOM
{evening_headroom_note}
⚠️ Your evening capsule selections MUST fit within this available space. Do NOT exceed {available_evening_mg}mg total for evening_capsule delivery.
If you need >650mg of evening supplements, the pipeline will split into 2 capsules — but try to stay within the available headroom to avoid dose compression.

## MANDATORY ITEMS (deterministic — MUST include with KB doses)
{json.dumps(rule_outputs.get("goal_triggered_supplements", {}).get("mandatory_vitamins", []), indent=2)}
{json.dumps(rule_outputs.get("goal_triggered_supplements", {}).get("mandatory_supplements", []), indent=2)}
These items are REQUIRED by deterministic rules. Include them with proper KB doses and delivery format.

## Vitamin & Mineral Reference Database
{json.dumps(vitamins_kb["vitamins_and_minerals"], indent=2)}

## Non-Vitamin Supplement Database (by health category)
{json.dumps(supplements_kb["health_categories"], indent=2)}

Select vitamins, minerals, and supplements that address the health claims.
Use exact doses from the databases. Apply therapeutic doses where triggered.
Return ONLY the JSON response."""

    response_text = _call_bedrock(SUPPLEMENT_SELECTION_SYSTEM, user_prompt, max_tokens=6000,
                                   temperature=0.05)
    return _extract_json_from_response(response_text)


# ─── CALL 3: PREBIOTIC DESIGN ────────────────────────────────────────────────

PREBIOTIC_DESIGN_SYSTEM = """You are a prebiotic formulation specialist.

You will receive:
1. Selected synbiotic mix (with required prebiotics per mix)
2. Client sensitivity classification and prebiotic dose range
3. Client digestive symptoms (bloating, stool type, food triggers)
4. Prebiotic rules (per-mix requirements, contradiction overrides)

Your task: Design the prebiotic blend for the POWDER JAR.

CRITICAL RULES:
- Total prebiotic grams MUST be within the provided dose range
- If contradictions exist (bloating ≥7, IBS-D, FODMAP sensitivity), apply override rules
- PHGG is the safest base fiber for sensitive clients
- List each prebiotic with exact gram dose
- Total FODMAP should stay ≤ sensitivity threshold

PHASED DOSING RULE (mandatory — affects your rationale):
The total_grams you output is the FULL week-3+ daily dose. The pipeline automatically computes
the week-1-2 half-dose (50% of total). You do NOT need to calculate this — just state the full dose.
However, your rationale MUST acknowledge that the client will start at half the dose for the first
2 weeks to allow gut adaptation before ramping to the full dose from week 3.
Reason: dietary fibers increase intestinal gas and bloating as the microbiota adapts. Starting at
half dose significantly reduces initial discomfort without compromising the therapeutic outcome.

DELIVERY: The prebiotic blend goes into a POWDER JAR (not a sachet). The jar has a soft daily
target of ≤19g total (including any non-bitter botanical powders added by the supplement step).
Design the prebiotic blend to leave reasonable headroom for potential botanical additions.

RESPOND WITH ONLY A JSON OBJECT:
{
  "strategy": "<description of prebiotic approach>",
  "total_grams": <number>,
  "total_fodmap_grams": <number>,
  "contradictions_found": [<list of contradiction types if any>],
  "overrides_applied": [<list of override descriptions if any>],
  "prebiotics": [
    {
      "substance": "<name>",
      "dose_g": <number>,
      "fodmap": <true|false>,
      "rationale": "<why this prebiotic and dose — include phased dosing acknowledgement>"
    }
  ],
  "condition_specific_additions": [
    {
      "substance": "<name>",
      "dose_g_or_mg": "<dose>",
      "condition": "<which condition>",
      "rationale": "<why added>"
    }
  ]
}"""


def design_prebiotics(unified_input: Dict, rule_outputs: Dict, mix_selection: Dict) -> Dict:
    """LLM Call 3: Design prebiotic blend for powder jar."""
    sensitivity = rule_outputs["sensitivity"]
    prebiotic_range = rule_outputs["prebiotic_range"]
    digestive = unified_input["questionnaire"]["digestive"]
    goals = unified_input["questionnaire"]["goals"]

    # Load knowledge base
    prebiotic_kb = _load_kb("prebiotic_rules.json")
    mix_key = f"mix_{mix_selection['mix_id']}"
    mix_prebiotics = prebiotic_kb["per_mix_prebiotics"].get(mix_key, {})

    user_prompt = f"""## Selected Synbiotic Mix
Mix ID: {mix_selection["mix_id"]}
Mix Name: {mix_selection["mix_name"]}

## Per-Mix Prebiotic Requirements
{json.dumps(mix_prebiotics, indent=2)}

## Sensitivity Classification
{json.dumps(sensitivity, indent=2)}

## Allowed Prebiotic Dose Range
Min: {prebiotic_range["min_g"]}g, Max: {prebiotic_range["max_g"]}g
CFU tier: {prebiotic_range["cfu_tier"]}

## Client Digestive Profile
Bloating severity: {digestive.get("bloating_severity")}/10
Bloating frequency: {digestive.get("bloating_frequency")}
Stool type (Bristol): {digestive.get("stool_type")}
Digestive satisfaction: {digestive.get("digestive_satisfaction")}/10

## Client Goals
{json.dumps(goals.get("ranked", []))}

## Condition-Specific Addition Options
{json.dumps(prebiotic_kb["condition_specific_additions"], indent=2)}

## Polyphenol Antimicrobial Thresholds (stay BELOW these for supplement doses)
{json.dumps(prebiotic_kb["polyphenol_antimicrobial_thresholds"], indent=2)}

Design the prebiotic blend for the POWDER JAR. Check for contradictions and apply overrides if needed.
Total must be within {prebiotic_range["min_g"]}-{prebiotic_range["max_g"]}g.
Remember: total_grams is the FULL week-3+ dose. The pipeline computes the week-1-2 half-dose automatically.
Leave headroom below 19g for potential botanical additions.
Return ONLY the JSON response."""

    response_text = _call_bedrock(PREBIOTIC_DESIGN_SYSTEM, user_prompt)
    return _extract_json_from_response(response_text)


# ─── OFFLINE FALLBACK (no Bedrock) ───────────────────────────────────────────

def _should_add_lp815(stress: float, goals: list) -> bool:
    """LP815 enhancement: add 5B CFU when stress/mood conditions met.
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
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), '..', 'shared'))
    from guild_priority import compute_guild_priority

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
        lp815 = _should_add_lp815(stress, goals)
        total_cfu = 50
        strains = []
        if lp815:
            strains.append({"name": "Lactiplantibacillus plantarum LP815", "cfu_billions": 5, "role": "GABA producer (psychobiotic enhancement)"})
            total_cfu += 5
        return {
            "mix_id": mix_id, "mix_name": mix_name,
            "primary_trigger": trigger, "clr_context": clr_ctx,
            "strains": strains, "total_cfu_billions": total_cfu,
            "lp815_added": lp815,
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
        # CLR-guided: only suppress proteolytic first if system is metabolically protein-driven (PPR > 0)
        # If PPR ≤ 0, proteolytic is elevated but not the metabolic driver → broad recovery addresses root cause
        if scores["proteolytic"] >= 5.0:  # 1A or CRITICAL
            if ppr is not None and ppr > 0:
                return _make_result(4, "Proteolytic Suppression",
                    f"Broad collapse ({compromised_beneficial} beneficial guilds compromised) + Proteolytic overgrowth ({proteolytic_pct:.1f}%, score={scores['proteolytic']:.1f}) — protein-driven (PPR={ppr:+.2f})",
                    clr_ctx=f"PPR={ppr:+.2f} (protein-driven → suppress first)",
                    confidence="high",
                    alt="Mix 1 if PPR turns negative on retest")
            else:
                # Proteolytic elevated but system not protein-driven → broad recovery
                return _make_result(1, "Dysbiosis Recovery",
                    f"Broad collapse ({compromised_beneficial} beneficial guilds compromised) + Proteolytic elevated ({proteolytic_pct:.1f}%) but PPR={'%+.2f' % ppr if ppr is not None else 'N/A'} (not protein-driven) → ecosystem reset prioritized",
                    clr_ctx=f"PPR={'%+.2f' % ppr if ppr is not None else 'N/A'} (fiber/neutral-driven → broad recovery)",
                    confidence="high",
                    alt="Mix 4 if PPR becomes positive on retest")

        # A2: Collapse + Mucin overgrowth
        # CLR-guided: MDR determines if mucus-dependent; evenness detects Akkermansia monoculture
        mucin_evenness = mucin.get("evenness")
        if scores["mucin"] >= 5.0:  # 1A or CRITICAL
            if mdr is not None and mdr > 0.5:
                return _make_result(8, "Fiber Expansion & Competitive Displacement",
                    f"Broad collapse + Mucin overgrowth ({mucin_pct:.1f}%, MDR={mdr:+.2f} mucus-dependent)",
                    clr_ctx=f"MDR={mdr:+.2f}, Fiber CLR={fiber_clr}",
                    confidence="high")
            elif mucin_evenness is not None and mucin_evenness < 0.4:
                # Low evenness = Akkermansia monoculture — aggressive displacement even if diet-fed
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
        # Find highest-priority beneficial guild
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
            # Bifido absent/depleted → Mix 2 (keystone restoration)
            if bifido_pct <= 0.5:
                return _make_result(2, "Bifidogenic Restore",
                    f"Bifidobacteria {bifido.get('status', 'depleted')} ({bifido_pct:.1f}%, score={top_score:.1f}) — keystone guild failure",
                    clr_ctx=f"Bifido CLR={bifido_clr:+.2f}" if bifido_clr is not None else "",
                    confidence="high",
                    alt="Mix 1 if multiple guilds also depleted")
            # Bifido present but depleted → Mix 2
            if bifido_pct < 3.0:
                return _make_result(2, "Bifidogenic Restore",
                    f"Bifidobacteria depleted ({bifido_pct:.1f}%, score={top_score:.1f}) — lactate amplifier compromised",
                    clr_ctx=f"Bifido CLR={bifido_clr:+.2f}" if bifido_clr is not None else "",
                    confidence="high" if top_score >= 5.0 else "medium")
            # Bifido within range but under pressure (CLR declining)
            return _make_result(2, "Bifidogenic Restore",
                f"Bifidobacteria under pressure ({bifido_pct:.1f}%, CLR={bifido_clr:+.2f}, score={top_score:.1f})" if bifido_clr else f"Bifidobacteria under pressure ({bifido_pct:.1f}%, score={top_score:.1f})",
                confidence="medium",
                alt="Monitor if CLR stabilizes")

        # B2: Fiber is highest priority
        if top_key == "fiber" and top_score >= 2.0:
            # Fiber substrate-limited (CLR > -0.3 = not actively outcompeted → feed them)
            if fiber_clr is not None and fiber_clr > -0.3:
                return _make_result(3, "Fiber & SCFA Restoration",
                    f"Fiber below range ({fiber_pct:.1f}%, score={top_score:.1f}, CLR={fiber_clr:+.2f}) — substrate provisioning needed",
                    clr_ctx=f"Fiber CLR={fiber_clr:+.2f} ({'winning but starved' if fiber_clr > 0.3 else 'neutral — can benefit from substrate'})",
                    confidence="high" if top_score >= 5.0 else "medium",
                    alt="Mix 8 if Mucin also elevated")
            # Fiber being actively outcompeted (CLR < -0.3)
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
            # CLR not available → default to substrate support
            return _make_result(3, "Fiber & SCFA Restoration",
                f"Fiber below range ({fiber_pct:.1f}%, score={top_score:.1f}) — substrate support",
                confidence="medium")

        # B3: Butyrate is highest priority
        if top_key == "butyrate" and top_score >= 2.0:
            # Check upstream dependencies
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

        # B4: Cross-feeders highest priority → broad recovery (trophic bridge)
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
            if mdr is not None and mdr > 0.5:
                return _make_result(8, "Fiber Expansion & Competitive Displacement",
                    f"Mucin overgrowth ({mucin_pct:.1f}%, MDR={mdr:+.2f} mucus-dependent)",
                    clr_ctx=f"MDR={mdr:+.2f}",
                    confidence="high")
            return _make_result(5, "Mucus Barrier Restoration",
                f"Mucin overgrowth ({mucin_pct:.1f}%, score={scores['mucin']:.1f}) — barrier support",
                clr_ctx=f"MDR={mdr}" if mdr is not None else "",
                confidence="high")

        # C3: All guilds at Monitor → Maintenance
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


# ─── OFFLINE PREBIOTIC DESIGN (mix-aware) ─────────────────────────────────────

def design_prebiotics_offline(unified_input: Dict, rule_outputs: Dict, mix_selection: Dict) -> Dict:
    """Mix-aware prebiotic design using synbiotic_mixes.json default formulas.

    Logic:
    1. Look up the selected mix's default prebiotic formula
    2. Check for high-sensitivity contradictions → apply overrides
    3. Scale to fit within allowed gram range
    4. Return structured prebiotic blend
    """
    sensitivity = rule_outputs["sensitivity"]
    prebiotic_range = rule_outputs["prebiotic_range"]
    digestive = unified_input.get("questionnaire", {}).get("digestive", {})
    bloating = digestive.get("bloating_severity", 0) or 0

    mix_id = mix_selection.get("mix_id")
    if mix_id is None:
        mix_id = 6  # Default to maintenance if MIXED flag

    # Load mix's default prebiotic formula from synbiotic_mixes.json
    try:
        mixes_kb = _load_kb("synbiotic_mixes.json")
        mix_data = mixes_kb["mixes"].get(str(mix_id), {})
    except Exception:
        mix_data = {}

    # Determine which formula to use
    is_high_sensitivity = sensitivity.get("classification") == "high"
    is_gassy = bloating >= 7

    # Check for alternative formulas
    if is_gassy and "gassy_client_formula" in mix_data:
        formula = mix_data["gassy_client_formula"]
        strategy = f"Mix {mix_id} gassy client formula (bloating {bloating}/10)"
        overrides = [f"Using gassy_client_formula due to bloating {bloating}/10"]
    elif is_high_sensitivity and "fodmap_sensitive_formula" in mix_data:
        formula = mix_data["fodmap_sensitive_formula"]
        strategy = f"Mix {mix_id} FODMAP-sensitive formula (high sensitivity)"
        overrides = ["Using FODMAP-sensitive formula due to high sensitivity"]
    else:
        formula = mix_data.get("default_prebiotic_formula", {})
        strategy = f"Mix {mix_id} ({mix_data.get('mix_name', '?')}) default formula"
        overrides = []

    # Extract components
    components = formula.get("components", [])

    # Scale to fit within allowed range
    formula_total = sum(c.get("dose_g", 0) for c in components)
    max_g = prebiotic_range.get("max_g", 8)
    min_g = prebiotic_range.get("min_g", 4)

    prebiotics = []
    if formula_total > 0 and components:
        # Scale factor to fit within range
        if formula_total > max_g:
            scale = max_g / formula_total
            overrides.append(f"Scaled down from {formula_total}g to {max_g}g (sensitivity clamp)")
        elif formula_total < min_g:
            scale = min_g / formula_total
            overrides.append(f"Scaled up from {formula_total}g to {min_g}g")
        else:
            scale = 1.0

        for c in components:
            dose = round(c.get("dose_g", 0) * scale, 2)
            if dose > 0:
                prebiotics.append({
                    "substance": c.get("substance", "Unknown"),
                    "dose_g": dose,
                    "fodmap": c.get("fodmap", False),
                    "rationale": c.get("rationale", formula.get("rationale", "")),
                })
    else:
        # Fallback — generic PHGG-moderate if no formula found
        target = (min_g + max_g) / 2
        prebiotics = [
            {"substance": "PHGG", "dose_g": round(target * 0.5, 2), "fodmap": False, "rationale": "Safe base fiber (no mix formula found)"},
            {"substance": "Beta-Glucans", "dose_g": round(target * 0.3, 2), "fodmap": False, "rationale": "Butyrate substrate"},
            {"substance": "GOS", "dose_g": round(target * 0.2, 2), "fodmap": True, "rationale": "Bifidogenic"},
        ]
        strategy = "Generic PHGG-moderate fallback (no mix formula available)"

    total_g = round(sum(p["dose_g"] for p in prebiotics), 2)
    total_fodmap = round(sum(p["dose_g"] for p in prebiotics if p["fodmap"]), 2)

    # Check for contradictions
    contradictions = []
    if is_gassy:
        contradictions.append(f"bloating {bloating}/10")
    if is_high_sensitivity:
        contradictions.append("high sensitivity classification")

    # Phased dosing — computed here for offline fallback (LLM path has it in the rationale text)
    half_dose = round(total_g * 0.5, 1)
    try:
        from pathlib import Path as _Path
        import json as _json
        _dfr_path = _Path(__file__).parent / "knowledge_base" / "delivery_format_rules.json"
        with open(_dfr_path, 'r', encoding='utf-8') as _f:
            _dfr = _json.load(_f)
        _policy = _dfr.get("phased_dosing_policy", {})
        _template = _policy.get("instruction_template", "Weeks 1–2: {half_dose_g}g daily. Week 3+: {full_dose_g}g daily.")
        _instruction = _template.replace("{half_dose_g}", str(half_dose)).replace("{full_dose_g}", str(total_g))
        _rationale = _policy.get("rationale", "")
    except Exception:
        _instruction = f"Weeks 1–2: {half_dose}g daily. Week 3+: {total_g}g daily."
        _rationale = ""

    return {
        "strategy": strategy,
        "total_grams": total_g,
        "total_fodmap_grams": total_fodmap,
        "contradictions_found": contradictions,
        "overrides_applied": overrides,
        "prebiotics": prebiotics,
        "condition_specific_additions": [],
        "phased_dosing": {
            "weeks_1_2_g": half_dose,
            "weeks_3_plus_g": total_g,
            "instruction": _instruction,
            "rationale": _rationale,
        },
    }


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def lookup_strains_for_mix(mix_id: int) -> list:
    """Look up canonical strains for a mix from synbiotic_mixes.json."""
    try:
        mixes_kb = _load_kb("synbiotic_mixes.json")
        mix_data = mixes_kb["mixes"].get(str(mix_id), {})
        return mix_data.get("strains", [])
    except Exception:
        return []


def run_llm_decisions(
    unified_input: Dict,
    rule_outputs: Dict,
    use_bedrock: bool = True
) -> Dict:
    """
    Run formulation decisions: deterministic mix + 2 LLM calls (or offline fallback).

    Architecture:
      - Mix selection: ALWAYS deterministic (LLMs unreliable with numerical thresholds)
      - Strain lookup: ALWAYS from synbiotic_mixes.json knowledge base
      - Supplement selection: LLM (qualitative clinical judgment) or offline skeleton
      - Prebiotic design: LLM (customization judgment) or offline mix-aware formula

    Returns: {mix_selection, supplement_selection, prebiotic_design}
    """
    if use_bedrock and not HAS_BOTO3:
        print("⚠️ boto3 not available — using offline fallback")
        use_bedrock = False

    # Mix selection: ALWAYS deterministic (never LLM)
    print("  📋 Mix selection (deterministic rules — never LLM)...")
    mix_result = select_mix_offline(unified_input, rule_outputs)

    # Look up canonical strains from knowledge base
    if mix_result.get("mix_id"):
        kb_strains = lookup_strains_for_mix(mix_result["mix_id"])
        if kb_strains:
            # Merge KB strains with any LP815 already added
            existing_lp815 = [s for s in mix_result.get("strains", []) if "LP815" in s.get("name", "")]

            # Assign cfu_billions to KB strains (KB doesn't store CFU — distribute base 50B evenly)
            base_cfu = 50  # Base mix CFU (excluding LP815 enhancement)
            from weight_calculator import distribute_cfu_evenly
            cfu_per_strain = distribute_cfu_evenly(base_cfu, len(kb_strains))
            for strain in kb_strains:
                if "cfu_billions" not in strain:
                    strain["cfu_billions"] = cfu_per_strain

            mix_result["strains"] = kb_strains + existing_lp815
            _lp815_label = f" + LP815 {existing_lp815[0]['cfu_billions']}B (psychobiotic)" if existing_lp815 else ""
            print(f"    Strains: {len(kb_strains)} base ({cfu_per_strain}B each){_lp815_label}")

    # Update prebiotic range with actual mix CFU
    from rules_engine import calculate_prebiotic_range
    prebiotic_range = calculate_prebiotic_range(
        rule_outputs["sensitivity"],
        cfu_billions=mix_result.get("total_cfu_billions", 50),
        mix_id=mix_result.get("mix_id")
    )
    rule_outputs["prebiotic_range"] = prebiotic_range

    # LLM Call 1 (of 2): Supplement selection
    if use_bedrock:
        print("  🧠 LLM Call 1/2: Supplement selection...")
        supplement_result = select_supplements(unified_input, rule_outputs)
    else:
        print("  📋 Offline: Supplement selection (skeleton)...")
        supplement_result = {
            "vitamins_minerals": [],
            "supplements": [],
            "omega3": {"dose_daily_mg": 1425, "dose_per_softgel_mg": 712.5, "rationale": "Default omega-3"},
            "existing_supplements_advice": [],
        }

    # LLM Call 2 (of 2): Prebiotic design
    if use_bedrock:
        print("  🧠 LLM Call 2/2: Prebiotic design...")
        prebiotic_result = design_prebiotics(unified_input, rule_outputs, mix_result)
    else:
        print("  📋 Offline: Prebiotic design (mix-aware)...")
        prebiotic_result = design_prebiotics_offline(unified_input, rule_outputs, mix_result)

    return {
        "mix_selection": mix_result,
        "supplement_selection": supplement_result,
        "prebiotic_design": prebiotic_result,
    }


# ─── FOCUSED LLM: POLYPHENOL CONFLICT RESOLUTION ─────────────────────────────

def resolve_polyphenol_conflict(
    polyphenol_items: list,
    client_goals: list,
    mix_name: str,
    mix_trigger: str,
    use_bedrock: bool = True,
) -> Optional[str]:
    """LLM-informed polyphenol dropping when the 1000mg cap is exceeded.

    Instead of the fixed drop order (sachet-safe → Tier 1 → Tier 2), asks the LLM
    which polyphenol is LEAST relevant to this client's goals + microbiome pattern.

    Args:
        polyphenol_items: List of dicts, each with keys:
            - substance (str): e.g. "Quercetin", "Apple polyphenol extract"
            - dose_mg (float): current dose
            - health_claim (str): which health category it addresses
            - tier (str|int): "sachet", 1, or 2
        client_goals: Ranked list of client goal strings
        mix_name: Selected synbiotic mix name (microbiome context)
        mix_trigger: Primary trigger description (microbiome summary)
        use_bedrock: Whether Bedrock is available

    Returns:
        Substance name to DROP (lowercase), or None if LLM call fails.
        Caller must fall back to fixed priority order when None is returned.
    """
    if not use_bedrock or not HAS_BOTO3:
        return None  # Caller uses deterministic fallback

    if len(polyphenol_items) < 2:
        return None  # Nothing to choose between

    system = (
        "You are a clinical nutritionist resolving a polyphenol capacity conflict. "
        "The client's formulation exceeds the 1000mg polyphenol cap. "
        "You must choose ONE polyphenol to REMOVE — the one LEAST relevant to this "
        "client's primary goals and microbiome pattern. "
        "Respond with ONLY a JSON object: {\"drop\": \"<exact substance name>\"}"
    )

    options_lines = []
    for item in polyphenol_items:
        options_lines.append(
            f"- {item['substance']} ({item['dose_mg']}mg) — claim: {item.get('health_claim', 'general')}, tier: {item.get('tier', '?')}"
        )

    user = (
        f"Client goals (ranked): {json.dumps(client_goals)}\n"
        f"Microbiome pattern: {mix_name} — {mix_trigger}\n\n"
        f"Polyphenols in formulation (total exceeds 1000mg cap):\n"
        + "\n".join(options_lines) + "\n\n"
        f"Which ONE polyphenol should be REMOVED? Pick the one least relevant "
        f"to the primary goal. Keep the one most aligned with the #1 goal.\n"
        f"Return ONLY: {{\"drop\": \"<substance name>\"}}"
    )

    try:
        response_text = _call_bedrock(system, user, max_tokens=200)
        result = _extract_json_from_response(response_text)
        drop_name = result.get("drop", "").strip()
        if not drop_name:
            return None
        # Validate the LLM returned a name that actually exists in our list
        known_names = {item["substance"].lower() for item in polyphenol_items}
        if drop_name.lower() in known_names:
            return drop_name.lower()
        # Fuzzy match — LLM may return partial name
        for known in known_names:
            if drop_name.lower() in known or known in drop_name.lower():
                return known
        print(f"  ⚠️ LLM polyphenol drop response '{drop_name}' not in known items — falling back to deterministic")
        return None
    except Exception as e:
        print(f"  ⚠️ LLM polyphenol conflict resolution failed: {e} — falling back to deterministic")
        return None


# ─── FOCUSED LLM: MINERAL CONFLICT RESOLUTION ────────────────────────────────

def resolve_mineral_conflict(
    mineral_a: str,
    mineral_a_dose: str,
    mineral_a_claim: str,
    mineral_b: str,
    mineral_b_dose: str,
    mineral_b_claim: str,
    client_goals: list,
    use_bedrock: bool = True,
) -> Optional[str]:
    """LLM-informed mineral conflict resolution for TIED goal-relevance scores.

    Only called when deterministic scoring produces equal scores for both minerals
    in an absorption conflict pair. If one mineral clearly wins on goal relevance,
    the deterministic logic handles it without an LLM call.

    Args:
        mineral_a/b: Mineral names (e.g. "Zinc", "Copper")
        mineral_a/b_dose: Dose strings (e.g. "8mg", "2mg")
        mineral_a/b_claim: Health claim category each addresses
        client_goals: Ranked list of client goal strings
        use_bedrock: Whether Bedrock is available

    Returns:
        Mineral name to KEEP (lowercase), or None if LLM call fails.
        Caller must fall back to existing behavior when None is returned.
    """
    if not use_bedrock or not HAS_BOTO3:
        return None  # Caller uses deterministic fallback

    system = (
        "You are a clinical nutritionist resolving a mineral absorption conflict. "
        "Two minerals compete for absorption and cannot be co-administered effectively. "
        "You must choose ONE mineral to KEEP — the one MORE important for this client. "
        "Respond with ONLY a JSON object: {\"keep\": \"<mineral name>\"}"
    )

    user = (
        f"Client goals (ranked): {json.dumps(client_goals)}\n\n"
        f"Two minerals conflict (absorption competition):\n"
        f"- {mineral_a} ({mineral_a_dose}) — addresses: {mineral_a_claim}\n"
        f"- {mineral_b} ({mineral_b_dose}) — addresses: {mineral_b_claim}\n\n"
        f"Both scored EQUALLY on goal relevance (tied). "
        f"Which ONE mineral is more important for this client's primary goal? "
        f"Consider the client's ranked goals and which mineral has broader clinical value.\n"
        f"Return ONLY: {{\"keep\": \"<mineral name>\"}}"
    )

    try:
        response_text = _call_bedrock(system, user, max_tokens=200)
        result = _extract_json_from_response(response_text)
        keep_name = result.get("keep", "").strip()
        if not keep_name:
            return None
        # Validate against the two options
        options = {mineral_a.lower(), mineral_b.lower()}
        if keep_name.lower() in options:
            return keep_name.lower()
        # Fuzzy match
        for opt in options:
            if keep_name.lower() in opt or opt in keep_name.lower():
                return opt
        print(f"  ⚠️ LLM mineral conflict response '{keep_name}' not in options {options} — falling back to deterministic")
        return None
    except Exception as e:
        print(f"  ⚠️ LLM mineral conflict resolution failed: {e} — falling back to deterministic")
        return None


# ─── OPUS LLM: ECOLOGICAL RATIONALE + INPUT NARRATIVES ───────────────────────

# ─── LLM MEDICATION INTERACTION SCREENING ─────────────────────────────────

MEDICATION_SCREENING_SYSTEM = """You are a clinical pharmacologist screening a patient's medication list against a supplement database.

You will receive:
1. The patient's complete medication list (names, dosages, duration)
2. A list of ALL available supplements, vitamins, and minerals from the pipeline's database

Your task: Identify every supplement that is CONTRAINDICATED or carries HIGH-SEVERITY interaction risk with the patient's specific medications.

CRITICAL RULES:
- Recognize brand names in ANY language (e.g., Thrombo ASS = aspirin, Concor Cor = bisoprolol, Euthyrox = levothyroxine, Ramipril = ACE inhibitor)
- Identify the pharmacological CLASS of each medication (e.g., beta-blocker, ACE inhibitor, antiplatelet, anticoagulant, SSRI, thyroid hormone, etc.)
- For each supplement, evaluate whether a HIGH-severity pharmacological interaction exists with any of the patient's medications
- HIGH severity = clinically significant risk of harm: bleeding, arrhythmia, dangerous blood pressure changes, serotonin syndrome, thyroid interference, etc.
- Do NOT flag LOW-severity or theoretical interactions (e.g., "may slightly affect absorption" is NOT high severity)
- Do NOT flag timing-based interactions that can be resolved by spacing doses (e.g., "take 2 hours apart")
- ONLY flag interactions where the supplement should be COMPLETELY EXCLUDED from the formulation

Return ONLY a JSON object:
{
  "excluded_substances": ["substance_name_1", "substance_name_2"],
  "exclusion_reasons": [
    {
      "substance": "substance_name_1",
      "medication": "medication_name (class)",
      "mechanism": "Brief pharmacological explanation of the interaction",
      "severity": "high"
    }
  ]
}

If no high-severity interactions are found, return:
{"excluded_substances": [], "exclusion_reasons": []}"""


def screen_medication_interactions(unified_input: Dict, use_bedrock: bool = True) -> Dict:
    """LLM-driven medication interaction screening.

    Scans the client's medication list against the complete supplement/vitamin
    database and returns a definitive exclusion list of substances that must
    never be added to this client's formulation.

    This replaces all hardcoded medication keyword matching. The LLM recognizes
    brand names in any language and understands pharmacological classes.

    Args:
        unified_input: Parsed pipeline input (contains questionnaire.medical.medications)
        use_bedrock: Whether Bedrock LLM is available

    Returns:
        {
            "excluded_substances": set of lowercased substance names,
            "exclusion_reasons": list of {substance, medication, mechanism, severity},
            "skipped": bool (True if LLM unavailable)
        }
    """
    if not use_bedrock or not HAS_BOTO3:
        return {
            "excluded_substances": set(),
            "exclusion_reasons": [],
            "skipped": True,
        }

    medications = unified_input.get("questionnaire", {}).get("medical", {}).get("medications", [])
    if not medications:
        return {
            "excluded_substances": set(),
            "exclusion_reasons": [],
            "skipped": False,
        }

    # Format medications for the prompt
    med_lines = []
    for m in medications:
        if isinstance(m, dict):
            med_lines.append(f"- {m.get('name', '')} {m.get('dosage', '')} ({m.get('how_long', '')})")
        else:
            med_lines.append(f"- {m}")
    meds_formatted = "\n".join(med_lines) if med_lines else "None reported"

    # Build compact substance list from both KBs
    substance_lines = []
    try:
        supplements_kb = _load_kb("supplements_nonvitamins.json")
        for entry in supplements_kb.get("supplements_flat", []):
            substance = entry.get("substance", "")
            interaction_risk = entry.get("interaction_risk", "")
            cautions = entry.get("cautions", {})
            cautions_str = ""
            if cautions:
                cautions_str = f" | Cautions: {json.dumps(cautions)}"
            substance_lines.append(
                f"- {substance} (risk: {interaction_risk}){cautions_str}"
            )
    except Exception:
        pass

    try:
        vitamins_kb = _load_kb("vitamins_minerals.json")
        for entry in vitamins_kb.get("vitamins_and_minerals", []):
            substance = entry.get("substance", "")
            interaction = entry.get("interaction_note", "")
            substance_lines.append(
                f"- {substance} (interaction note: {interaction})"
            )
    except Exception:
        pass

    # Also include prebiotic substances
    prebiotic_substances = [
        "PHGG", "Psyllium Husk", "Pure Inulin", "FOS (Oligofructose)",
        "GOS (Galactooligosaccharides)", "Beta-Glucans", "Resistant Starch",
        "Glucomannan", "Lactulose"
    ]
    for ps in prebiotic_substances:
        substance_lines.append(f"- {ps} (prebiotic fiber)")

    user_prompt = f"""## Patient Medications
{meds_formatted}

## Available Supplements, Vitamins, Minerals, and Prebiotics
{chr(10).join(substance_lines)}

Identify ALL supplements from the list above that have HIGH-severity pharmacological interactions with this patient's specific medications. Remember to identify the pharmacological class of each medication (brand names may be in any language).

Return ONLY the JSON response."""

    try:
        response_text = _call_bedrock(
            MEDICATION_SCREENING_SYSTEM, user_prompt,
            max_tokens=2000, temperature=0.1
        )
        result = _extract_json_from_response(response_text)

        # Normalize excluded substances to lowercase set
        raw_excluded = result.get("excluded_substances", [])
        excluded_set = set()
        for name in raw_excluded:
            excluded_set.add(name.lower().strip())

        return {
            "excluded_substances": excluded_set,
            "exclusion_reasons": result.get("exclusion_reasons", []),
            "skipped": False,
        }
    except Exception as e:
        print(f"  ⚠️ Medication interaction screening failed: {e}")
        return {
            "excluded_substances": set(),
            "exclusion_reasons": [],
            "skipped": True,
        }


OPUS_MODEL_ID = 'eu.anthropic.claude-opus-4-6-v1'

def generate_ecological_rationale(unified_input: Dict, mix_result: Dict) -> Dict:
    """Generate ecological rationale for mix selection when alternatives exist.
    Uses Opus for deeper scientific reasoning."""
    alternative = mix_result.get("alternative_considered", "")
    if not alternative:
        return {}

    guilds = unified_input["microbiome"]["guilds"]
    clr = unified_input["microbiome"]["clr_ratios"]

    guild_lines = []
    for gk, gv in guilds.items():
        clr_val = gv.get("clr")
        clr_str = "CLR %.2f" % clr_val if isinstance(clr_val, (int, float)) else "CLR N/A"
        guild_lines.append("%s: %.1f%% (%s) %s priority=%s" % (
            gv.get("name", gk), gv.get("abundance_pct", 0),
            gv.get("status", "?"), clr_str, gv.get("priority_level", "?")))

    prompt = """You are a microbiome ecologist assessing a probiotic mix selection decision.

SELECTED: Mix %d (%s)
Trigger: %s
ALTERNATIVE CONSIDERED: %s
CLR Context: %s

GUILD DATA:
%s

CLR RATIOS: CUR=%s, FCR=%s, MDR=%s, PPR=%s

Explain in 3-4 sentences each:
1. Why the SELECTED mix is ecologically appropriate
2. Why the ALTERNATIVE was NOT chosen (ecological tradeoffs)
3. Whether a COMBINED strategy could work (be honest about constraints)
4. Your recommendation

Return ONLY a JSON object:
{
  "selected_rationale": "...",
  "alternative_analysis": "...",
  "combined_assessment": "...",
  "recommendation": "..."
}""" % (
        mix_result.get("mix_id", 0), mix_result.get("mix_name", "?"),
        mix_result.get("primary_trigger", "?"), alternative,
        mix_result.get("clr_context", ""),
        "\n".join(guild_lines),
        clr.get("CUR"), clr.get("FCR"), clr.get("MDR"), clr.get("PPR"))

    try:
        response = _call_bedrock(
            "You are a microbiome ecologist. Provide objective, evidence-based ecological analysis. Be concise and scientifically precise. IMPORTANT: NEVER use 'you' or 'your' — always use 'this client', 'the individual', or 'this sample's'.",
            prompt, max_tokens=1500, model_id=OPUS_MODEL_ID)
        return _extract_json_from_response(response)
    except Exception as e:
        print("  Warning: Ecological rationale generation failed: %s" % e)
        return {"error": str(e)}


def generate_input_narratives(unified_input: Dict, rule_outputs: Dict) -> Dict:
    """Generate human-readable narrative summaries of microbiome and questionnaire inputs.
    Uses Opus for natural language."""
    guilds = unified_input["microbiome"]["guilds"]
    clr = unified_input["microbiome"]["clr_ratios"]
    q = unified_input["questionnaire"]
    score = unified_input["microbiome"]["overall_score"]

    guild_lines = []
    for gk, gv in guilds.items():
        guild_lines.append("%s: %.1f%% (%s)" % (
            gv.get("name", gk), gv.get("abundance_pct", 0), gv.get("status", "?")))

    prompt = """Summarize these microbiome and questionnaire inputs as TWO short narrative paragraphs (3-4 sentences each) for a scientific board review. Write in third person. No jargon. Make it readable.

MICROBIOME:
Overall Score: %s/100 (%s)
Guilds: %s
CLR Ratios: CUR=%s, FCR=%s, MDR=%s, PPR=%s

QUESTIONNAIRE:
Age: %s, Sex: %s
Goals: %s
Stress: %s/10, Sleep: %s/10, Bloating: %s/10
Sensitivity: %s
Completion: %s%%

Return ONLY a JSON:
{
  "microbiome_narrative": "...",
  "questionnaire_narrative": "..."
}""" % (
        score.get("total", "?"), score.get("band", "?"),
        "; ".join(guild_lines),
        clr.get("CUR"), clr.get("FCR"), clr.get("MDR"), clr.get("PPR"),
        q.get("demographics", {}).get("age", "?"),
        q.get("demographics", {}).get("biological_sex", "?"),
        ", ".join(q.get("goals", {}).get("ranked", [])[:3]),
        q.get("lifestyle", {}).get("stress_level", "?"),
        q.get("lifestyle", {}).get("sleep_quality", "?"),
        q.get("digestive", {}).get("bloating_severity", "?"),
        rule_outputs.get("sensitivity", {}).get("classification", "?"),
        q.get("completion", {}).get("completion_pct", 0))

    try:
        response = _call_bedrock(
            "You are writing readable summaries for a scientific review board. Be clear, professional, and human-readable. No underscores or technical keys. IMPORTANT: NEVER use 'you' or 'your' — always use 'this client', 'the individual', or 'this sample's'.",
            prompt, max_tokens=800, model_id=OPUS_MODEL_ID)
        return _extract_json_from_response(response)
    except Exception as e:
        print("  Warning: Input narrative generation failed: %s" % e)
        return {"microbiome_narrative": "", "questionnaire_narrative": ""}


# ─── FORMULATION SANITY CHECK (LLM post-pipeline review) ─────────────────────

SANITY_CHECK_SYSTEM = """You are a formulation QA engineer checking a manufacturing recipe for INTERNAL CONSISTENCY and STRUCTURAL CORRECTNESS. You are NOT a nutritionist — do NOT comment on whether supplements are clinically appropriate or needed. The clinical decisions have already been validated upstream.

Your job: check the recipe for FORMULATION ERRORS only — specifically, cases where the recipe CONTRADICTS the provided knowledge base reference data.

You will receive AUTHORITATIVE REFERENCE DATA from the pipeline's knowledge base files. Use this as ground truth when evaluating the recipe. Only flag issues where the recipe deviates from or contradicts the KB specifications.

CHECK FOR THESE ISSUES ONLY:
1. Capsule fill efficiency — any capsule with <10% fill is wasteful (e.g., 6mg in a 650mg capsule)
2. Dose inconsistency — same ingredient showing different amounts in different sections
3. Missing components — an ingredient listed in one section but absent from the manufacturing recipe
4. Capacity violations — a unit exceeding the capacity specified in the DELIVERY FORMAT REFERENCE for its format type
5. Timing contradictions — a substance scheduled for a timing that CONTRADICTS its timing_restriction in the SUPPLEMENT TIMING REFERENCE (e.g., a morning_only substance in evening, or an evening_ok substance in morning when it should be evening)
6. Zero-dose ingredients — any ingredient at 0mg/0g that shouldn't be
7. Dose vs KB mismatch — a vitamin/mineral at a dose that significantly differs from its KB standard dose (check VITAMIN/MINERAL DOSE REFERENCE)

DO NOT FLAG:
- Units that match their KB specifications (e.g., a softgel with daily total matching KB daily_totals is CORRECT)
- Fixed-format units operating as specified in the KB (e.g., Magnesium Bisglycinate Capsule is ALWAYS 750mg fill — this is correct per KB)
- A substance with timing_restriction "morning_only" scheduled for morning — this is CORRECT
- A substance with timing_restriction "evening_ok" scheduled for evening — this is CORRECT
- Whether supplement selections are clinically appropriate
- Whether vitamins are needed or redundant
- Whether prebiotics are the right choice
- The probiotic capsule content (locked, never review)

Return ONLY a JSON object:
{
  "warnings": [
    {"severity": "error|warning|info", "unit": "unit name", "issue": "short description", "suggestion": "what to do"}
  ],
  "overall_assessment": "one sentence summary"
}

If everything looks structurally correct, return:
{"warnings": [], "overall_assessment": "Formulation structurally consistent — no QA issues found."}"""


def analyze_questionnaire_clinical(unified_input: Dict, use_bedrock: bool = True) -> Dict:
    """Analyze the full questionnaire for clinical profile, inferred health signals, and review flags.
    
    Returns:
        {
          "profile_narrative": ["bullet 1", "bullet 2", ...],
          "inferred_health_signals": [
              {"signal": "infection_susceptibility", "reason": "UTI 1-2x/year + slow recovery"},
              ...
          ],
          "clinical_review_flags": [
              {"severity": "high", "title": "...", "detail": "..."},
              ...
          ]
        }
    
    profile_narrative: bullet-point clinical summary for display in terminal + HTML.
    inferred_health_signals: list of {signal, reason} objects — reasons shown in decision trace.
    clinical_review_flags: display-only alerts for human reviewer — no pipeline changes.
    """
    if not use_bedrock or not HAS_BOTO3:
        return {
            "profile_narrative": [],
            "inferred_health_signals": [],
            "clinical_review_flags": [],
            "skipped": True,
        }
    
    q = unified_input.get("questionnaire", {})
    demographics = q.get("demographics", {})
    lifestyle = q.get("lifestyle", {})
    medical = q.get("medical", {})
    digestive = q.get("digestive", {})
    goals = q.get("goals", {}).get("ranked", [])
    diet = q.get("diet", {})
    food_triggers = q.get("food_triggers", {})
    exercise = lifestyle.get("exercise_detail", {})

    # Format medications clearly for the LLM (use the fixed field)
    meds = medical.get("medications", [])
    meds_formatted = "; ".join(
        f"{m.get('name','')} {m.get('dosage','')} ({m.get('how_long','')})" if isinstance(m, dict) else str(m)
        for m in meds
    ) if meds else "None reported"

    # Format family history
    fh = medical.get("family_history", {})
    fh_parts = []
    if isinstance(fh, dict):
        for condition, data in fh.items():
            if isinstance(data, dict) and data.get("has"):
                relatives = data.get("relatives", "unknown relative")
                fh_parts.append(f"{condition} ({relatives})")
    fh_formatted = "; ".join(fh_parts) if fh_parts else "None reported"

    # Format exercise
    ex_types = exercise.get("types", [])
    ex_str = (
        f"{', '.join(ex_types) if ex_types else 'not specified'} | "
        f"moderate {exercise.get('moderate_days_per_week','?')}x/week, "
        f"vigorous {exercise.get('vigorous_days_per_week','?')}x/week | "
        f"steps/day: {exercise.get('avg_daily_steps','?')} | "
        f"sitting: {exercise.get('hours_sitting_per_day','?')}h/day"
    )

    system_prompt = """You are a clinical nutritionist conducting a detailed clinical review of a patient questionnaire before formulating a personalised supplement plan.

Your task is to:
1. Write a thorough bullet-point clinical profile summarising the most clinically important information
2. Identify symptoms or conditions that imply additional health claims NOT explicitly stated as goals, with a clear reason for each
3. Flag EVERYTHING that requires human clinical review — especially medications, diagnoses, or conditions that could interact with supplements

CRITICAL RULES — READ CAREFULLY:
- EVERY medication listed in the "Medications" field MUST appear in profile_narrative and be checked for supplement interactions in clinical_review_flags. Do not skip or omit any medication even if the name appears in a non-English format.
- L-Thyroxin, Levothyroxine, Synthroid, Euthyrox — ALL are thyroid replacement hormones. Flag them as HIGH severity (timing conflict with all morning supplements) AND flag Ashwagandha interaction (may interfere with thyroid hormone levels).
- Any thyroid diagnosis (Hashimoto's, hypothyroidism) combined with iodine supplementation requires a HIGH severity flag (excess iodine can trigger Hashimoto's flares).
- Any diagnosis of food_allergies_intolerances requires a MEDIUM severity flag listing reported allergens.
- Drug allergies (e.g. Penicillin) must be listed in profile_narrative.
- Family history of cancer, diabetes, autoimmune, cardiovascular should each be noted in profile_narrative.
- Skin concerns that are "persistent" with known triggers (diet, stress, weather) = confirmed skin_quality signal.
- UTI 1-2x/year = infection_susceptibility signal.
- Low step count (<5000/day) + high sitting time (≥8h) should be noted as metabolic risk context.

Return ONLY valid JSON in this exact structure:
{
  "profile_narrative": [
    "• Demographic: ...",
    "• Goals: ...",
    "• Diagnoses: ...",
    "• Medications: ...",
    "• Allergies: ...",
    "• Skin: ...",
    "• Digestive: ...",
    "• Triggers: ...",
    "• Lifestyle: ...",
    "• Exercise: ...",
    "• Family history: ..."
  ],
  "inferred_health_signals": [
    {"signal": "skin_quality", "reason": "Persistent frequent acne + itchy/dry/sensitive skin; correlates with diet, stress, weather"},
    {"signal": "infection_susceptibility", "reason": "UTI 1-2x/year + 2-3 colds/year + slow recovery (2-4 weeks)"}
  ],
  "clinical_review_flags": [
    {
      "severity": "high",
      "title": "L-Thyroxin timing conflict",
      "detail": "Patient takes L-Thyroxin 175mg daily (Hashimoto's, since 2018). Thyroid hormone must be taken on an empty stomach 30-60 min before any food or supplements. ALL morning capsules must be timed at least 30-60 min after L-Thyroxin intake."
    }
  ]
}

Valid signal values for inferred_health_signals:
["infection_susceptibility", "skin_quality", "bowel_function", "fatigue", "immune_system", "stress_anxiety", "sleep_quality", "hormone_balance", "anti_inflammatory", "heart_health", "weight_management", "bone_health"]

Severity values: "high" (supplement interaction / contraindication), "medium" (caution / monitoring needed), "low" (informational)."""

    user_prompt = f"""Full patient questionnaire:

DEMOGRAPHICS:
Sex: {demographics.get('biological_sex', 'unknown')} | Age: {demographics.get('age', '?')}
Weight: {demographics.get('weight_kg', '?')}kg | Height: {demographics.get('height_cm', '?')}cm | BMI: {demographics.get('bmi', '?')}
Country: {demographics.get('country', '?')} | Work environment: {demographics.get('occupation_environment', '?')}

GOALS (ranked by client):
{chr(10).join(f"  {i+1}. {g.replace('_',' ')}" for i, g in enumerate(goals))}

DIAGNOSES: {', '.join(medical.get('diagnoses', [])) or 'None'}
MEDICATIONS (from other_medications field — list ALL): {meds_formatted}
Drug allergies: {medical.get('drug_allergies', '') or 'None reported'} (has_allergy={medical.get('drug_allergies_has', 'no')})
NSAIDs: {medical.get('nsaid_which', '') or 'None'} ({medical.get('nsaid_use', '')})
Reported vitamin deficiencies: {medical.get('vitamin_deficiencies', []) or 'None'}

SKIN:
Concerns: {medical.get('skin_concerns', [])}
Persistence: {medical.get('skin_persistence', 'unknown')}
Frequency: {medical.get('skin_issues_frequency', 'unknown')}
Change patterns: {medical.get('skin_change_patterns', 'unknown')}

DIGESTIVE:
Satisfaction: {digestive.get('digestive_satisfaction', '?')}/10
Bloating: {digestive.get('bloating_frequency', '?')} | when: {digestive.get('bloating_when', [])}
Abdominal pain: severity {digestive.get('abdominal_pain_severity', '?')}/10 | freq: {digestive.get('abdominal_pain_frequency', '?')} | character: {digestive.get('abdominal_pain_character', [])}
Colon symptoms: {medical.get('colon_symptoms', [])}
Motility details: {medical.get('motility_details', '') or 'None'}
Gut-brain symptoms: {medical.get('gut_brain_symptoms', [])}
Stress worsens digestion: {digestive.get('digestive_symptoms_with_stress', '?')}

TRIGGER FOODS: {food_triggers.get('triggers', [])}
Colon triggers (freetext): {food_triggers.get('colon_triggers_text', '') or 'None'}

LIFESTYLE:
Stress: {lifestyle.get('stress_level', '?')}/10 | symptoms: {lifestyle.get('stress_symptoms', [])}
Sleep: {lifestyle.get('sleep_quality', '?')}/10 | duration: {lifestyle.get('sleep_duration', '?')}h | issues: {lifestyle.get('sleep_issues', [])}
Energy level: {lifestyle.get('energy_level', '?')}
Mental clarity: {lifestyle.get('mental_clarity', '?')}

EXERCISE: {ex_str}

DIET:
Pattern: {diet.get('diet_pattern', 'unknown')}
Whole grains: {diet.get('whole_grains', '?')} | Fiber: {diet.get('fiber_intake', '?')} | Water: {diet.get('water_intake', '?')}
Fermented foods: {diet.get('fermented_foods_frequency', '?')} | Processed: {diet.get('processed_foods_frequency', '?')}

INFECTIONS:
UTI frequency: {medical.get('uti_per_year', 'not reported')}
Colds per year: {medical.get('colds_per_year', 'not reported')}
Recovery when ill: {medical.get('infection_recovery', 'not reported')}

FAMILY HISTORY: {fh_formatted}

PREVIOUS SUPPLEMENTS: {medical.get('previous_supplements', 'none')}
Previous effect: {medical.get('previous_supplement_effect', '?')} — {medical.get('previous_supplement_notes', '')}"""

    try:
        raw = _call_bedrock(system_prompt, user_prompt, max_tokens=2000, temperature=0.2)
        result = _extract_json_from_response(raw)
        
        # Normalise inferred_health_signals — accept both old (plain strings) and new ({signal, reason}) format
        raw_signals = result.get("inferred_health_signals", [])
        normalised_signals = []
        for s in raw_signals:
            if isinstance(s, dict) and "signal" in s:
                normalised_signals.append(s)
            elif isinstance(s, str):
                # Backward compat: plain string → wrap with empty reason
                normalised_signals.append({"signal": s, "reason": ""})
        
        return {
            "profile_narrative": result.get("profile_narrative", []),
            "inferred_health_signals": normalised_signals,
            "clinical_review_flags": result.get("clinical_review_flags", []),
        }
    except Exception as e:
        print(f"  ⚠️ Clinical questionnaire analysis failed: {e}")
        return {
            "profile_narrative": [],
            "inferred_health_signals": [],
            "clinical_review_flags": [],
        }


def _build_sanity_check_kb_references() -> str:
    """Load KB files and build compact reference summaries for the sanity check prompt.
    
    Reads from the same JSON knowledge bases used by the rest of the pipeline,
    so any KB updates are automatically reflected in QA checks.
    
    Returns a formatted string with 3 reference sections:
    1. Delivery format specifications (capacity, daily_count, timing, fixed status)
    2. Supplement timing classifications (substance → timing_restriction)
    3. Vitamin/mineral standard doses (substance → dose, unit)
    """
    references = []

    # 1. DELIVERY FORMAT REFERENCE — from delivery_format_rules.json
    try:
        delivery_kb = _load_kb("delivery_format_rules.json")
        delivery_lines = ["## DELIVERY FORMAT REFERENCE (from delivery_format_rules.json)"]
        delivery_lines.append("NOTE: Architecture v3.0 — powder jar replaces sachet. Morning/Evening Wellness Capsules replace fixed single capsules.")
        for fmt_key, fmt_data in delivery_kb.get("delivery_formats", {}).items():
            label = fmt_data.get("label", fmt_key)
            capacity_mg = fmt_data.get("capacity_mg")
            capacity_per_capsule = fmt_data.get("capacity_mg_per_capsule") or capacity_mg
            capacity_g = fmt_data.get("capacity_g") or fmt_data.get("daily_target_g")
            daily_count = fmt_data.get("daily_count", 1)
            timing = fmt_data.get("timing", "?")
            fixed = fmt_data.get("fixed_composition", False)
            stacking = fmt_data.get("subject_to_stacking_optimizer", False)

            if isinstance(daily_count, str):
                cap_str = f"{capacity_per_capsule}mg/capsule" if capacity_per_capsule else "variable"
                daily_total_str = " [COUNT DETERMINED BY CapsuleStackingOptimizer]"
            elif capacity_mg:
                cap_str = f"{capacity_mg}mg"
                daily_total_str = f" → daily total: {capacity_mg * daily_count}mg across {daily_count} units" if daily_count > 1 else ""
            elif capacity_g:
                cap_str = f"{capacity_g}g (soft target)" if fmt_data.get("daily_target_g") else f"{capacity_g}g"
                daily_total_str = ""
            else:
                cap_str = "N/A"
                daily_total_str = ""

            fixed_str = " [FIXED — identical for all clients]" if fixed else ""
            stacking_str = " [CapsuleStackingOptimizer]" if stacking else ""
            delivery_lines.append(
                f"- {label}: capacity={cap_str}, timing={timing}{daily_total_str}{fixed_str}{stacking_str}"
            )

            # Show fixed daily totals if available (e.g., softgel omega + D3 + E + astaxanthin)
            daily_totals = fmt_data.get("daily_totals", {})
            if daily_totals:
                totals_parts = [f"{k}={v}" for k, v in daily_totals.items()]
                delivery_lines.append(f"  Daily totals: {', '.join(totals_parts)}")

        # Phased dosing policy
        phased = delivery_kb.get("phased_dosing_policy", {})
        if phased:
            delivery_lines.append(f"- Powder Jar: PHASED DOSING — weeks 1-2 = {phased.get('weeks_1_2_fraction',0.5)*100:.0f}% of full dose, week 3+ = full dose")

        # Also show capacity validation rules
        cap_val = delivery_kb.get("capacity_validation", {})
        for fmt_key, val_rules in cap_val.items():
            if val_rules.get("fixed") or val_rules.get("no_validation_needed"):
                delivery_lines.append(f"- {fmt_key}: FIXED format — no capacity validation needed (always correct)")

        references.append("\n".join(delivery_lines))
    except Exception as e:
        references.append(f"## DELIVERY FORMAT REFERENCE: failed to load ({e})")

    # 2. SUPPLEMENT TIMING REFERENCE — from supplements_nonvitamins.json
    try:
        supplements_kb = _load_kb("supplements_nonvitamins.json")
        timing_lines = ["## SUPPLEMENT TIMING REFERENCE (from supplements_nonvitamins.json)"]
        timing_lines.append("timing_restriction values:")
        timing_lines.append("  morning_only = MUST be morning (stimulant/energizing — NEVER schedule for evening)")
        timing_lines.append("  evening_ok = FLEXIBLE — morning OR evening both valid (this is NOT a restriction, do NOT flag morning scheduling)")
        timing_lines.append("  any = FLEXIBLE — any timing valid (not a restriction)")
        timing_lines.append("TIMING CONTRADICTION = ONLY when a morning_only substance appears in evening timing. All other combinations are CORRECT.")
        seen = set()
        for cat in supplements_kb.get("health_categories", []):
            for supp in cat.get("supplements", []):
                substance = supp.get("substance", "")
                timing_restriction = supp.get("timing_restriction", "any")
                delivery_constraint = supp.get("delivery_constraint", "any")
                if substance.lower() not in seen:
                    seen.add(substance.lower())
                    timing_lines.append(f"- {substance}: timing={timing_restriction}, delivery={delivery_constraint}")
        references.append("\n".join(timing_lines))
    except Exception as e:
        references.append(f"## SUPPLEMENT TIMING REFERENCE: failed to load ({e})")

    # 3. VITAMIN/MINERAL DOSE REFERENCE — from vitamins_minerals.json
    try:
        vitamins_kb = _load_kb("vitamins_minerals.json")
        dose_lines = ["## VITAMIN/MINERAL DOSE REFERENCE (from vitamins_minerals.json)"]
        for vm in vitamins_kb.get("vitamins_and_minerals", []):
            substance = vm.get("substance", "")
            raw_dose = vm.get("max_intake_in_supplements", "")
            timing = vm.get("timing_in_protocol", "")
            dose_lines.append(f"- {substance}: standard_dose={raw_dose}, timing={timing}")
        references.append("\n".join(dose_lines))
    except Exception as e:
        references.append(f"## VITAMIN/MINERAL DOSE REFERENCE: failed to load ({e})")

    return "\n\n".join(references)


def formulation_sanity_check(
    recipe: Dict,
    health_claims: list = None,
    client_goals: list = None,
    use_bedrock: bool = True,
) -> Dict:
    """LLM-powered sanity check of the final manufacturing recipe.

    Reviews the complete formulation for unreasonable patterns that
    deterministic rules might miss: tiny capsules, dose waste, claim
    misalignment, timing conflicts, etc.

    Now injects authoritative KB reference data (delivery formats, supplement
    timing classifications, vitamin doses) so the LLM can compare the recipe
    against the actual source-of-truth files used by the pipeline.

    Args:
        recipe: Manufacturing recipe JSON (from build_manufacturing_recipe)
        health_claims: List of health claim strings
        client_goals: Ranked list of client goal strings
        use_bedrock: Whether Bedrock LLM is available

    Returns:
        Dict with 'warnings' list and 'overall_assessment' string.
        Each warning: {severity, unit, issue, suggestion}
    """
    if not use_bedrock or not HAS_BOTO3:
        return {"warnings": [], "overall_assessment": "Sanity check skipped (offline mode)", "skipped": True}

    # Load KB reference data (read from JSON files — same source of truth as pipeline)
    kb_references = _build_sanity_check_kb_references()

    # Fixed-format units are validated deterministically by weight_calculator.py
    # (capacity checks, CFU formula, fixed composition). The LLM can't reliably
    # handle per-unit vs daily-total math (e.g., 750mg×2=1500mg softgel) and
    # generates false positives. Exclude them from the prompt entirely.
    FIXED_UNIT_LABELS = {
        "probiotic hard capsule",         # Locked composition — CFU × 10mg, validated by calc
        "probiotic capsule",              # Alternate label
        "omega + antioxidant softgel",    # Fixed composition — same for every client
        "magnesium bisglycinate capsule", # Fixed 750mg — always correct
    }

    # Load KB capacity defaults for units that don't specify their own capacity
    # (recipe JSON sometimes omits capacity_mg/capacity_g from format field)
    KB_CAPACITY_DEFAULTS = {
        "sachet": {"capacity_g": 19, "unit": "g"},
        "hard_capsule": {"capacity_mg": 650, "unit": "mg"},
    }

    # Build compact recipe summary for the prompt (only variable units)
    units_summary = []
    skipped_fixed = []
    for unit in recipe.get("units", []):
        label = unit.get("label", "?")
        # Skip fixed-format units — deterministic validation handles these
        if label.lower() in FIXED_UNIT_LABELS:
            qty = unit.get("quantity", 1)
            qty_str = f"{qty}×" if qty > 1 else "1×"
            skipped_fixed.append(f"{qty_str} {label}")
            continue
        timing = unit.get("timing", "?")
        # Prefer per-capsule fill for capacity validation; fall back to total
        fill_mg = unit.get("fill_weight_per_capsule_mg") or unit.get("fill_weight_mg") or unit.get("total_weight_mg")
        fill_g = unit.get("fill_weight_g") or unit.get("total_weight_g")
        fmt_type = unit.get("format", {}).get("type", "?")
        quantity = unit.get("quantity", 1)

        # Get capacity — from unit format field first, then fall back to KB defaults
        capacity = unit.get("format", {}).get("capacity_mg") or unit.get("format", {}).get("capacity_g")
        if not capacity and fmt_type in KB_CAPACITY_DEFAULTS:
            kb_cap = KB_CAPACITY_DEFAULTS[fmt_type]
            capacity = kb_cap.get("capacity_mg") or kb_cap.get("capacity_g")

        ingredients = unit.get("ingredients", unit.get("ingredients_per_unit", []))
        ing_lines = []
        for ing in ingredients:
            comp = ing.get("component", "?")
            amt_mg = ing.get("amount_mg")
            amt_g = ing.get("amount_g")
            # Fix: handle mcg vitamins where weight_mg=0 but dose string exists in "amount" field
            # amt_mg=0.0 is falsy in Python, so check explicitly with `is not None`
            if amt_mg is not None and amt_mg > 0:
                amt = f"{amt_mg}mg"
            elif amt_g is not None and amt_g > 0:
                amt = f"{amt_g}g"
            elif ing.get("amount"):
                # Microgram vitamins store their dose string in "amount" (e.g., "200mcg")
                amt = str(ing["amount"])
            elif ing.get("dose_per_softgel"):
                amt = str(ing["dose_per_softgel"])
            else:
                amt = "?"
            ing_lines.append(f"  - {comp}: {amt}")

        # Label clearly when showing per-capsule vs total
        per_cap_note = " (per capsule)" if unit.get("fill_weight_per_capsule_mg") and unit.get("quantity", 1) > 1 else ""
        fill_str = f"{fill_mg}mg{per_cap_note}" if fill_mg else (f"{fill_g}g" if fill_g else "?")
        cap_str = f"{capacity}mg" if capacity and isinstance(capacity, (int, float)) and capacity > 100 else (f"{capacity}g" if capacity else "?")
        qty_str = f" ×{quantity}" if quantity > 1 else ""
        units_summary.append(f"UNIT: {label} ({fmt_type}, {timing}{qty_str}, fill={fill_str}, capacity={cap_str})\n" + "\n".join(ing_lines))

    skipped_str = ""
    if skipped_fixed:
        skipped_str = f"\nNOTE: The following fixed-format units are validated deterministically and excluded from this review:\n" + \
                      "\n".join(f"  - {s} (fixed composition — always correct per KB)" for s in skipped_fixed)

    if not units_summary:
        # All units are fixed-format — nothing for LLM to review
        return {"warnings": [], "overall_assessment": "All units are fixed-format — deterministic validation only, no LLM QA needed."}

    prompt = f"""QA review of this manufacturing recipe against the knowledge base reference data.

VARIABLE UNITS TO REVIEW:
{chr(10).join(units_summary)}
{skipped_str}

PROTOCOL: {recipe.get('protocol_summary', 'N/A')}

KNOWLEDGE BASE REFERENCE DATA (source of truth — use this to validate the recipe):
{kb_references}

Compare the recipe units above against the reference data. Flag ONLY issues where the recipe CONTRADICTS the KB specifications:
1. Capsule fill efficiency — any capsule with <10% fill is wasteful
2. Dose inconsistency — same ingredient at different amounts across sections
3. Capacity violations — a unit exceeding the capacity specified in the DELIVERY FORMAT REFERENCE
4. Timing contradictions — a substance whose recipe timing contradicts its timing_restriction in SUPPLEMENT TIMING REFERENCE
5. Zero-dose ingredients — anything at 0mg/0g that shouldn't be
6. Dose vs KB mismatch — a vitamin/mineral dose that significantly differs from its standard_dose in VITAMIN/MINERAL DOSE REFERENCE
7. Missing ingredients — something referenced but absent from recipe"""

    try:
        response_text = _call_bedrock(SANITY_CHECK_SYSTEM, prompt, max_tokens=1500, temperature=0.1,
                                       model_id=OPUS_MODEL_ID)
        result = _extract_json_from_response(response_text)
        if isinstance(result, dict) and "warnings" in result:
            return result
        # Unexpected format — return empty
        return {"warnings": [], "overall_assessment": "Sanity check returned unexpected format", "raw": str(result)[:200]}
    except Exception as e:
        print(f"  ⚠️ Sanity check LLM call failed: {e}")
        return {"warnings": [], "overall_assessment": f"Sanity check failed: {e}", "skipped": True}


# ─── MEDICATION EVIDENCE RETRIEVAL (ELICIT FALLBACK) ─────────────────────────

def retrieve_medication_evidence(
    medication_entries: list,
    selected_supplements: list,
    use_bedrock: bool = True,
) -> Dict:
    """Retrieve structured medication evidence for unknown medications.

    Called ONLY for medications that did NOT match any rule in
    medication_interactions.json. Uses Bedrock LLM as a structured
    evidence extractor (Elicit API integration point for the future).

    CRITICAL SAFEGUARD: Results are ALWAYS Tier C (flag only).
    They can NEVER auto-modify the formulation. A human must review
    the flags and manually add a rule to medication_interactions.json
    to promote any finding to Tier A or B.

    Args:
        medication_entries: List of {name, dosage, how_long} dicts for
            medications NOT matched by the deterministic KB.
        selected_supplements: List of supplement names currently in
            the formulation (for cross-reference).
        use_bedrock: Whether to use Bedrock LLM.

    Returns:
        {
            "evidence_flags": [...],       # Tier C clinical flags
            "evidence_objects": [...],     # Raw structured evidence per medication
            "source": "bedrock_llm" | "elicit_api" | "skipped",
        }
    """
    if not medication_entries or not use_bedrock:
        return {
            "evidence_flags": [],
            "evidence_objects": [],
            "source": "skipped",
        }

    evidence_flags = []
    evidence_objects = []

    for med_entry in medication_entries:
        med_name = med_entry.get("name", "").strip()
        med_dosage = med_entry.get("dosage", "").strip()
        if not med_name:
            continue

        # ── Build structured evidence query ──────────────────────────
        supplements_str = ", ".join(selected_supplements[:20]) if selected_supplements else "none selected yet"

        system_prompt = (
            "You are a clinical pharmacology evidence extractor. "
            "For the given medication, return ONLY structured JSON evidence. "
            "Do NOT provide free narrative. Do NOT make formulation decisions. "
            "Your output is used as INPUT to a rule engine — not shown to patients.\n\n"
            "Return a JSON object with these exact keys:\n"
            "{\n"
            '  "medication": "<name>",\n'
            '  "administration_timing": "<when to take, e.g. with food, empty stomach>",\n'
            '  "empty_stomach_required": true/false,\n'
            '  "absorption_dependencies": ["<list of factors affecting absorption>"],\n'
            '  "mineral_interactions": [{"mineral": "<name>", "mechanism": "<brief>", "severity": "high|moderate|low"}],\n'
            '  "fibre_interactions": [{"substance": "<name>", "mechanism": "<brief>", "severity": "high|moderate|low"}],\n'
            '  "micronutrient_interactions": [{"nutrient": "<name>", "mechanism": "<brief>", "severity": "high|moderate|low"}],\n'
            '  "supplement_contraindications": [{"supplement": "<name>", "mechanism": "<brief>", "severity": "high|moderate|low"}],\n'
            '  "pharmacokinetic_interactions": [{"substance": "<name>", "mechanism": "<brief>", "severity": "high|moderate|low"}],\n'
            '  "dose_sensitive_interactions": [{"substance": "<name>", "threshold": "<dose>", "mechanism": "<brief>"}],\n'
            '  "food_restrictions": ["<list of foods to avoid>"],\n'
            '  "confidence": "high|moderate|low"\n'
            "}"
        )

        user_prompt = (
            f"MEDICATION: {med_name}"
            f"{f' (dosage: {med_dosage})' if med_dosage else ''}\n\n"
            f"SUPPLEMENTS IN FORMULATION: {supplements_str}\n\n"
            f"Extract structured pharmacological evidence for this medication. "
            f"Focus on interactions with the listed supplements. "
            f"Return ONLY the JSON object described in the system prompt."
        )

        try:
            response_text = _call_bedrock(
                system_prompt, user_prompt,
                max_tokens=1500, temperature=0.0,
                model_id=OPUS_MODEL_ID,
            )
            evidence = _extract_json_from_response(response_text)

            if isinstance(evidence, dict):
                evidence["_source"] = "bedrock_llm"
                evidence["_retrieval_timestamp"] = __import__("datetime").datetime.utcnow().isoformat() + "Z"
                evidence_objects.append(evidence)

                # ── Cross-reference against selected supplements ─────
                _all_interactions = (
                    evidence.get("mineral_interactions", []) +
                    evidence.get("fibre_interactions", []) +
                    evidence.get("micronutrient_interactions", []) +
                    evidence.get("supplement_contraindications", []) +
                    evidence.get("pharmacokinetic_interactions", [])
                )

                for interaction in _all_interactions:
                    # Check if the interacting substance is in the formulation
                    interacting = (
                        interaction.get("mineral", "") or
                        interaction.get("substance", "") or
                        interaction.get("nutrient", "") or
                        interaction.get("supplement", "")
                    ).lower()
                    severity = interaction.get("severity", "low")
                    mechanism = interaction.get("mechanism", "")

                    # Check if this substance is in selected supplements
                    is_in_formulation = any(
                        interacting in s.lower() or s.lower() in interacting
                        for s in selected_supplements
                    ) if selected_supplements and interacting else False

                    if is_in_formulation or severity == "high":
                        evidence_flags.append({
                            "rule_id": "ELICIT_AUTO",
                            "tier": "C",  # HARDCODED — NEVER Tier A or B from external evidence
                            "severity": severity,
                            "title": f"EXTERNAL EVIDENCE: {interacting.title()} + {med_name}",
                            "detail": mechanism,
                            "medication": med_name,
                            "interacting_substance": interacting,
                            "in_formulation": is_in_formulation,
                            "auto_executed": False,  # HARDCODED — NEVER auto-execute external evidence
                            "source": "bedrock_llm_evidence_retrieval",
                            "review_status": "PENDING_CLINICIAN",
                        })

                # Also flag timing constraints
                if evidence.get("empty_stomach_required"):
                    evidence_flags.append({
                        "rule_id": "ELICIT_AUTO",
                        "tier": "C",
                        "severity": "moderate",
                        "title": f"EXTERNAL EVIDENCE: {med_name} requires empty stomach — timing review needed",
                        "detail": f"Administration: {evidence.get('administration_timing', '?')}. Supplements may need timing separation.",
                        "medication": med_name,
                        "auto_executed": False,
                        "source": "bedrock_llm_evidence_retrieval",
                        "review_status": "PENDING_CLINICIAN",
                    })

        except Exception as e:
            print(f"  ⚠️ Evidence retrieval failed for {med_name}: {e}")
            evidence_flags.append({
                "rule_id": "ELICIT_ERROR",
                "tier": "C",
                "severity": "low",
                "title": f"Evidence retrieval failed for {med_name}",
                "detail": str(e),
                "medication": med_name,
                "auto_executed": False,
                "source": "error",
                "review_status": "PENDING_CLINICIAN",
            })

    return {
        "evidence_flags": evidence_flags,
        "evidence_objects": evidence_objects,
        "source": "bedrock_llm",
    }
