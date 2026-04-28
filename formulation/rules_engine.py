#!/usr/bin/env python3
"""
Rules Engine — All deterministic formulation rules.

Takes unified_input (from parse_inputs.py) and applies threshold-based rules
from knowledge_base JSONs. No LLM calls — pure Python logic.

Produces: rule_outputs dict with:
  - sensitivity_classification
  - health_claims (from goals + microbiome signals)
  - therapeutic_dose_triggers
  - prebiotic_dose_range
  - barrier_support_needed
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ─── KNOWLEDGE BASE LOADING ──────────────────────────────────────────────────

KB_DIR = Path(__file__).parent / "knowledge_base"

def _load_kb(filename: str) -> Dict:
    """Load a knowledge base JSON file."""
    path = KB_DIR / filename
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ─── SENSITIVITY CLASSIFICATION ───────────────────────────────────────────────
# Rule hierarchy for the narrative-mode extension (24 April 2026)
# ---------------------------------------------------------------
# `classify_sensitivity()` is the engine's authoritative ladder for bloating-
# driven decisions (prebiotic grams, FODMAP clamp, mix filters). The report
# side must not re-derive it.
#
# After `classify_sensitivity()` runs, `classify_symptom_narrative_mode()`
# maps that classification (+ microbiome corroboration) onto a narrative
# mode used by Section 3 of the health report:
#
#   low                                      → "none" / "lifestyle_normalising"
#   moderate + no microbiome corroboration   → "lifestyle_normalising"
#   moderate + microbiome corroboration      → "microbial_contributing"
#   high                                     → "microbial_driving"
#
# "Microbiome corroboration" means ≥1 of:
#   - Bifidobacteria depletion  (below range)
#   - Fiber Degraders below range / CLR negative
#   - Butyrate Producers below range
#   - Cross-Feeders below range (limited SCFA chain)
#   - Mucin Degraders above range (barrier strain) — handled as above=True
# See rnd_health_report/docs/handover_20260424_render_and_logic_refinements.md
# Part B1 for the full table and the clinical motivation.


def classify_sensitivity(digestive: Dict) -> Dict:
    """
    Classify client sensitivity based on digestive questionnaire data.
    Returns: {"classification": "high"|"moderate"|"low", "reasoning": [...]}
    """
    kb = _load_kb("sensitivity_thresholds.json")
    rules = kb["classification_rules"]

    bloating_severity = digestive.get("bloating_severity")
    bloating_frequency = digestive.get("bloating_frequency", "")
    stool_type = digestive.get("stool_type")
    digestive_satisfaction = digestive.get("digestive_satisfaction")

    reasoning = []

    # Check HIGH sensitivity (OR conditions)
    high_triggered = False
    if bloating_severity is not None and bloating_severity >= 7:
        high_triggered = True
        reasoning.append(f"Bloating severity {bloating_severity}/10 (≥7 threshold)")
    if bloating_frequency and str(bloating_frequency).lower() in ("daily", "most_days"):
        high_triggered = True
        reasoning.append(f"Bloating frequency '{bloating_frequency}' (daily/most-days — high)")
    if stool_type is not None and stool_type in [6, 7]:
        high_triggered = True
        reasoning.append(f"Bristol stool type {stool_type} (loose/watery)")
    if digestive_satisfaction is not None and digestive_satisfaction <= 3:
        high_triggered = True
        reasoning.append(f"Digestive satisfaction {digestive_satisfaction}/10 (≤3 threshold)")

    if high_triggered:
        return {
            "classification": "high",
            "max_prebiotic_g": rules["high_sensitivity"]["max_prebiotic_g"],
            "prebiotic_clamp": rules["high_sensitivity"]["prebiotic_clamp"],
            "reasoning": reasoning
        }

    # Check LOW sensitivity (AND conditions)
    # bloating_severity=None means not reported — treated same as ≤3 (no distress)
    low_triggered = True
    bloating_ok = bloating_severity is None or bloating_severity <= 3
    if bloating_ok:
        reasoning.append(f"Bloating severity {bloating_severity if bloating_severity is not None else 'not reported'}/10 (≤3 or N/R — low)")
    else:
        low_triggered = False

    if digestive_satisfaction is not None and digestive_satisfaction >= 7:
        reasoning.append(f"Digestive satisfaction {digestive_satisfaction}/10 (≥7 — good)")
    else:
        low_triggered = False

    if low_triggered:
        return {
            "classification": "low",
            "max_prebiotic_g": rules["low_sensitivity"]["max_prebiotic_g"],
            "prebiotic_clamp": rules["low_sensitivity"]["prebiotic_clamp"],
            "reasoning": reasoning
        }

    # Default: moderate
    if not reasoning:
        reasoning.append("Insufficient digestive data — defaulting to moderate")
    else:
        reasoning.append("Between high and low thresholds — moderate")

    return {
        "classification": "moderate",
        "max_prebiotic_g": rules["moderate_sensitivity"]["max_prebiotic_g"],
        "prebiotic_clamp": rules["moderate_sensitivity"]["prebiotic_clamp"],
        "reasoning": reasoning
    }


# ─── SYMPTOM NARRATIVE MODE (B1) ──────────────────────────────────────────────

def classify_symptom_narrative_mode(
    sensitivity: Dict,
    microbiome: Dict,
    digestive: Dict,
) -> Dict:
    """Decide the narrative mode the Section 3 health-report builder should use
    when talking about bloating-adjacent symptoms.

    Scalable, reproducible rule — reuses the engine's own sensitivity ladder
    so every client, regardless of ID, passes through the same four modes.

    Modes:
        "none"
            Sensitivity is low AND no bloating reported → S3 does not hero
            this symptom.
        "lifestyle_normalising"
            Sensitivity is low/moderate AND microbiome does not corroborate
            a microbial bloating driver → copy normalises the symptom as
            diet/lifestyle/menstrual/stress variance. Does NOT blame
            bacterial fermentation.
        "microbial_contributing"
            Sensitivity is moderate AND microbiome DOES corroborate →
            copy may gesture at microbial contribution with nuance
            ("your results show ... which can contribute to ...") but
            avoids the definitive "bacterial fermentation imbalance" claim.
        "microbial_driving"
            Sensitivity is high (severity ≥7 OR daily/most_days frequency
            OR Bristol 6–7 OR digestive_satisfaction ≤3). Copy may directly
            link bloating to microbial fermentation.

    Corroborating microbiome signals (≥1 required to upgrade `moderate`
    from ``lifestyle_normalising`` to ``microbial_contributing``):
      - Bifidobacteria below range
      - Fiber Degraders below range (CLR < 0 reinforces)
      - Butyrate Producers below range
      - Cross-Feeders below range (SCFA chain gap)
      - Mucin Degraders above range (mucin-thinning signal)

    Returns:
        dict with keys:
          - ``mode``: one of the four mode strings.
          - ``corroborating_microbiome_signals``: list of human-readable
            strings describing the signals that matched. Empty when none.
          - ``reasoning``: explainability trail for the dashboard.
    """
    classification = sensitivity.get("classification", "moderate")
    bloating_severity = digestive.get("bloating_severity") if digestive else None

    reasoning: list[str] = [
        f"sensitivity.classification = {classification}",
    ]

    # ── Microbiome corroboration signals ─────────────────────────────────
    guild_status = (microbiome.get("guild_status") or {}) if microbiome else {}
    guild_details = (microbiome.get("guild_details") or {}) if microbiome else {}
    corroborating: list[str] = []

    def _is_below(key: str) -> bool:
        status = str(guild_status.get(key, "")).lower()
        return "below" in status

    def _is_above(key: str) -> bool:
        status = str(guild_status.get(key, "")).lower()
        return "above" in status or "elev" in status

    if _is_below("bifidobacteria"):
        corroborating.append("Bifidobacteria below range")
    if _is_below("fiber_degraders"):
        clr = (guild_details.get("fiber_degraders") or {}).get("clr")
        tail = f" (CLR {clr})" if clr is not None else ""
        corroborating.append(f"Fiber Degraders below range{tail}")
    if _is_below("butyrate_producers"):
        corroborating.append("Butyrate Producers below range")
    if _is_below("cross_feeders"):
        corroborating.append("Cross-Feeders below range — limited SCFA chain")
    if _is_above("mucin_degraders"):
        corroborating.append("Mucin Degraders above range — mucus layer strain")

    # ── Mode selection ───────────────────────────────────────────────────
    if classification == "high":
        mode = "microbial_driving"
        reasoning.append("High sensitivity → microbial_driving (direct language OK)")
    elif classification == "moderate":
        if corroborating:
            mode = "microbial_contributing"
            reasoning.append(
                f"Moderate sensitivity + {len(corroborating)} corroborating microbiome signal(s) "
                f"→ microbial_contributing (nuanced language)"
            )
        else:
            mode = "lifestyle_normalising"
            reasoning.append(
                "Moderate sensitivity but no microbiome corroboration "
                "→ lifestyle_normalising (no microbial claim)"
            )
    else:  # low
        if bloating_severity and bloating_severity >= 1:
            mode = "lifestyle_normalising"
            reasoning.append(
                "Low sensitivity but bloating was reported "
                "→ lifestyle_normalising (no microbial claim)"
            )
        else:
            mode = "none"
            reasoning.append(
                "Low sensitivity and no bloating reported → none (S3 skips bloating hero)"
            )

    return {
        "mode": mode,
        "corroborating_microbiome_signals": corroborating,
        "reasoning": reasoning,
    }


# ─── MULTI-SYMPTOM NARRATIVE MODES (B1 — extended 24 Apr 2026) ───────────────
# The single-symptom `classify_symptom_narrative_mode()` above handles bloating.
# The dispatcher below extends the same 4-mode framework to 5 additional symptoms
# (stress, sleep, fatigue, skin, immune) using corroboration signals that already
# exist in the engine's knowledge base — no new clinical thresholds invented.
#
# Reuses:
#   - sensitivity_thresholds.json (bloating)
#   - goal_to_health_claim.json::microbiome_signal_to_vitamin_claims
#     (biotin_limited_producer / folate_risk / b12_inverse_signal / b_complex_risk)
#   - clr_decision_rules.json (CLR sign convention)
#   - microbiome.guild_status (below / within / above range)
#
# Scalability contract: each symptom has a tier function (severity band) and a
# corroboration function (counts microbiome signals that make a microbial claim
# defensible). The mode decision is identical across symptoms:
#
#   tier=none                         → "none"
#   tier=low                          → "lifestyle_normalising"
#   tier=moderate AND corrob >= N     → "microbial_contributing"
#   tier=moderate AND corrob <  N     → "lifestyle_normalising"
#   tier=high                         → "microbial_driving"
#
# Per-symptom minimum corroboration count (N) reflects evidence strength:
#   bloating (direct gut mechanism)                        → 1
#   stress, sleep (moderate gut-brain evidence)            → 1
#   fatigue, immune (indirect but documented vitamin link) → 1
#   skin (weak gut-skin axis, correlative)                 → 2  (stricter)


_SYMPTOM_CORROBORATION_MIN = {
    "bloating": 1,
    "stress":   1,
    "sleep":    1,
    "fatigue":  1,
    "skin":     2,   # tighter bar — evidence is correlative, not causal
    "immune":   1,
}


def _severity_band_bloating(digestive: Dict, sensitivity: Dict) -> str:
    """Reuse classify_sensitivity output. low/moderate/high → same band."""
    classification = (sensitivity or {}).get("classification", "moderate")
    sev = (digestive or {}).get("bloating_severity")
    if classification == "high":
        return "high"
    if classification == "moderate":
        return "moderate"
    # low
    if sev and sev >= 1:
        return "low"
    return "none"


def _severity_band_stress(lifestyle: Dict) -> str:
    """Stress level 1–10. No reporting → 'none'. ≥7 → high, 5–6 → moderate, ≤4 → low."""
    sl = (lifestyle or {}).get("stress_level")
    if sl is None:
        return "none"
    try:
        v = int(sl)
    except Exception:
        return "none"
    if v >= 7:
        return "high"
    if v >= 5:
        return "moderate"
    if v >= 1:
        return "low"
    return "none"


def _severity_band_sleep(lifestyle: Dict) -> str:
    """Sleep quality 1–10 (higher = better). <=4 high problem, 5–7 moderate, ≥8 low."""
    sq = (lifestyle or {}).get("sleep_quality")
    if sq is None:
        return "none"
    try:
        v = int(sq)
    except Exception:
        return "none"
    if v <= 4:
        return "high"
    if v <= 6:
        return "moderate"
    if v <= 7:
        return "low"
    return "none"


def _severity_band_fatigue(lifestyle: Dict) -> str:
    """energy_level enum: very_low / low / moderate / good_all_day (or None)."""
    el = ((lifestyle or {}).get("energy_level") or "").lower()
    if el in ("very_low", "very low"):
        return "high"
    if el == "low":
        return "moderate"
    if el == "moderate":
        return "low"
    if el in ("good_all_day", "good all day", "high"):
        return "none"
    return "none"


def _severity_band_skin(goals: Dict, medical: Dict) -> str:
    """Skin concerns are captured via a goal keyword. No numeric severity —
    presence of goal alone = 'moderate' (worth mentioning if microbiome
    corroborates strongly). If the client also reports a skin condition in
    medical, bump to 'high'.
    """
    ranked = (goals or {}).get("ranked", []) or []
    has_skin_goal = any(
        "skin" in g.lower() for g in ranked
    )
    # Medical history scan for skin conditions
    med_conds = (medical or {}).get("medical_conditions", []) + \
                (medical or {}).get("conditions", []) + \
                (medical or {}).get("diagnoses", [])
    med_text = " ".join(str(c).lower() for c in med_conds)
    has_skin_condition = any(
        kw in med_text for kw in ("eczema", "psoriasis", "acne", "rosacea", "dermatitis")
    )
    if has_skin_condition:
        return "high"
    if has_skin_goal:
        return "moderate"
    return "none"


def _severity_band_immune(immune_data: Dict, lifestyle: Dict) -> str:
    """UTI + colds frequencies. Recurrent → high, occasional → moderate, rare/none → low/none."""
    # Normalise both possible field layouts
    uti = str((immune_data or {}).get("uti_per_year", "")).lower()
    colds = str((immune_data or {}).get("colds_per_year", "")).lower()

    recurrent_uti = uti not in ("", "none", "none_or_rarely", "rarely", "0", "0-1", "rarely_0_1")
    many_colds = any(pat in colds for pat in ("4+", "6+", "5+", "8+", "many", "frequent"))

    if recurrent_uti or many_colds:
        return "high"

    moderate_colds = "2-3" in colds or "3-4" in colds
    if moderate_colds:
        return "moderate"

    if uti or colds:
        return "low"
    return "none"


def _corroborate_stress(microbiome: Dict) -> List[str]:
    signals = []
    gs = (microbiome or {}).get("guild_status") or {}
    if "below" in str(gs.get("butyrate_producers", "")).lower():
        signals.append("Butyrate Producers below range — gut-brain axis link")
    if "below" in str(gs.get("cross_feeders", "")).lower():
        signals.append("Cross-Feeders below range — SCFA chain gap")
    return signals


def _corroborate_sleep(microbiome: Dict) -> List[str]:
    signals = []
    gs = (microbiome or {}).get("guild_status") or {}
    if "below" in str(gs.get("butyrate_producers", "")).lower():
        signals.append("Butyrate Producers below range — serotonin precursor pathway")
    if "below" in str(gs.get("bifidobacteria", "")).lower():
        signals.append("Bifidobacteria below range — tryptophan metabolism link")
    return signals


def _corroborate_fatigue(microbiome: Dict) -> List[str]:
    signals = []
    vs = (microbiome or {}).get("vitamin_signals") or {}
    gs = (microbiome or {}).get("guild_status") or {}
    # Reuse the signal thresholds from extract_health_claims()
    if (vs.get("biotin") or {}).get("risk_level", 0) >= 1:
        signals.append("Biotin production at risk (microbiome vitamin signal)")
    if (vs.get("folate") or {}).get("risk_level", 0) >= 2:
        signals.append("Folate production at risk (microbiome vitamin signal)")
    if (vs.get("B12") or {}).get("risk_level", 0) >= 2:
        signals.append("B12 production at risk (microbiome vitamin signal)")
    if (vs.get("B_complex") or {}).get("risk_level", 0) >= 2:
        signals.append("B-complex production at risk (microbiome vitamin signal)")
    if "below" in str(gs.get("butyrate_producers", "")).lower():
        signals.append("Butyrate Producers below range — energy metabolism link")
    return signals


def _corroborate_skin(microbiome: Dict) -> List[str]:
    signals = []
    vs = (microbiome or {}).get("vitamin_signals") or {}
    gs = (microbiome or {}).get("guild_status") or {}
    if (vs.get("biotin") or {}).get("risk_level", 0) >= 1:
        signals.append("Biotin production at risk — skin / hair quality link")
    if "above" in str(gs.get("mucin_degraders", "")).lower() or \
       "elev" in str(gs.get("mucin_degraders", "")).lower():
        signals.append("Mucin Degraders above range — barrier function strain")
    if "above" in str(gs.get("proteolytic", "")).lower() or \
       "elev" in str(gs.get("proteolytic", "")).lower():
        signals.append("Proteolytic Guild above range — pro-inflammatory signal")
    return signals


def _corroborate_immune(microbiome: Dict) -> List[str]:
    signals = []
    vs = (microbiome or {}).get("vitamin_signals") or {}
    gs = (microbiome or {}).get("guild_status") or {}
    if (vs.get("folate") or {}).get("risk_level", 0) >= 2:
        signals.append("Folate production at risk — immune support link")
    if (vs.get("B12") or {}).get("risk_level", 0) >= 2:
        signals.append("B12 production at risk — immune support link")
    if "below" in str(gs.get("bifidobacteria", "")).lower():
        signals.append("Bifidobacteria below range — Treg / IgA modulation link")
    return signals


def _pick_mode(severity_band: str, corrob_count: int, min_required: int) -> str:
    """Unified mode-picker for all non-bloating symptoms."""
    if severity_band == "none":
        return "none"
    if severity_band == "low":
        return "lifestyle_normalising"
    if severity_band == "moderate":
        return "microbial_contributing" if corrob_count >= min_required else "lifestyle_normalising"
    if severity_band == "high":
        return "microbial_driving"
    return "lifestyle_normalising"  # safe default


def classify_all_symptom_narrative_modes(
    unified_input: Dict,
    sensitivity: Dict,
) -> Dict:
    """Return a dict mapping each of the 6 symptoms to its narrative mode.

    Output shape:
        {
          "bloating": { "mode": ..., "signals": [...], "severity_band": ... },
          "stress":   { ... },
          "sleep":    { ... },
          "fatigue":  { ... },
          "skin":     { ... },
          "immune":   { ... },
        }

    This is persisted on ``rule_outputs.symptom_narrative_modes`` and surfaced
    on ``input_summary.questionnaire_driven.symptom_narrative_modes`` for the
    Section 3 health-report builder.
    """
    q = unified_input.get("questionnaire", {}) or {}
    microbiome = unified_input.get("microbiome", {}) or {}
    digestive = q.get("digestive", {}) or {}
    lifestyle = q.get("lifestyle", {}) or {}
    goals = q.get("goals", {}) or {}
    medical = q.get("medical", {}) or {}
    immune = q.get("immune", {}) or digestive.get("immune", {}) or {}

    out = {}

    # ── bloating (reuse existing single-symptom function for parity) ─────
    bloating_result = classify_symptom_narrative_mode(
        sensitivity=sensitivity,
        microbiome=microbiome,
        digestive=digestive,
    )
    bloating_band = _severity_band_bloating(digestive, sensitivity)
    out["bloating"] = {
        "mode": bloating_result["mode"],
        "signals": bloating_result["corroborating_microbiome_signals"],
        "severity_band": bloating_band,
        "min_corrob_required": _SYMPTOM_CORROBORATION_MIN["bloating"],
    }

    # ── stress ───────────────────────────────────────────────────────────
    band = _severity_band_stress(lifestyle)
    sig = _corroborate_stress(microbiome)
    out["stress"] = {
        "mode": _pick_mode(band, len(sig), _SYMPTOM_CORROBORATION_MIN["stress"]),
        "signals": sig,
        "severity_band": band,
        "min_corrob_required": _SYMPTOM_CORROBORATION_MIN["stress"],
    }

    # ── sleep ────────────────────────────────────────────────────────────
    band = _severity_band_sleep(lifestyle)
    sig = _corroborate_sleep(microbiome)
    out["sleep"] = {
        "mode": _pick_mode(band, len(sig), _SYMPTOM_CORROBORATION_MIN["sleep"]),
        "signals": sig,
        "severity_band": band,
        "min_corrob_required": _SYMPTOM_CORROBORATION_MIN["sleep"],
    }

    # ── fatigue ──────────────────────────────────────────────────────────
    band = _severity_band_fatigue(lifestyle)
    sig = _corroborate_fatigue(microbiome)
    out["fatigue"] = {
        "mode": _pick_mode(band, len(sig), _SYMPTOM_CORROBORATION_MIN["fatigue"]),
        "signals": sig,
        "severity_band": band,
        "min_corrob_required": _SYMPTOM_CORROBORATION_MIN["fatigue"],
    }

    # ── skin ─────────────────────────────────────────────────────────────
    band = _severity_band_skin(goals, medical)
    sig = _corroborate_skin(microbiome)
    out["skin"] = {
        "mode": _pick_mode(band, len(sig), _SYMPTOM_CORROBORATION_MIN["skin"]),
        "signals": sig,
        "severity_band": band,
        "min_corrob_required": _SYMPTOM_CORROBORATION_MIN["skin"],
    }

    # ── immune ───────────────────────────────────────────────────────────
    band = _severity_band_immune(immune, lifestyle)
    sig = _corroborate_immune(microbiome)
    out["immune"] = {
        "mode": _pick_mode(band, len(sig), _SYMPTOM_CORROBORATION_MIN["immune"]),
        "signals": sig,
        "severity_band": band,
        "min_corrob_required": _SYMPTOM_CORROBORATION_MIN["immune"],
    }

    return out


# ─── HEALTH CLAIM EXTRACTION ─────────────────────────────────────────────────

def extract_health_claims(goals: Dict, vitamin_signals: Dict) -> Dict:
    """
    Map questionnaire goals + microbiome vitamin signals to health claim categories.
    Returns: {"supplement_claims": [...], "vitamin_claims": [...], "microbiome_vitamin_needs": [...]}
    """
    kb = _load_kb("goal_to_health_claim.json")
    goal_mappings = kb["goal_mappings"]
    mb_signals = kb["microbiome_signal_to_vitamin_claims"]

    supplement_claims = set()
    vitamin_claims = set()
    triggers_timing = False
    claim_sources = []

    # Map questionnaire goals
    ranked_goals = goals.get("ranked", [])
    # Also surface the unresolved other_raw_text as context (even if not in ranked)
    other_raw_text = goals.get("other_raw_text")

    for goal in ranked_goals:
        goal_key = goal.lower().strip()
        if goal_key in goal_mappings:
            mapping = goal_mappings[goal_key]
            for claim in mapping.get("health_claims", []):
                supplement_claims.add(claim)
                claim_sources.append({"claim": claim, "source": "questionnaire_goal", "goal": goal_key})
            for vclaim in mapping.get("vitamin_claims", []):
                vitamin_claims.add(vclaim)
            if mapping.get("triggers_timing_rules"):
                triggers_timing = True
        elif goal_key == "other":
            # "other" should have been resolved by parse_inputs._resolve_goals() before reaching here.
            # If it arrives unresolved, it means parse_inputs did not call _normalize_other_goal.
            # Log a loud warning so the issue is visible in pipeline logs — do NOT silently skip.
            raw_hint = f" (raw text: '{other_raw_text}')" if other_raw_text else " (no other_goal_details provided)"
            print(
                f"  ❌ ERROR: Unresolved 'other' goal reached health claim extraction{raw_hint}. "
                f"parse_inputs.extract_questionnaire_data() should have resolved this via _resolve_goals(). "
                f"This goal will be IGNORED — check parse_inputs.py for the root cause."
            )

    # Map microbiome vitamin signals
    microbiome_vitamin_needs = []

    # Biotin
    biotin_signal = vitamin_signals.get("biotin", {})
    if biotin_signal.get("risk_level", 0) >= 1:
        vitamin_claims.add("Fatigue")
        vitamin_claims.add("Skin Quality")
        microbiome_vitamin_needs.append({
            "vitamin": "Biotin (B7)",
            "trigger": f"biotin risk_level={biotin_signal.get('risk_level')}",
            "source": "microbiome_signal"
        })

    # Folate
    folate_signal = vitamin_signals.get("folate", {})
    if folate_signal.get("risk_level", 0) >= 2:
        vitamin_claims.add("Immune System")
        vitamin_claims.add("Fatigue")
        microbiome_vitamin_needs.append({
            "vitamin": "Folate (B9)",
            "trigger": f"folate risk_level={folate_signal.get('risk_level')}",
            "source": "microbiome_signal"
        })

    # B12
    b12_signal = vitamin_signals.get("B12", {})
    if b12_signal.get("risk_level", 0) >= 2:
        vitamin_claims.add("Immune System")
        vitamin_claims.add("Fatigue")
        microbiome_vitamin_needs.append({
            "vitamin": "Vitamin B12",
            "trigger": f"B12 risk_level={b12_signal.get('risk_level')}",
            "source": "microbiome_signal"
        })

    # B-complex
    bcomplex_signal = vitamin_signals.get("B_complex", {})
    if bcomplex_signal.get("risk_level", 0) >= 2:
        vitamin_claims.add("Immune System")
        vitamin_claims.add("Fatigue")
        vitamin_claims.add("Metabolism")
        microbiome_vitamin_needs.append({
            "vitamin": "B-Complex",
            "trigger": f"B-complex risk_level={bcomplex_signal.get('risk_level')}",
            "source": "microbiome_signal"
        })

    return {
        "supplement_claims": sorted(supplement_claims),
        "vitamin_claims": sorted(vitamin_claims),
        "microbiome_vitamin_needs": microbiome_vitamin_needs,
        "triggers_timing_rules": triggers_timing,
        "claim_sources": claim_sources,
    }


# ─── THERAPEUTIC DOSE TRIGGERS ────────────────────────────────────────────────

def check_therapeutic_triggers(medical: Dict, lifestyle: Dict) -> Dict:
    """
    Check if client has reported deficiencies that require therapeutic doses.
    Returns: {"therapeutic_vitamins": [...], "enhanced_vitamins": [...]}
    """
    kb = _load_kb("therapeutic_doses.json")
    dose_table = kb["therapeutic_dose_table"]

    reported_deficiencies = medical.get("vitamin_deficiencies", []) + medical.get("reported_deficiencies", [])
    # Normalize deficiency names
    reported_lower = [d.lower().strip() for d in reported_deficiencies if d]

    therapeutic_vitamins = []
    enhanced_vitamins = []

    # Symptom indicators
    has_brain_fog = "brain_fog" in str(lifestyle.get("stress_symptoms", [])).lower()
    has_fatigue = (lifestyle.get("energy_level") or "").lower() in ["very_low", "low"] if lifestyle.get("energy_level") else False
    age = None  # Will be passed separately if needed

    for entry in dose_table:
        vitamin_name = entry["vitamin"].lower()

        # Check if this vitamin is in reported deficiencies
        matched = False
        for deficiency in reported_lower:
            if vitamin_name.replace("vitamin ", "") in deficiency or deficiency in vitamin_name:
                matched = True
                break

        if not matched:
            continue

        # Determine dose tier
        has_symptoms = False
        if "b12" in vitamin_name and has_brain_fog:
            has_symptoms = True
        elif "d" in vitamin_name and has_fatigue:
            has_symptoms = True
        elif "iron" in vitamin_name and has_fatigue:
            has_symptoms = True

        if has_symptoms:
            therapeutic_vitamins.append({
                "vitamin": entry["vitamin"],
                "dose": entry["therapeutic_dose"],
                "standard_dose": entry["standard_dose"],
                "monitoring": entry["monitoring_required"],
                "masking_risk": entry.get("masking_risk"),
                "tier": "therapeutic",
                "reason": f"Reported {entry['vitamin']} deficiency with active symptoms"
            })
        else:
            enhanced_vitamins.append({
                "vitamin": entry["vitamin"],
                "dose": entry["enhanced_dose"],
                "standard_dose": entry["standard_dose"],
                "monitoring": entry["monitoring_required"],
                "tier": "enhanced",
                "reason": f"Reported {entry['vitamin']} deficiency without active symptoms"
            })

    return {
        "therapeutic_vitamins": therapeutic_vitamins,
        "enhanced_vitamins": enhanced_vitamins,
        "reported_deficiencies": reported_deficiencies,
    }


# ─── PREBIOTIC DOSE RANGE ────────────────────────────────────────────────────

def calculate_prebiotic_range(
    sensitivity: Dict,
    cfu_billions: int = 50,
    mix_id: int = None
) -> Dict:
    """
    Calculate allowed prebiotic gram range based on CFU tier + sensitivity.
    """
    kb = _load_kb("prebiotic_rules.json")
    dosing = kb["dosing_by_cfu_tier"]

    classification = sensitivity["classification"]

    # Determine CFU tier key
    if mix_id == 8 and cfu_billions <= 50:
        tier_key = "50B_mix8"
    elif cfu_billions <= 50:
        tier_key = "50B"
    elif cfu_billions <= 75:
        tier_key = "75B"
    else:
        tier_key = "100B"

    tier = dosing.get(tier_key, dosing["50B"])

    # Apply sensitivity clamp
    if classification == "high":
        g_range = tier.get("high_sensitivity", tier["total_g_range"])
    elif classification == "low":
        g_range = tier.get("low_high_tolerance", tier["total_g_range"])
    else:
        g_range = tier.get("moderate", tier["total_g_range"])

    # Also clamp by sensitivity max
    max_g = sensitivity.get("max_prebiotic_g", 10)
    g_range = [g_range[0], min(g_range[1], max_g)]

    return {
        "min_g": g_range[0],
        "max_g": g_range[1],
        "cfu_tier": tier_key,
        "sensitivity_clamp": classification,
        "note": tier.get("note", ""),
    }


# ─── BARRIER SUPPORT CHECK ───────────────────────────────────────────────────

def assess_magnesium_needs(lifestyle: Dict, goals: Dict) -> Dict:
    """
    Assess magnesium needs based on 3 criteria: sleep, sport, stress.
    Dosing: 2 capsules if ≥2 needs, 1 capsule if 1 need, 0 if no needs.
    Each capsule: 750mg Mg bisglycinate = 105mg elemental Mg.
    """
    needs = []
    reasoning = []
    ranked_goals = goals.get("ranked", [])

    # Need 1: Sleep
    sleep_quality = lifestyle.get("sleep_quality")
    sleep_goal = any("sleep" in g.lower() for g in ranked_goals)
    if (sleep_quality is not None and sleep_quality <= 7) or sleep_goal:
        needs.append("sleep")
        reason = []
        if sleep_quality is not None and sleep_quality <= 7:
            reason.append(f"sleep quality {sleep_quality}/10 ≤ 7")
        if sleep_goal:
            reason.append("sleep improvement is a stated goal")
        reasoning.append(f"Sleep: {' + '.join(reason)}")

    # Need 2: Sport/exercise
    exercise = lifestyle.get("exercise_frequency", "")
    exercise_str = str(exercise).lower() if exercise else ""
    sport_indicators = ["moderate", "vigorous", "strength", "cardio", "sport", "high", "good_all_day"]
    is_active = any(ind in exercise_str for ind in sport_indicators)
    if not is_active and lifestyle.get("energy_level"):
        energy = str(lifestyle.get("energy_level", "")).lower()
        if "good_all_day" in energy:
            is_active = True
    if is_active:
        needs.append("sport")
        reasoning.append(f"Sport: Active lifestyle ({exercise or 'inferred'})")

    # Need 3: Stress
    stress = lifestyle.get("stress_level")
    stress_goals = {"reduce_stress_anxiety", "improve_mood_reduce_anxiety"}
    stress_goal = any(g in stress_goals for g in ranked_goals)
    if (stress is not None and stress >= 6) or stress_goal:
        needs.append("stress")
        reason = []
        if stress is not None and stress >= 6:
            reason.append(f"stress {stress}/10 ≥ 6")
        if stress_goal:
            reason.append("stress/mood goal")
        reasoning.append(f"Stress: {' + '.join(reason)}")

    need_count = len(needs)
    capsules = 2 if need_count >= 2 else (1 if need_count == 1 else 0)

    return {
        "needs_identified": needs,
        "need_count": need_count,
        "capsules": capsules,
        "mg_bisglycinate_total_mg": capsules * 750,
        "elemental_mg_total_mg": capsules * 105,
        "reasoning": reasoning,
        "timing": None,  # Timing is determined by apply_timing_rules(), not hardcoded here
    }


def assess_softgel_needs(health_claims: Dict, medical: Dict, lifestyle: Dict, goals: Dict) -> Dict:
    """
    Assess whether client needs the fixed softgel (Omega + D3 + E + Astaxanthin).
    Client gets softgel if they need ANY ONE of the 4 components.
    Check contraindications from questionnaire.
    """
    needs = []
    reasoning = []
    contraindications = []
    ranked_goals = goals.get("ranked", [])

    # Omega-3 needs (broadly indicated)
    # NOTE: weight_management included — omega-3 supports metabolic health and satiety signalling
    omega_triggers = {"improve_mood_reduce_anxiety", "reduce_stress_anxiety", "improve_skin_health",
                      "longevity_healthy_aging", "support_heart_health", "boost_energy_reduce_fatigue",
                      "improve_focus_concentration", "weight_management", "manage_weight",
                      "improve_sleep_quality", "sleep_quality"}
    omega_goal = any(g in omega_triggers for g in ranked_goals)
    omega_claim = any(c in health_claims.get("supplement_claims", []) for c in
                      ["Stress/Anxiety", "Skin Quality", "Anti-inflammatory", "Memory & Cognition",
                       "Fatigue", "Triglycerides", "Blood Cholesterol", "Fullness/Satiety"])

    # Fallback: if questionnaire coverage is incomplete (goals_ranked empty) and any
    # health claims exist, default to including the softgel — it is safe and broadly beneficial
    incomplete_questionnaire_fallback = (
        len(ranked_goals) == 0
        and len(health_claims.get("supplement_claims", [])) > 0
    )

    if omega_goal or omega_claim or incomplete_questionnaire_fallback:
        needs.append("omega3")
        reasoning.append(f"Omega-3: {'goal match' if omega_goal else 'health claim match'}")

    # Vitamin D needs
    vit_d_claims = any(c in health_claims.get("vitamin_claims", []) for c in ["Immune System"])
    vit_d_deficiency = any("d" in d.lower() for d in medical.get("vitamin_deficiencies", []) + medical.get("reported_deficiencies", []))
    if vit_d_claims or vit_d_deficiency:
        needs.append("vitamin_d")
        reason = []
        if vit_d_claims:
            reason.append("immune health claim")
        if vit_d_deficiency:
            reason.append("reported Vitamin D deficiency")
        reasoning.append(f"Vitamin D: {' + '.join(reason)}")

    # Vitamin E needs
    skin_goal = any("skin" in g.lower() for g in ranked_goals)
    if skin_goal:
        needs.append("vitamin_e")
        reasoning.append("Vitamin E: skin quality goal")

    # Astaxanthin needs
    sport_active = str(lifestyle.get("exercise_frequency", "")).lower()
    is_active = any(ind in sport_active for ind in ["moderate", "vigorous", "strength", "sport"])
    if not is_active and lifestyle.get("energy_level"):
        is_active = "good_all_day" in str(lifestyle.get("energy_level", "")).lower()
    if skin_goal or is_active:
        needs.append("astaxanthin")
        reason = []
        if skin_goal:
            reason.append("skin UV protection")
        if is_active:
            reason.append("muscle recovery (active lifestyle)")
        reasoning.append(f"Astaxanthin: {' + '.join(reason)}")

    # Contraindications check
    medications = medical.get("medications", [])
    meds_str = " ".join(str(m).lower() for m in medications) if medications else ""
    if "warfarin" in meds_str or "blood thinner" in meds_str or "anticoagulant" in meds_str:
        contraindications.append("Blood thinners — omega-3 may increase bleeding risk")
    if "chemotherapy" in meds_str:
        contraindications.append("Chemotherapy — Vitamin E may alter effectiveness")

    include = len(needs) > 0 and len(contraindications) == 0
    return {
        "include_softgel": include,
        "needs_identified": needs,
        "need_count": len(needs),
        "reasoning": reasoning,
        "contraindications": contraindications,
        "daily_count": 2 if include else 0,
    }


def select_sleep_supplements(lifestyle: Dict, goals: Dict) -> Dict:
    """Evidence-based sleep supplement selection.
    
    Decision tree:
    - Melatonin 1mg: ONLY for sleep onset problems (difficulty_falling_asleep)
    - L-Theanine 200-400mg: Default for arousal/relaxation (dose escalated for high stress + poor sleep)
    - Valerian 400mg: Only for maintenance issues + sleep_quality ≤4
    - Mg bisglycinate: Handled separately by assess_magnesium_needs()
    """
    sleep_quality = lifestyle.get("sleep_quality")
    stress_level = lifestyle.get("stress_level")
    stress_symptoms = lifestyle.get("stress_symptoms", [])
    ranked_goals = goals.get("ranked", [])
    
    # Parse sleep issues from questionnaire
    sleep_issues_raw = lifestyle.get("stress_symptoms", [])  # sleep issues often in stress section
    # Also check for sleep-specific fields if available
    sleep_issues = set()
    for item in sleep_issues_raw:
        item_lower = str(item).lower()
        if "falling_asleep" in item_lower or "difficulty_falling" in item_lower:
            sleep_issues.add("difficulty_falling_asleep")
        if "waking" in item_lower and "unrefreshed" in item_lower:
            sleep_issues.add("waking_unrefreshed")
        if "waking" in item_lower and ("during" in item_lower or "night" in item_lower):
            sleep_issues.add("waking_during_night")

    supplements = []
    reasoning = []

    # STEP 1: No supplement gate
    has_sleep_goal = any("sleep" in g.lower() for g in ranked_goals)
    if sleep_quality is not None and sleep_quality > 7 and not has_sleep_goal:
        return {"supplements": [], "reasoning": ["Sleep quality >7 and no sleep goal → no sleep supplement needed"]}

    # STEP 2: High arousal modifier
    high_stress = (
        stress_level is not None and stress_level >= 7 and
        any(s in str(stress_symptoms).lower() for s in ["racing_thoughts", "anxiety", "anxious", "on_edge"])
    )

    # L-theanine dose rule
    severe_sleep = sleep_quality is not None and sleep_quality <= 5
    l_theanine_dose = 400 if (high_stress and severe_sleep) else 200

    # STEP 3: Pattern cases
    has_onset = "difficulty_falling_asleep" in sleep_issues
    has_maintenance = "waking_during_night" in sleep_issues or "waking_unrefreshed" in sleep_issues

    if has_onset:
        # CASE 1: Sleep onset problem
        supplements.append({"substance": "Melatonin", "dose_mg": 1, "timing": "evening", "rationale": "Sleep onset problem — clock-resetter for sleep latency"})
        reasoning.append("Melatonin: difficulty_falling_asleep reported → onset problem")

        if stress_level is not None and (stress_level >= 5 or high_stress):
            supplements.append({"substance": "L-Theanine", "dose_mg": l_theanine_dose, "timing": "evening",
                              "rationale": f"Arousal reduction for sleep onset (stress {stress_level}/10)"})
            reasoning.append(f"L-Theanine {l_theanine_dose}mg: stress {stress_level}/10 {'+ high arousal' if high_stress else ''}")

    elif has_maintenance:
        # CASE 2: Maintenance / non-restorative sleep
        supplements.append({"substance": "L-Theanine", "dose_mg": l_theanine_dose, "timing": "evening",
                          "rationale": "Sleep maintenance — relaxation without sedation"})
        reasoning.append(f"L-Theanine {l_theanine_dose}mg: maintenance/non-restorative sleep pattern")

        if sleep_quality is not None and sleep_quality <= 4:
            supplements.append({"substance": "Valerian Root", "dose_mg": 400, "timing": "evening",
                              "rationale": f"Severe sleep maintenance (quality {sleep_quality}/10) — mild herbal hypnotic"})
            reasoning.append(f"Valerian 400mg: sleep quality ≤4 ({sleep_quality}/10) escalation")

    else:
        # CASE 3: No specific issues but sleep ≤7 or sleep goal
        supplements.append({"substance": "L-Theanine", "dose_mg": l_theanine_dose, "timing": "evening",
                          "rationale": "General sleep support — safe relaxation aid"})
        reasoning.append(f"L-Theanine {l_theanine_dose}mg: general poor sleep (no specific issues reported)")

        if sleep_quality is not None and sleep_quality <= 4:
            supplements.append({"substance": "Melatonin", "dose_mg": 1, "timing": "evening",
                              "rationale": f"Severe sleep quality ({sleep_quality}/10) — consider clock-resetter"})
            reasoning.append("Melatonin 1mg: severe fallback (sleep ≤4)")

    # STEP 4: Global stress safety check
    if high_stress:
        has_theanine = any(s["substance"] == "L-Theanine" for s in supplements)
        if not has_theanine:
            supplements.append({"substance": "L-Theanine", "dose_mg": 200, "timing": "evening",
                              "rationale": "High stress safety net — cognitive arousal management"})
            reasoning.append("L-Theanine 200mg: high stress safety check (wasn't already added)")

        has_melatonin = any(s["substance"] == "Melatonin" for s in supplements)
        if has_melatonin and not has_onset:
            supplements = [s for s in supplements if s["substance"] != "Melatonin"]
            reasoning.append("Melatonin removed: high stress but no onset problem confirmed")

    return {"supplements": supplements, "reasoning": reasoning}


# ─── TIMING OPTIMIZATION ─────────────────────────────────────────────────────

def apply_timing_rules(
    lifestyle: Dict,
    goals: Dict,
    selected_components: List[str] = None
) -> Dict:
    """
    Apply universal timing rules (Framework Step 7.5).
    
    Uses effective_goals (explicit + inferred) and selected_components for
    context-aware timing. The algorithm:
    1. Detect calming needs from goals (keyword scan)
    2. For dual-use supplements (morning focus vs evening calming), check if
       the calming role is already covered by another evening component
    3. Assign timing adaptively based on coverage analysis
    
    Returns timing assignments for magnesium, ashwagandha, L-theanine.
    """
    kb = _load_kb("timing_rules.json")
    rules = kb["universal_rules"]

    sleep_quality = lifestyle.get("sleep_quality")
    stress_level = lifestyle.get("stress_level")
    energy_level = lifestyle.get("energy_level")
    ranked_goals = goals.get("ranked", [])
    top_goal = goals.get("top_goal", "")

    timing_assignments = {}
    evening_components = []

    # ── Classify goal keywords across ALL effective goals ────────────────
    CALMING_KEYWORDS = {"sleep", "anxiety", "mood", "stress", "relax"}
    ENERGY_KEYWORDS = {"energy", "focus", "concentration", "fatigue"}
    has_calming_goal = any(
        any(kw in g.lower() for kw in CALMING_KEYWORDS)
        for g in ranked_goals
    ) if ranked_goals else False
    has_energy_goal = any(
        any(kw in g.lower() for kw in ENERGY_KEYWORDS)
        for g in ranked_goals
    ) if ranked_goals else False

    # ── Build calming supplement coverage from selected components ───────
    # Check which selected supplements are inherently calming (evening_ok in KB)
    # and will be routed to evening. This tells us whether the calming role
    # is already covered, freeing dual-use supplements for their morning benefit.
    _calming_supplements_in_formula = set()
    KNOWN_CALMING_SUBSTANCES = {"ashwagandha", "valerian", "melatonin", "chamomile",
                                "melissa", "lemon balm", "magnesium"}
    if selected_components:
        for comp in selected_components:
            comp_lower = comp.lower()
            if any(calm in comp_lower for calm in KNOWN_CALMING_SUBSTANCES):
                _calming_supplements_in_formula.add(comp_lower)

    # Rule 1: Magnesium ALWAYS evening timing
    magnesium_evening = True
    mg_reason_parts = []
    if sleep_quality is not None and sleep_quality <= 7:
        mg_reason_parts.append(f"sleep quality {sleep_quality}/10")
    if top_goal and "sleep" in top_goal.lower():
        mg_reason_parts.append("sleep goal")
    mg_reason = f"Always evening — Mg bisglycinate supports sleep, recovery, and relaxation"
    if mg_reason_parts:
        mg_reason += f" ({'; '.join(mg_reason_parts)})"
    timing_assignments["magnesium"] = {
        "timing": "evening",
        "delivery": "evening_hard_capsule",
        "reason": mg_reason
    }
    evening_components.append("Magnesium")

    # Rule 2: Ashwagandha timing — adaptive based on calming needs
    # Evening if calming needed; morning only if pure energy/focus with NO calming needs
    if has_calming_goal and sleep_quality is not None and sleep_quality <= 7:
        timing_assignments["ashwagandha"] = {
            "timing": "evening",
            "delivery": "evening_hard_capsule",
            "reason": f"Calming goal + sleep {sleep_quality}/10 → evening synergy"
        }
        evening_components.append("Ashwagandha")
        _calming_supplements_in_formula.add("ashwagandha")
    elif has_calming_goal:
        timing_assignments["ashwagandha"] = {
            "timing": "evening",
            "delivery": "evening_hard_capsule",
            "reason": "Calming goal (anxiety/stress/mood) → evening"
        }
        evening_components.append("Ashwagandha")
        _calming_supplements_in_formula.add("ashwagandha")
    elif has_energy_goal:
        timing_assignments["ashwagandha"] = {
            "timing": "morning",
            "delivery": "morning_hard_capsule",
            "reason": "Pure energy/focus goal, no calming needs → morning"
        }
    else:
        # Default: evening (Ashwagandha is a calming adaptogen)
        timing_assignments["ashwagandha"] = {
            "timing": "evening",
            "delivery": "evening_hard_capsule",
            "reason": "Default evening — Ashwagandha is calming adaptogen"
        }
        evening_components.append("Ashwagandha")
        _calming_supplements_in_formula.add("ashwagandha")

    # Rule 3: L-Theanine — adaptive dual-use timing
    # L-Theanine has two roles: morning (calm focus) vs evening (sleep/relaxation synergy).
    # Decision algorithm:
    #   1. If client has calming needs → check if those needs are ALREADY covered
    #      by another evening calming supplement (Ashwagandha, Valerian, etc.)
    #   2. If calming needs exist AND NOT already covered → evening (primary calming role)
    #   3. If calming needs exist AND already covered by another evening supplement
    #      → morning (secondary focus role — calming is handled)
    #   4. If no calming needs at all → morning
    _other_evening_calming = _calming_supplements_in_formula - {"l-theanine", "l_theanine", "theanine"}
    _calming_already_covered = len(_other_evening_calming) > 0

    if has_calming_goal and not _calming_already_covered:
        # Calming needs exist but NO other calming supplement is in evening → L-Theanine fills the gap
        timing_assignments["l_theanine"] = {
            "timing": "evening",
            "delivery": "evening_hard_capsule",
            "join_with": "Magnesium",
            "reason": "Calming need uncovered — L-Theanine assigned to evening for sleep/relaxation synergy"
        }
        if "L-Theanine" not in evening_components:
            evening_components.append("L-Theanine")
    elif has_calming_goal and _calming_already_covered:
        # Calming needs exist AND another supplement already covers evening calming
        # → L-Theanine more useful in morning for calm focus benefit
        timing_assignments["l_theanine"] = {
            "timing": "evening",
            "delivery": "evening_hard_capsule",
            "join_with": "Magnesium",
            "reason": f"Calming need present — evening synergy with Mg (calming also supported by {', '.join(s.title() for s in _other_evening_calming)})"
        }
        if "L-Theanine" not in evening_components:
            evening_components.append("L-Theanine")
    else:
        # No calming needs at all → morning
        timing_assignments["l_theanine"] = {
            "timing": "morning",
            "delivery": "morning_wellness_capsule",
            "reason": "No calming needs — morning timing for focus benefit"
        }

    return {
        "timing_assignments": timing_assignments,
        "evening_components": evening_components,
        "evening_capsule_needed": len(evening_components) > 0,
    }


# ─── POLYPHENOL EXCLUSION CHECKS ─────────────────────────────────────────────

def check_polyphenol_exclusions(medical: Dict, demographics: Dict = None) -> Dict:
    """
    Check if any polyphenols should be excluded based on medical conditions.
    
    Exclusion rules (from nutritionist-confirmed Quercetin cautions):
    - Pregnancy/breastfeeding → auto-exclude Quercetin
    - Kidney disease → auto-exclude Quercetin
    - Anticoagulants/antiplatelets → flag Quercetin interaction (auto-remove in pipeline)
    
    Returns: {
        "excluded_substances": [list of substance names to exclude],
        "flagged_interactions": [list of interaction warnings],
        "reasoning": [list of reasoning strings]
    }
    """
    excluded = []
    flagged = []
    reasoning = []

    # Normalize medical data
    medical_history = [str(c).lower() for c in medical.get("medical_conditions", []) + medical.get("conditions", []) + medical.get("diagnoses", [])] if medical else []
    medications = [str(m).lower() for m in medical.get("medications", [])] if medical else []
    meds_str = " ".join(medications)

    # Check pregnancy/breastfeeding
    is_pregnant = any(kw in c for c in medical_history for kw in ["pregnant", "pregnancy", "expecting"])
    is_breastfeeding = any(kw in c for c in medical_history for kw in ["breastfeed", "lactating", "nursing"])
    # Also check demographics if available
    if demographics:
        preg_field = demographics.get("pregnant") or demographics.get("pregnancy") or demographics.get("is_pregnant")
        if preg_field and str(preg_field).lower() in ("true", "yes", "1"):
            is_pregnant = True
        bf_field = demographics.get("breastfeeding") or demographics.get("is_breastfeeding") or demographics.get("lactating")
        if bf_field and str(bf_field).lower() in ("true", "yes", "1"):
            is_breastfeeding = True

    if is_pregnant or is_breastfeeding:
        excluded.append("quercetin")
        status = "pregnant" if is_pregnant else "breastfeeding"
        reasoning.append(f"Quercetin auto-excluded: client is {status} (nutritionist rule: don't add unless specifically cleared)")

    # Check kidney disease
    kidney_keywords = ["kidney disease", "renal disease", "kidney failure", "renal failure", "ckd",
                       "chronic kidney", "kidney impairment", "renal impairment", "dialysis",
                       "nephropathy", "kidney stones", "renal stones"]
    has_kidney = any(kw in c for c in medical_history for kw in kidney_keywords)
    if has_kidney:
        excluded.append("quercetin")
        reasoning.append("Quercetin auto-excluded: kidney disease reported")

    # NOTE: Medication-based interaction checks (anticoagulants, polypharmacy, etc.)
    # are now handled by the LLM medication screening in Stage A.5b of generate_formulation.py.
    # This function only handles CONDITION-based exclusions (pregnancy, kidney) which are
    # medical states, not medication interactions.

    return {
        "excluded_substances": list(set(excluded)),
        "flagged_interactions": flagged,
        "reasoning": reasoning,
    }


# ─── GOAL-TRIGGERED MANDATORY SUPPLEMENTS ─────────────────────────────────────

def assess_goal_triggered_supplements(goals: Dict, lifestyle: Dict) -> Dict:
    """Deterministic supplement rules based on client goals.
    
    These supplements MUST be in the formula when specific goals are present.
    The LLM is told to include them with proper KB doses; Stage D validates presence.
    
    Rules:
    1. Energy/fatigue goal → B9 (Folate) + B12 + Vitamin C (standardized)
    2. Sleep goal → L-Theanine (already handled by select_sleep_supplements)
    
    Note: Glutathione is now LLM-decided based on KB health claims
    (Anti-inflammatory, Infection Susceptibility, Skin Quality, Sport/Recovery).
    
    Returns: {
        "mandatory_vitamins": [{"substance": ..., "reason": ...}],
        "mandatory_supplements": [{"substance": ..., "reason": ...}],
        "reasoning": [...]
    }
    """
    ranked_goals = goals.get("ranked", [])
    ranked_lower = [g.lower() for g in ranked_goals]
    reasoning = []
    
    mandatory_vitamins = []
    mandatory_supplements = []
    
    # Rule 1: Energy/fatigue → standardize B9 + B12 + Vitamin C
    ENERGY_KEYWORDS = {"energy", "fatigue", "reduce_fatigue", "boost_energy"}
    has_energy = any(any(kw in g for kw in ENERGY_KEYWORDS) for g in ranked_lower)
    if has_energy:
        mandatory_vitamins.append({"substance": "Folate (B9)", "reason": "Energy goal → standardized B9 (Marijn rule)"})
        mandatory_vitamins.append({"substance": "Vitamin B12", "reason": "Energy goal → standardized B12 (Marijn rule)"})
        mandatory_vitamins.append({"substance": "Vitamin C", "reason": "Energy goal → standardized Vitamin C (Marijn rule)"})
        reasoning.append("Energy/fatigue goal → B9 + B12 + Vitamin C standardized")

    # Rule 2: Skin health → standardize Niacinamide (B3) + Pantothenic Acid (B5)
    # B3: strongest evidence for acne — sebum regulation, anti-inflammatory, barrier repair
    # B5: RCT-proven sebum control and persistent acne reduction (complementary mechanism)
    SKIN_KEYWORDS = {"skin", "improve_skin"}
    has_skin = any(any(kw in g for kw in SKIN_KEYWORDS) for g in ranked_lower)
    if has_skin:
        mandatory_vitamins.append({
            "substance": "Niacinamide (B3)",
            "reason": "Skin goal → standardized Niacinamide B3 (sebum regulation, anti-inflammatory, barrier repair — strongest evidence for acne)"
        })
        mandatory_vitamins.append({
            "substance": "Pantothenic Acid (B5)",
            "reason": "Skin goal → standardized Pantothenic Acid B5 (sebum control, RCT-proven for persistent acne — complementary to B3)"
        })
        reasoning.append("Skin health goal → Niacinamide B3 + Pantothenic Acid B5 standardized")

    # Rule 3: Hormone balance goal → mandatory Ashwagandha (cortisol regulation)
    # Ashwagandha is the clinician-confirmed 1st choice for hormone balance — cortisol
    # normalisation is the primary mechanism for HPA-axis hormone regulation.
    # This is deterministic to guarantee it is always included when hormone_balance is a goal,
    # even if the LLM selects other Stress/Anxiety supplements first.
    HORMONE_KEYWORDS = {"hormone", "hormone_balance"}
    has_hormone = any(any(kw in g for kw in HORMONE_KEYWORDS) for g in ranked_lower)
    if has_hormone:
        mandatory_supplements.append({
            "substance": "Ashwagandha (Withania somnifera)",
            "dose_mg": 300,
            "delivery": "evening_capsule",
            "reason": "Hormone balance goal → mandatory Ashwagandha 300mg (cortisol regulation via HPA-axis modulation)"
        })
        reasoning.append("Hormone balance goal → Ashwagandha standardized (deterministic)")

    # Note: Glutathione selection is now handled by the LLM supplement selection step
    # based on KB health claims (Anti-inflammatory, Infection Susceptibility, Skin Quality, Sport/Recovery).
    # It is no longer forced deterministically.
    
    return {
        "mandatory_vitamins": mandatory_vitamins,
        "mandatory_supplements": mandatory_supplements,
        "reasoning": reasoning,
    }


# ─── MEDICATION INTERACTION RULES (DETERMINISTIC KB-DRIVEN) ──────────────────

def apply_medication_rules(unified_input: Dict) -> Dict:
    """Deterministic medication interaction screening from knowledge base.

    Reads the client's medication list, fuzzy-matches against
    knowledge_base/medication_interactions.json, and returns structured
    formulation constraints that are enforced upstream of all LLM decisions.

    Tier system:
        A — Hard medical override. MUST change formulation. Auto-executed.
        B — Caution adjustment. Auto-executed if conditions are met.
        C — Informational flag. Clinician review only, no auto-action.

    Returns:
        {
            "matched_rules": [...],
            "timing_override": {...} or None,
            "substances_to_remove": set(),
            "removal_reasons": [...],
            "magnesium_removed": bool,
            "clinical_flags": [...],
            "unmatched_medications": [...],   # For Elicit/LLM fallback
        }
    """
    import re as _re
    import unicodedata

    kb = _load_kb("medication_interactions.json")
    rules = kb.get("rules", [])

    # ── Extract medications from questionnaire ───────────────────────────
    q = unified_input.get("questionnaire", {})
    medical = q.get("medical", {})
    medications_raw = medical.get("medications", [])

    # Normalize medication entries to list of {name, dosage} dicts
    medication_entries = []
    for m in medications_raw:
        if isinstance(m, dict):
            medication_entries.append({
                "name": str(m.get("name", "")).strip(),
                "dosage": str(m.get("dosage", "")).strip(),
                "how_long": str(m.get("how_long", "")).strip(),
            })
        elif isinstance(m, str) and m.strip():
            medication_entries.append({"name": m.strip(), "dosage": "", "how_long": ""})

    if not medication_entries:
        return {
            "matched_rules": [],
            "timing_override": None,
            "substances_to_remove": set(),
            "removal_reasons": [],
            "magnesium_removed": False,
            "clinical_flags": [],
            "unmatched_medications": [],
        }

    # ── Normalize text for fuzzy matching ────────────────────────────────
    def _normalize(text: str) -> str:
        """Lowercase, strip accents/special chars, collapse whitespace."""
        text = text.lower().strip()
        # Normalize unicode (e.g., Greek Τ4 → t4)
        text = unicodedata.normalize("NFKD", text)
        text = "".join(c for c in text if not unicodedata.combining(c))
        # Remove dosage suffixes for matching (e.g., "ramipril 2,5mg day" → "ramipril")
        text = _re.sub(r'[\d,.\s]*(mg|mcg|ug|ml|day|daily|forever|years?).*$', '', text).strip()
        return text

    def _matches_medication(med_name_normalized: str, rule: Dict) -> bool:
        """Check if a normalized medication name matches a KB rule."""
        rule_name = rule.get("medication", "").lower()
        aliases = [a.lower() for a in rule.get("aliases", [])]
        # Also normalize aliases (handle Greek chars etc.)
        aliases_normalized = [_normalize(a) for a in rule.get("aliases", [])]

        all_names = [rule_name] + aliases + aliases_normalized

        for candidate in all_names:
            if not candidate:
                continue
            # Exact match
            if med_name_normalized == candidate:
                return True
            # Substring match (medication name contains KB name or vice versa)
            if candidate in med_name_normalized or med_name_normalized in candidate:
                return True

        return False

    # ── Match medications against KB rules ───────────────────────────────
    matched_rules = []
    matched_medication_names = set()
    unmatched_medications = []

    for med_entry in medication_entries:
        med_name_raw = med_entry["name"]
        med_name_norm = _normalize(med_name_raw)

        if not med_name_norm:
            continue

        found_match = False
        for rule in rules:
            if _matches_medication(med_name_norm, rule):
                matched_rules.append({
                    "rule": rule,
                    "medication_raw": med_name_raw,
                    "medication_dosage": med_entry.get("dosage", ""),
                })
                matched_medication_names.add(med_name_norm)
                found_match = True
                break  # One rule per medication

        if not found_match:
            unmatched_medications.append(med_entry)

    # ── Execute matched rules ────────────────────────────────────────────
    timing_override = None
    substances_to_remove = set()
    removal_reasons = []
    magnesium_removed = False
    clinical_flags = []

    for match in matched_rules:
        rule = match["rule"]
        tier = rule.get("tier", "C")
        rule_id = rule.get("rule_id", "?")
        medication_raw = match["medication_raw"]

        # ── Tier A: Hard medical override ────────────────────────────
        if tier == "A":
            resolution = rule.get("resolution", {})
            res_type = resolution.get("type")

            if res_type == "timing_override":
                timing_override = {
                    "rule_id": rule_id,
                    "medication": medication_raw,
                    "medication_normalized": rule.get("medication", ""),
                    "move_to": resolution.get("move_to", "dinner"),
                    "affects": resolution.get("affects", ["all_units"]),
                    "exclude_from_override": resolution.get("exclude_from_override", []),
                    "reason": resolution.get("reason", ""),
                    "clinical_note": rule.get("clinical_note", ""),
                    "tier": "A",
                    "severity": rule.get("severity", "high"),
                }
                clinical_flags.append({
                    "rule_id": rule_id,
                    "tier": "A",
                    "severity": rule.get("severity", "high"),
                    "title": f"TIMING OVERRIDE: {rule.get('medication', '').title()} — all units moved to {resolution.get('move_to', 'dinner')}",
                    "detail": rule.get("clinical_note", ""),
                    "medication": medication_raw,
                    "auto_executed": True,
                })

            elif res_type == "unconditional_remove":
                for substance in resolution.get("substances", []):
                    substances_to_remove.add(substance.lower())
                    removal_reasons.append({
                        "substance": substance,
                        "medication": medication_raw,
                        "rule_id": rule_id,
                        "tier": "A",
                        "mechanism": rule.get("description", ""),
                        "reason": resolution.get("reason", ""),
                    })

        # ── Tier B: Caution adjustment ───────────────────────────────
        elif tier == "B":
            for conflict in rule.get("conflicting_supplements", []):
                sub_resolution = conflict.get("resolution", {})
                res_type = sub_resolution.get("type")

                if res_type == "conditional_remove":
                    # Check if condition is met (alternative support exists)
                    condition = sub_resolution.get("condition", "")
                    if condition == "alternative_stress_support_exists":
                        # This will be fully evaluated in generate_formulation.py
                        # after LLM decisions are made. For now, mark as pending.
                        substances_to_remove.add(conflict["substance"].lower())
                        if "magnesium" in conflict["substance"].lower():
                            magnesium_removed = True
                        removal_reasons.append({
                            "substance": conflict["substance"],
                            "medication": medication_raw,
                            "rule_id": rule_id,
                            "tier": "B",
                            "mechanism": conflict.get("mechanism", ""),
                            "reason": sub_resolution.get("reason", ""),
                            "condition": condition,
                            "check_alternatives": sub_resolution.get("check_alternatives", []),
                        })
                        clinical_flags.append({
                            "rule_id": rule_id,
                            "tier": "B",
                            "severity": rule.get("severity", "moderate"),
                            "title": f"CONDITIONAL REMOVAL: {conflict['substance'].title()} — {rule.get('medication', '').title()} interaction",
                            "detail": rule.get("clinical_note", ""),
                            "medication": medication_raw,
                            "auto_executed": True,
                            "condition": condition,
                        })

                elif res_type == "unconditional_remove":
                    substances_to_remove.add(conflict["substance"].lower())
                    if "magnesium" in conflict["substance"].lower():
                        magnesium_removed = True
                    removal_reasons.append({
                        "substance": conflict["substance"],
                        "medication": medication_raw,
                        "rule_id": rule_id,
                        "tier": "B",
                        "mechanism": conflict.get("mechanism", ""),
                        "reason": sub_resolution.get("reason", ""),
                    })

                elif res_type == "flag_only":
                    clinical_flags.append({
                        "rule_id": rule_id,
                        "tier": "B",
                        "severity": rule.get("severity", "moderate"),
                        "title": f"CAUTION: {conflict['substance'].title()} + {rule.get('medication', '').title()}",
                        "detail": conflict.get("mechanism", ""),
                        "medication": medication_raw,
                        "auto_executed": False,
                    })

        # ── Tier C: Informational flag only ──────────────────────────
        elif tier == "C":
            clinical_flags.append({
                "rule_id": rule_id,
                "tier": "C",
                "severity": rule.get("severity", "low"),
                "title": f"INFO: {rule.get('medication', '').title()} — review recommended",
                "detail": rule.get("clinical_note", rule.get("description", "")),
                "medication": medication_raw,
                "auto_executed": False,
            })

    # ── MED_003: Curcumin + any serious prescription medication ──────────
    # Fires independently of the matched-rules loop above.
    # Scans ALL medication entries (matched + unmatched) against the OTC whitelist.
    # If ANY non-OTC medication is present → remove curcumin (Tier A).
    # If ALL medications are OTC → raise Tier C review flag only.
    if medication_entries:
        med_003 = next((r for r in rules if r.get("rule_id") == "MED_003"), None)
        if med_003:
            otc_whitelist = [w.lower().strip() for w in med_003.get("otc_whitelist", [])]

            def _is_otc(med_name: str) -> bool:
                """Return True if medication name matches any OTC whitelist entry."""
                med_lower = _normalize(med_name)
                return any(otc in med_lower or med_lower in otc for otc in otc_whitelist)

            non_otc_meds = [m for m in medication_entries if not _is_otc(m["name"])]
            otc_only_meds = [m for m in medication_entries if _is_otc(m["name"])]

            if non_otc_meds:
                # Tier A: unconditional remove curcumin
                substances_to_remove.add("curcumin")
                for med_entry in non_otc_meds:
                    # Find a known_affected_medications entry for richer mechanism text
                    known = next(
                        (k for k in med_003.get("known_affected_medications", [])
                         if _normalize(med_entry["name"]) in [_normalize(k["name"])] +
                         [_normalize(a) for a in k.get("aliases", [])]),
                        None
                    )
                    mechanism = known["mechanism"] if known else med_003["description"]
                    removal_reasons.append({
                        "substance": "curcumin",
                        "medication": med_entry["name"],
                        "rule_id": "MED_003",
                        "tier": "A",
                        "mechanism": mechanism,
                        "reason": med_003["resolution"]["reason"],
                        "replace_with_category": med_003["resolution"].get("replace_with_category", "Anti-inflammatory"),
                    })
                clinical_flags.append({
                    "rule_id": "MED_003",
                    "tier": "A",
                    "severity": "high",
                    "title": f"CURCUMIN EXCLUDED: CYP3A4/P-gp interaction with {', '.join(m['name'] for m in non_otc_meds)}",
                    "detail": med_003["clinical_note"],
                    "medication": ", ".join(m["name"] for m in non_otc_meds),
                    "auto_executed": True,
                    "replacement_note": med_003["resolution"].get("replace_note", ""),
                })
                print(f"  🚫 MED_003: Curcumin excluded — CYP3A4/P-gp interaction with: {[m['name'] for m in non_otc_meds]}")

            elif otc_only_meds:
                # Tier C: flag only — curcumin stays but needs review
                clinical_flags.append({
                    "rule_id": "MED_003",
                    "tier": "C",
                    "severity": "low",
                    "title": f"CURCUMIN REVIEW: OTC medication present — low interaction risk but verify",
                    "detail": f"Client takes OTC medication(s): {', '.join(m['name'] for m in otc_only_meds)}. Curcumin CYP3A4 interaction risk is low at OTC doses but warrants clinician review.",
                    "medication": ", ".join(m["name"] for m in otc_only_meds),
                    "auto_executed": False,
                })
                print(f"  ⚠️  MED_003: Curcumin flag (Tier C) — OTC meds present: {[m['name'] for m in otc_only_meds]}")

    return {
        "matched_rules": matched_rules,
        "timing_override": timing_override,
        "substances_to_remove": substances_to_remove,
        "removal_reasons": removal_reasons,
        "magnesium_removed": magnesium_removed,
        "clinical_flags": clinical_flags,
        "unmatched_medications": unmatched_medications,
    }


# ─── CAPSULE UNDERFILL COMPANION SELECTION ────────────────────────────────────

CAPSULE_MAX_MG = 650
UNDERFILL_THRESHOLD = 0.10  # 10% of capsule capacity

# Labels that are EXCLUDED from underfill check (fixed composition)
_SKIP_LABELS = {"probiotic hard capsule", "magnesium bisglycinate capsule"}


def assess_capsule_underfill(
    recipe_units: List[Dict],
    active_health_claims: List[str],
    substances_to_remove: set = None,
    existing_components: List[str] = None,
) -> List[Dict]:
    """Check all hard capsule units for underfill and propose companion components.

    Runs AFTER capsule layout is built. For any capsule with fill < 10% of
    capacity (65mg in size 00), searches the supplement KB for a compatible
    companion component from the same or related health claim category.

    Companion selection rules:
      1. Same health claim category as primary substance, next KB rank
      2. If exhausted, expand to other active health claims for this client
      3. Filter candidates by:
         a. delivery_constraint allows capsule ("any" or "capsule_only")
         b. timing_restriction compatible with capsule timing
         c. Not already present in any formulation unit
         d. Not in substances_to_remove (medication rules)
         e. interaction_risk is not "medium" or "high" (conservative)
         f. Min KB dose fits within remaining capsule space
      4. If valid candidate found → return as companion proposal
      5. If no candidate → log warning, leave capsule as-is

    Args:
        recipe_units:       List of unit dicts from manufacturing recipe
        active_health_claims: Client's active supplement_claims list
        substances_to_remove: Set of substance names removed by medication rules
        existing_components:  List of all component names already in the formula

    Returns:
        List of companion proposals, each:
        {
            "unit_number": int,
            "unit_label": str,
            "primary_substance": str,
            "companion_substance": str,
            "companion_dose_mg": float,
            "companion_health_claim": str,
            "companion_rank": str,
            "rationale": str,
        }
        Empty list if no underfills found or no valid companions available.
    """
    substances_to_remove = substances_to_remove or set()
    existing_components = existing_components or []

    # Normalize existing components for duplicate checking
    existing_lower = {c.lower().strip() for c in existing_components}
    removed_lower = {s.lower().strip() for s in substances_to_remove}

    # Load supplement KB
    kb = _load_kb("supplements_nonvitamins.json")
    supplements_flat = kb.get("supplements_flat", [])

    proposals = []

    for unit in recipe_units:
        label = (unit.get("label") or "").lower()

        # Skip non-capsule units and fixed-composition capsules
        fmt = unit.get("format", {})
        if fmt.get("type") not in ("hard_capsule",):
            continue
        if any(skip in label for skip in _SKIP_LABELS):
            continue

        # Determine fill weight
        fill_mg = (
            unit.get("fill_weight_mg")
            or unit.get("fill_weight_per_capsule_mg")
            or unit.get("total_fill_weight_mg")
            or 0
        )

        # Check underfill threshold
        if fill_mg >= UNDERFILL_THRESHOLD * CAPSULE_MAX_MG:
            continue

        # Identify primary substance(s) and their health claims
        ingredients = unit.get("ingredients") or []
        capsule_layout = unit.get("capsule_layout") or []

        primary_substances = []
        primary_claims = set()

        for ing in ingredients:
            comp_name = ing.get("component", "")
            primary_substances.append(comp_name)

            # Look up health claims from KB
            for supp in supplements_flat:
                if supp.get("substance", "").lower() == comp_name.lower():
                    for claim in supp.get("parsed", {}).get("health_claims", []):
                        primary_claims.add(claim)
                    break

        # Determine capsule timing context
        timing = (unit.get("timing") or "morning").lower()
        is_evening = "evening" in timing

        remaining_mg = CAPSULE_MAX_MG - fill_mg

        # Build candidate list: same claim first, then other active claims
        claim_search_order = list(primary_claims)
        for claim in active_health_claims:
            if claim not in claim_search_order:
                claim_search_order.append(claim)

        best_candidate = None

        for claim in claim_search_order:
            # Collect KB supplements for this claim, sorted by rank
            claim_supps = [
                s for s in supplements_flat
                if claim in s.get("parsed", {}).get("health_claims", [])
            ]
            claim_supps.sort(key=lambda s: s.get("parsed", {}).get("rank_priority", 99))

            for supp in claim_supps:
                supp_name = supp.get("substance", "")
                supp_id = supp.get("id", "")
                supp_lower = supp_name.lower()

                # Skip if it's the primary substance itself
                if any(supp_lower == ps.lower() for ps in primary_substances):
                    continue

                # Skip if already in formula
                if supp_lower in existing_lower or supp_id.lower() in existing_lower:
                    continue

                # Skip if removed by medication rules
                if supp_lower in removed_lower or supp_id in removed_lower:
                    continue

                # Check delivery constraint
                delivery = supp.get("delivery_constraint", "any")
                if delivery not in ("any", "capsule_only"):
                    continue

                # Check timing compatibility
                timing_restriction = supp.get("timing_restriction", "any")
                if is_evening and timing_restriction == "morning_only":
                    continue
                if not is_evening and timing_restriction == "evening_only":
                    continue

                # Check interaction risk (conservative: skip medium+)
                interaction = supp.get("parsed", {}).get("interaction_level", "low")
                if interaction in ("medium", "high"):
                    continue

                # Determine min dose in mg
                parsed_dose = supp.get("parsed", {}).get("dose", {})
                dose_unit = parsed_dose.get("unit", "mg")
                min_dose = parsed_dose.get("min") or parsed_dose.get("value")

                if min_dose is None:
                    continue

                # Convert g → mg if needed
                if dose_unit == "g":
                    min_dose_mg = min_dose * 1000
                else:
                    min_dose_mg = min_dose

                # Check if dose fits remaining capsule space
                if min_dose_mg > remaining_mg:
                    continue

                # Valid candidate found
                best_candidate = {
                    "unit_number": unit.get("unit_number"),
                    "unit_label": unit.get("label"),
                    "primary_substance": ", ".join(primary_substances),
                    "primary_fill_mg": fill_mg,
                    "companion_substance": supp_name,
                    "companion_id": supp_id,
                    "companion_dose_mg": min_dose_mg,
                    "companion_health_claim": claim,
                    "companion_rank": supp.get("rank", "?"),
                    "companion_interaction_risk": supp.get("interaction_risk", "?"),
                    "rationale": (
                        f"Capsule under-filled ({fill_mg}mg / {CAPSULE_MAX_MG}mg = "
                        f"{fill_mg/CAPSULE_MAX_MG*100:.1f}%). "
                        f"Added {supp_name} ({min_dose_mg}mg) from '{claim}' category "
                        f"({supp.get('rank', '?')}) to improve capsule utilisation."
                    ),
                }
                break  # Take first valid candidate per claim

            if best_candidate:
                break  # Stop searching claims once a companion is found

        if best_candidate:
            proposals.append(best_candidate)
        elif fill_mg < UNDERFILL_THRESHOLD * CAPSULE_MAX_MG:
            # Log warning for capsules where no companion could be found
            print(
                f"  ⚠️ UNDERFILL WARNING: Unit {unit.get('unit_number')} "
                f"'{unit.get('label')}' fill={fill_mg}mg ({fill_mg/CAPSULE_MAX_MG*100:.1f}%) — "
                f"no valid companion found for [{', '.join(primary_substances)}]"
            )

    return proposals


# ─── MAIN RULES ENGINE ───────────────────────────────────────────────────────

def apply_rules(unified_input: Dict) -> Dict:
    """
    Main entry point — apply all deterministic rules to unified input.

    Args:
        unified_input: Output from parse_inputs.parse_inputs()

    Returns:
        rule_outputs dict with all deterministic decisions
    """
    microbiome = unified_input["microbiome"]
    questionnaire = unified_input["questionnaire"]

    # 1. Sensitivity classification (with FODMAP digestion-goal override)
    sensitivity = classify_sensitivity(questionnaire["digestive"])
    
    # FODMAP override: if digestion comfort is a goal but no bloating reported,
    # treat as at least moderate sensitivity (conservative FODMAP clamping)
    ranked_goals = questionnaire["goals"].get("ranked", [])
    has_digestion_goal = any("digestion" in g.lower() or "comfort" in g.lower() for g in ranked_goals)
    bloating = questionnaire["digestive"].get("bloating_severity")
    if has_digestion_goal and (bloating is None or bloating <= 3) and sensitivity["classification"] == "low":
        sensitivity["classification"] = "moderate"
        sensitivity["reasoning"].append(f"FODMAP override: digestion goal present but bloating {bloating or 'N/R'}/10 → bumped to moderate (conservative)")

    # 1b. Symptom narrative mode (B1 — 24 April 2026)
    #     Deterministic downstream signal for the Section 3 health-report
    #     builder. Decides whether the bloating narrative can mention
    #     microbial fermentation (and how strongly). See docstring for
    #     classify_symptom_narrative_mode().
    narrative_mode = classify_symptom_narrative_mode(
        sensitivity=sensitivity,
        microbiome=microbiome,
        digestive=questionnaire["digestive"],
    )
    sensitivity["symptom_narrative_mode"] = narrative_mode["mode"]
    sensitivity["corroborating_microbiome_signals"] = narrative_mode["corroborating_microbiome_signals"]
    sensitivity["symptom_narrative_reasoning"] = narrative_mode["reasoning"]

    # 1c. Multi-symptom narrative modes (B1 extended — 24 April 2026)
    #     Framework-level dict covering bloating + stress + sleep + fatigue +
    #     skin + immune. Each entry carries {mode, signals, severity_band,
    #     min_corrob_required}. Section 3 of the health report reads from
    #     `input_summary.questionnaire_driven.symptom_narrative_modes`.
    symptom_narrative_modes = classify_all_symptom_narrative_modes(
        unified_input=unified_input,
        sensitivity=sensitivity,
    )

    # 2. Health claim extraction
    health_claims = extract_health_claims(
        questionnaire["goals"],
        microbiome["vitamin_signals"]
    )

    # 3. Therapeutic dose triggers
    therapeutic = check_therapeutic_triggers(
        questionnaire["medical"],
        questionnaire["lifestyle"]
    )

    # 4. Prebiotic dose range (using default 50B CFU — will be refined after mix selection)
    prebiotic_range = calculate_prebiotic_range(sensitivity, cfu_billions=50)

    # 5. Magnesium need assessment (replaces old barrier support)
    magnesium = assess_magnesium_needs(
        questionnaire["lifestyle"],
        questionnaire["goals"]
    )

    # 6. Softgel needs assessment
    softgel = assess_softgel_needs(
        health_claims,
        questionnaire["medical"],
        questionnaire["lifestyle"],
        questionnaire["goals"]
    )

    # 7. Sleep supplement selection (evidence-based)
    sleep_supplements = select_sleep_supplements(
        questionnaire["lifestyle"],
        questionnaire["goals"]
    )

    # 8. Timing optimization
    timing = apply_timing_rules(
        questionnaire["lifestyle"],
        questionnaire["goals"]
    )

    # 9. Goal-triggered mandatory supplements (deterministic)
    goal_triggered = assess_goal_triggered_supplements(
        questionnaire["goals"],
        questionnaire["lifestyle"]
    )

    return {
        "sensitivity": sensitivity,
        "symptom_narrative_modes": symptom_narrative_modes,
        "health_claims": health_claims,
        "therapeutic_triggers": therapeutic,
        "prebiotic_range": prebiotic_range,
        "magnesium": magnesium,
        "softgel": softgel,
        "sleep_supplements": sleep_supplements,
        "timing": timing,
        "goal_triggered_supplements": goal_triggered,
    }


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    from parse_inputs import parse_inputs

    parser = argparse.ArgumentParser(description="Apply formulation rules to sample")
    parser.add_argument("--sample-dir", required=True, help="Path to sample directory")
    parser.add_argument("--output", help="Optional: save rule outputs JSON to file")
    args = parser.parse_args()

    unified = parse_inputs(args.sample_dir)
    results = apply_rules(unified)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"Rule outputs saved to: {args.output}")
    else:
        print(json.dumps(results, indent=2, ensure_ascii=False))
