#!/usr/bin/env python3
"""
Formulation Pipeline Orchestrator

Main entry point that chains all pipeline stages:
  A. Parse inputs (microbiome + questionnaire)
  B. Apply deterministic rules
  C. LLM clinical decisions (mix, supplements, prebiotics)
  D. Post-processing (timing, delivery, barrier support)
  E. Weight calculation + validation
  F. Assemble master JSON + platform JSON

Usage:
    # Single sample (with LLM)
    python generate_formulation.py --sample-dir /path/to/analysis/batch/sample/

    # Single sample (no LLM — offline mode for testing)
    python generate_formulation.py --sample-dir /path/to/sample/ --no-llm

    # Process entire batch
    python generate_formulation.py --batch-dir /path/to/analysis/batch/
"""

import json
import os
import sys
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

# Local imports
from parse_inputs import parse_inputs
from rules_engine import apply_rules, apply_timing_rules, calculate_prebiotic_range
from llm_decisions import run_llm_decisions
from weight_calculator import FormulationCalculator, distribute_cfu_evenly, CapsuleStackingOptimizer, JAR_TARGET_G
SACHET_CAPACITY_G = JAR_TARGET_G  # backward compat alias for any remaining references
from platform_mapping import build_platform_json, build_decision_trace, build_manufacturing_recipe, build_component_rationale
from dose_optimizer import DoseOptimizer, add_excipient_if_needed

SCRIPT_DIR = Path(__file__).parent
KB_DIR = SCRIPT_DIR / "knowledge_base"


# ─── TEE WRITER — captures stdout while passing through to terminal ──────────

class TeeWriter:
    """Wraps stdout to simultaneously print to terminal and capture to buffer.
    
    Usage:
        tee = TeeWriter()
        tee.start()
        print("This goes to terminal AND buffer")
        content = tee.stop()  # Returns captured text, restores stdout
    """
    def __init__(self):
        self._buffer = []
        self._original_stdout = None
    
    def start(self):
        self._original_stdout = sys.stdout
        self._buffer = []
        sys.stdout = self
    
    def stop(self) -> str:
        if self._original_stdout:
            sys.stdout = self._original_stdout
            self._original_stdout = None
        return ''.join(self._buffer)
    
    def write(self, text):
        if self._original_stdout:
            self._original_stdout.write(text)
        self._buffer.append(text)
    
    def flush(self):
        if self._original_stdout:
            self._original_stdout.flush()
    
    def fileno(self):
        if self._original_stdout:
            return self._original_stdout.fileno()
        return 1  # stdout default
    
    def reconfigure(self, **kwargs):
        if self._original_stdout and hasattr(self._original_stdout, 'reconfigure'):
            self._original_stdout.reconfigure(**kwargs)

# Shared modules — single source of truth for priority system + formatting
sys.path.insert(0, str(SCRIPT_DIR.parent / 'shared'))
from guild_priority import build_priority_list, PRIORITY_RANK_MAP, PRIORITY_COLOR_MAP, PRIORITY_HEX_MAP, HARMFUL_GUILD_NAMES as _SHARED_HARMFUL, MUCIN_GUILD_NAMES as _SHARED_MUCIN
from formatting import format_dose as _format_dose, sleep_label as _sleep_label


# ─── VITAMIN PRODUCTION DISCLAIMER ───────────────────────────────────────────

VITAMIN_PRODUCTION_DISCLAIMER = (
    "Your microbiome composition suggests you have {status} populations of bacteria "
    "associated with {vitamin} production. However, this tells us about potential — "
    "not actual vitamin output. To know whether you're getting enough {vitamin}, "
    "a blood test is the only reliable measure. Think of this as a signal worth "
    "exploring, not a diagnosis."
)


# NOTE: _format_dose and _sleep_label are imported from shared/formatting.py
# (see imports at top of file). They're kept as module-level names for backward
# compatibility with platform_mapping.py and generate_dashboards.py.


# ─── TEXT TRUNCATION DETECTION ────────────────────────────────────────────────

def _check_text_truncation(text: str, field_name: str = "") -> Optional[str]:
    """Detect truncated LLM text that ends mid-word or mid-sentence.
    
    Returns warning string if truncation detected, None if OK.
    """
    if not text or len(text) < 20:
        return None
    text = text.strip()
    # Check for mid-word truncation (ends with lowercase letter without punctuation)
    if text[-1].isalpha() and text[-1].islower() and not text.endswith(('etc', 'al', 'vs')):
        # Check if the last word is incomplete (< 3 chars and not a common short word)
        last_word = text.split()[-1] if text.split() else ""
        common_endings = {"a", "an", "as", "at", "be", "by", "do", "go", "he", "if", "in",
                          "is", "it", "me", "my", "no", "of", "on", "or", "so", "to", "up", "us", "we"}
        if len(last_word) <= 2 and last_word.lower() not in common_endings:
            return f"⚠️ TRUNCATION [{field_name}]: Text ends mid-word: '...{text[-30:]}'"
    # Check for arrow truncation (→ at end)
    if text.endswith('→') or text.endswith('—') or text.endswith(' –'):
        return f"⚠️ TRUNCATION [{field_name}]: Text ends with connector: '...{text[-30:]}'"
    return None


# ─── GOAL-MINERAL AFFINITY CHECK ─────────────────────────────────────────────

GOAL_MINERAL_AFFINITIES = {
    # goal_keyword → [expected minerals]
    "skin": ["zinc"],
    "immune": ["zinc"],
    "longevity": ["zinc"],
    "bone": ["calcium"],
    "energy": ["iron"],  # iron only flagged for females (gate handles males)
    "fatigue": ["iron"],
}


def _assess_questionnaire_coverage(unified_input: Dict) -> Dict:
    """Assess questionnaire completeness and flag areas with missing data."""
    q = unified_input.get("questionnaire", {})
    completion = q.get("completion", {})
    completion_pct = completion.get("completion_pct", 0)
    completed_steps = completion.get("completed_steps", [])

    missing_areas = []
    confidence_impacts = []

    # Check each critical data area
    lifestyle = q.get("lifestyle", {})
    if lifestyle.get("stress_level") is None:
        missing_areas.append("Stress level not reported")
        confidence_impacts.append("Sleep/stress supplement decisions based on microbiome only")
    if lifestyle.get("sleep_quality") is None:
        missing_areas.append("Sleep quality not reported")
        confidence_impacts.append("Melatonin/L-theanine/valerian decisions may be suboptimal")
    if lifestyle.get("energy_level") is None:
        missing_areas.append("Energy level not reported")

    digestive = q.get("digestive", {})
    if digestive.get("bloating_severity") is None:
        missing_areas.append("Bloating severity not reported")
        confidence_impacts.append("Sensitivity classification defaults to 'normal'")
    if digestive.get("stool_type") is None:
        missing_areas.append("Stool type not reported")

    goals = q.get("goals", {})
    if not goals.get("ranked"):
        missing_areas.append("Health goals not ranked")
        confidence_impacts.append("Supplement prioritization based on microbiome patterns only")

    medical = q.get("medical", {})
    if not medical.get("reported_deficiencies") and not medical.get("vitamin_deficiencies"):
        missing_areas.append("No vitamin deficiencies reported (may exist)")
        confidence_impacts.append("Therapeutic vitamin doses not triggered — standard doses used")

    food_triggers = q.get("food_triggers", {})
    if food_triggers.get("count", 0) == 0:
        missing_areas.append("No food triggers/sensitivities reported")

    # Overall assessment
    if completion_pct == 0:
        level = "MINIMAL"
        summary = "No questionnaire data available — formulation based entirely on microbiome analysis"
    elif completion_pct < 50:
        level = "LOW"
        summary = f"Questionnaire {completion_pct:.0f}% complete — several personalization signals missing"
    elif completion_pct < 80:
        level = "MODERATE"
        summary = f"Questionnaire {completion_pct:.0f}% complete — some personalization signals missing"
    else:
        level = "GOOD"
        summary = f"Questionnaire {completion_pct:.0f}% complete — sufficient data for personalization"

    return {
        "completion_pct": completion_pct,
        "completed_steps": completed_steps,
        "coverage_level": level,
        "summary": summary,
        "missing_data_areas": missing_areas,
        "confidence_impacts": confidence_impacts,
        "recommendation": "Consider re-running formulation after questionnaire completion" if level in ("MINIMAL", "LOW") else None,
    }


def _generate_manufacturing_recipe_md(recipe: Dict, sample_id: str, q_coverage: Dict) -> str:
    """Generate a human-readable manufacturing recipe markdown from the JSON recipe."""
    from datetime import datetime

    lines = []
    lines.append(f"# Manufacturing Recipe")
    lines.append("")
    lines.append(f"**Client Code:** {sample_id}  ")
    lines.append(f"**Date:** {datetime.now().strftime('%B %d, %Y')}  ")
    lines.append(f"**Protocol:** {recipe.get('protocol_summary', 'N/A')}  ")
    lines.append(f"**Duration:** {recipe.get('protocol_duration_weeks', 16)} weeks  ")
    lines.append(f"**Validation:** {recipe.get('validation', 'N/A')}")

    # Questionnaire coverage warning
    if q_coverage.get("coverage_level") in ("MINIMAL", "LOW"):
        lines.append("")
        lines.append(f"> ⚠️ **LIMITED QUESTIONNAIRE DATA** ({q_coverage['coverage_level']})")
        lines.append(f"> {q_coverage['summary']}")
        if q_coverage.get("missing_data_areas"):
            for area in q_coverage["missing_data_areas"]:
                lines.append(f"> - {area}")
        if q_coverage.get("recommendation"):
            lines.append(f"> **Recommendation:** {q_coverage['recommendation']}")

    lines.append("")
    lines.append("---")
    lines.append("")

    # Units
    for unit in recipe.get("units", []):
        unit_num = unit.get("unit_number", "?")
        label = unit.get("label", "Unknown")
        fmt = unit.get("format", {})
        timing = unit.get("timing", "morning")
        qty = unit.get("quantity", 1)

        lines.append(f"## Unit {unit_num}: {label}")
        lines.append("")

        # Format details
        fmt_type = fmt.get("type", "unknown").replace("_", " ").title()
        fmt_size = fmt.get("size", "")
        fmt_material = fmt.get("material", "")
        if fmt_type:
            lines.append(f"**Format:** {f'Size {fmt_size} ' if fmt_size else ''}{fmt_material} {fmt_type}".strip())

        fill = unit.get("fill_weight_mg") or unit.get("fill_weight_g")
        fill_unit = "mg" if unit.get("fill_weight_mg") else "g"
        if fill:
            lines.append(f"**Fill Weight:** {fill}{fill_unit}")

        lines.append(f"**Timing:** {qty}× {timing}")

        storage = unit.get("storage")
        if storage:
            lines.append(f"**Storage:** {storage}")

        lines.append("")

        # Ingredients table
        ingredients = unit.get("ingredients", unit.get("ingredients_per_unit", []))
        if ingredients:
            # Determine columns
            has_cfu = any("cfu_billions" in ing for ing in ingredients)
            has_category = any("category" in ing for ing in ingredients)

            header = "| Component | Amount |"
            separator = "|-----------|--------|"
            if has_cfu:
                header += " CFU |"
                separator += "-----|"

            lines.append(header)
            lines.append(separator)

            for ing in ingredients:
                component = ing.get("component", "?")
                # Get amount
                amount_g = ing.get("amount_g")
                amount_mg = ing.get("amount_mg")
                dose = ing.get("dose_per_softgel", ing.get("amount", ""))
                if amount_g:
                    amount_str = f"{amount_g}g"
                elif amount_mg:
                    amount_str = f"{amount_mg}mg"
                elif dose:
                    amount_str = str(dose)
                else:
                    amount_str = "—"

                row = f"| {component} | {amount_str} |"
                if has_cfu:
                    cfu = ing.get("cfu_billions")
                    row += f" {cfu}B CFU |" if cfu else " — |"
                lines.append(row)

        lines.append("")

        # Unit total
        total_weight = unit.get("total_weight_mg") or unit.get("total_weight_g")
        if total_weight:
            tw_unit = "mg" if unit.get("total_weight_mg") else "g"
            total_cfu = sum(i.get("cfu_billions", 0) for i in ingredients)
            cfu_str = f", {total_cfu}B CFU" if total_cfu else ""
            lines.append(f"**Unit {unit_num} Total:** {total_weight}{tw_unit}{cfu_str}")

        lines.append("")
        lines.append("---")
        lines.append("")

    # Grand total
    grand = recipe.get("grand_total", {})
    if grand:
        lines.append("## Grand Total (Daily)")
        lines.append("")
        lines.append(f"- **Total Units:** {grand.get('total_units', '?')}")
        lines.append(f"- **Total Daily Weight:** {grand.get('total_daily_weight_g', '?')}g")
        lines.append(f"- **Morning Units:** {grand.get('morning_units', '?')}")
        lines.append(f"- **Evening Units:** {grand.get('evening_units', '?')}")
        lines.append("")

    # Footer
    lines.append("---")
    lines.append("")
    lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} | Pipeline v1.0*")

    return "\n".join(lines)


def _markdown_to_pdf(md_path: str, pdf_path: str) -> bool:
    """Convert Markdown to PDF using pandoc."""
    import subprocess
    pandoc_path = shutil.which('pandoc')
    if not pandoc_path:
        print(f"  ⚠️ PDF generation skipped — pandoc not found. Install with: brew install pandoc")
        return False
    try:
        result = subprocess.run(
            [pandoc_path, md_path, '-o', pdf_path,
             '--pdf-engine=xelatex',
             '-V', 'geometry:margin=2cm',
             '-V', 'fontsize=11pt',
             '-V', 'mainfont=Helvetica Neue',
             '-V', 'monofont=Courier New',
             '--highlight-style=tango'],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            return True
        # Fallback without xelatex
        result2 = subprocess.run(
            [pandoc_path, md_path, '-o', pdf_path,
             '-V', 'geometry:margin=2cm'],
            capture_output=True, text=True, timeout=60
        )
        if result2.returncode == 0:
            return True
        print(f"  ⚠️ PDF generation failed: {result2.stderr[:200]}")
        return False
    except subprocess.TimeoutExpired:
        print(f"  ⚠️ PDF generation timed out")
        return False
    except Exception as e:
        print(f"  ⚠️ PDF generation failed: {e}")
        return False


def _build_component_registry(calc, mix, supplements, prebiotics, rule_outputs, unified_input):
    """Build component registry — single source of truth for ALL component tables.
    
    Built from the ACTUAL calculator state (post-dedup, post-trim).
    Each entry has: substance, dose, delivery, category, source, health_claims,
    based_on, what_it_targets, informed_by.
    
    This registry is consumed by:
    - build_component_rationale() for the health table
    - generate_dashboards.py for Step 7
    - Source attribution % calculation
    """
    from platform_mapping import _extract_claims_from_rationale, _derive_prebiotic_target, _derive_mg_target
    
    registry = []
    goals = unified_input.get("questionnaire", {}).get("goals", {}).get("ranked", [])
    q = unified_input.get("questionnaire", {})
    stress = q.get("lifestyle", {}).get("stress_level", "?")
    sleep = q.get("lifestyle", {}).get("sleep_quality", "?")
    mix_name = mix.get("mix_name", "")
    mix_trigger = mix.get("primary_trigger", "")
    sg_decision = rule_outputs.get("softgel", {})
    sg_needs = sg_decision.get("needs_identified", [])
    
    # 1. Probiotics (from calc.probiotic_components)
    lp815_added = mix.get("lp815_added", False)
    non_lp815 = [p for p in calc.probiotic_components if "LP815" not in p.get("substance", "")]
    if non_lp815:
        # Use actual per-strain CFU from data (never use a default fallback for display)
        per_strain_cfu = non_lp815[0].get("cfu_billions", 0) if non_lp815 else 0
        total_base_cfu = sum(p.get("cfu_billions", 0) for p in non_lp815)
        # Build dose string: "50B CFU (12.5B each)" — explicit, no reverse-engineering downstream
        dose_str = f"{total_base_cfu}B CFU ({per_strain_cfu}B each)" if per_strain_cfu else f"{total_base_cfu}B CFU"
        registry.append({
            "substance": f"{len(non_lp815)} base strains ({mix_name})",
            "dose": dose_str,
            "delivery": "probiotic capsule",
            "category": "probiotic",
            "source": "microbiome_primary",
            "health_claims": [mix_name, mix_trigger.split("(")[0].strip() if "(" in mix_trigger else mix_trigger],
            "based_on": f"Microbiome analysis ({mix_trigger})",
            "what_it_targets": mix.get("mix_name", "Gut microbiome optimization"),
            "informed_by": "microbiome",
        })
    
    # LP815 separately — label is context-aware:
    # Mix 7 (clinician-directed Psychobiotic) → "psychobiotic strain"
    # All other mixes → "gut-brain enhancement strain" (GABA/stress support add-on)
    lp815_strains = [p for p in calc.probiotic_components if "LP815" in p.get("substance", "")]
    mix_id = mix.get("mix_id")
    lp815_label = "LP815 psychobiotic strain" if mix_id == 7 else "LP815 gut-brain enhancement strain"
    if lp815_strains:
        registry.append({
            "substance": f"{lp815_label} (5B CFU)",
            "dose": "5B CFU",
            "delivery": "probiotic capsule",
            "category": "probiotic",
            "source": "microbiome_linked",
            "health_claims": ["Stress/Anxiety", "Sleep Quality", "Gut-Brain"],
            "based_on": f"Microbiome gut-brain pattern + stress {stress}/10",
            "what_it_targets": "Stress, anxiety, mood, sleep (produces calming GABA)",
            "informed_by": "microbiome + questionnaire",
        })
    
    # 2. Softgels (from calc — fixed composition if present)
    if calc.softgel_count > 0:
        # Omega-3 — only microbiome_linked if actual microbiome gut-brain signal triggered it
        # (not just a questionnaire goal match)
        sg_reasoning = sg_decision.get("reasoning", [])
        omega_has_mb_signal = any("microbiome" in r.lower() or "gut-brain" in r.lower() for r in sg_reasoning)
        vitd_has_mb_signal = any("microbiome" in r.lower() or "restoration" in r.lower() for r in sg_reasoning)
        # Also check microbiome_vitamin_needs for Vit D
        mb_vit_needs = rule_outputs.get("health_claims", {}).get("microbiome_vitamin_needs", [])
        vitd_has_mb_signal = vitd_has_mb_signal or any("D" in n.get("vitamin", "") for n in mb_vit_needs)
        
        registry.append({
            "substance": f"Omega-3 DHA & EPA ({calc.softgel_count} softgels, {712.5 * calc.softgel_count}mg)",
            "dose": f"{712.5 * calc.softgel_count}mg",
            "delivery": "softgel",
            "category": "omega",
            "source": "microbiome_linked" if omega_has_mb_signal else "questionnaire_only",
            "health_claims": ["Brain Health", "Anti-inflammatory"],
            "based_on": f"{'Microbiome gut-brain pattern + ' if omega_has_mb_signal else ''}mood/brain health goals",
            "what_it_targets": "Brain health, mood support, anti-inflammatory",
            "informed_by": "microbiome" if omega_has_mb_signal else "questionnaire",
        })
        registry.append({
            "substance": f"Vitamin D3 ({10 * calc.softgel_count}mcg / {400 * calc.softgel_count} IU)",
            "dose": f"{10 * calc.softgel_count}mcg",
            "delivery": "softgel",
            "category": "vitamin",
            "source": "microbiome_linked" if vitd_has_mb_signal else "questionnaire_only",
            "health_claims": ["Immune System"],
            "based_on": "Microbiome restoration + immune health claim" if vitd_has_mb_signal else "Questionnaire (immune health goal)",
            "what_it_targets": "Immune support, bone health",
            "informed_by": "microbiome" if vitd_has_mb_signal else "questionnaire",
        })
        registry.append({
            "substance": f"Vitamin E ({7.5 * calc.softgel_count}mg)",
            "dose": f"{7.5 * calc.softgel_count}mg",
            "delivery": "softgel",
            "category": "vitamin",
            "source": "questionnaire_only",
            "health_claims": ["Skin Quality", "Antioxidant Protection"] if "vitamin_e" in sg_needs else ["Antioxidant Protection"],
            "based_on": "Skin quality goal" if "vitamin_e" in sg_needs else "General wellness (antioxidant protection, bundled with omega softgel)",
            "what_it_targets": "Antioxidant protection, skin health",
            "informed_by": "questionnaire",
        })
        registry.append({
            "substance": f"Astaxanthin ({3 * calc.softgel_count}mg active)",
            "dose": f"{3 * calc.softgel_count}mg",
            "delivery": "softgel",
            "category": "antioxidant",
            "source": "questionnaire_only",
            "health_claims": ["Sport/Recovery", "Antioxidant Protection"] if "astaxanthin" in sg_needs else ["Antioxidant Protection"],
            "based_on": "Active lifestyle + skin/sport support" if "astaxanthin" in sg_needs else "General wellness (antioxidant protection, bundled with omega softgel)",
            "what_it_targets": "Antioxidant, UV protection, muscle recovery",
            "informed_by": "questionnaire",
        })
    
    # 3. Prebiotics (from calc.sachet_prebiotics — post-dedup)
    for pb in calc.sachet_prebiotics:
        substance = pb["substance"]
        target = _derive_prebiotic_target(substance, mix_name)
        registry.append({
            "substance": f"{substance} ({pb['dose_g']}g)",
            "dose": f"{pb['dose_g']}g",
            "delivery": "sachet",
            "category": "prebiotic",
            "source": "microbiome_primary",
            "health_claims": [f"{mix_name} substrate"],
            "based_on": f"Microbiome pattern ({pb.get('rationale', mix_name + ' requirement')})",
            "what_it_targets": target,
            "informed_by": "microbiome",
        })
    
    # 4. Vitamins/Minerals (from calc.sachet_vitamins — post-dedup)
    for vm in calc.sachet_vitamins:
        rationale = vm.get("rationale", "")
        claims = _extract_claims_from_rationale(rationale, goals)
        claims_list = [c.strip() for c in claims.split(",")] if claims else []
        informed = vm.get("informed_by", "questionnaire")
        if informed == "microbiome":
            source = "microbiome_primary"
        elif informed == "both":
            source = "microbiome_linked"
        else:
            source = "questionnaire_only"
        registry.append({
            "substance": f"{vm['substance']} ({vm['dose']})",
            "dose": vm["dose"],
            "delivery": "sachet",
            "category": "vitamin_mineral",
            "source": source,
            "health_claims": claims_list,
            "based_on": f"{'Microbiome + ' if informed in ('microbiome','both') else ''}Questionnaire ({claims})" if claims else "Health questionnaire",
            "what_it_targets": rationale or "General wellness",
            "informed_by": informed,
        })
    
    # 5. Supplements (from calc.sachet_supplements — post-dedup, post-trim)
    # IMPORTANT: calc.sachet_supplements is an alias for morning_pooled_components
    # (same list as calc.sachet_vitamins used in section 4 above).
    # We must SKIP items with _source == "vitamin_mineral" — those were already
    # processed by section 4 with correct mcg/mg dose formatting.
    # Without this filter, mcg-unit vitamins appear twice:
    #   Section 4: "Selenium (40mcg)" ← correct
    #   Section 5: "Selenium (0.0mg)" ← wrong (weight_mg is 0 for mcg items)
    supp_claims_map = {}
    for sp in supplements.get("supplements", []):
        supp_claims_map[sp.get("substance", "").lower()] = sp.get("health_claim", "")
    
    for sp in calc.sachet_supplements:
        # Skip vitamin/mineral items — already registered by section 4 above
        if sp.get("_source") == "vitamin_mineral":
            continue
        substance = sp["substance"]
        substance_lower = substance.lower()
        claim = supp_claims_map.get(substance_lower, "")
        rationale = sp.get("rationale", "")
        is_fiber = any(f in substance_lower for f in ["phgg", "fiber", "inulin", "fos", "gos", "pectin", "psyllium", "beta-glucan"])
        
        if is_fiber:
            source = "microbiome_linked"
            based_on = f"Microbiome pattern + questionnaire ({claim})" if claim else "Microbiome pattern (prebiotic fiber)"
        else:
            source = "questionnaire_only"
            based_on = f"Questionnaire ({claim})" if claim else "Health questionnaire"
        
        claims_list = [claim] if claim else []
        if not claims_list and rationale:
            rc = _extract_claims_from_rationale(rationale, goals)
            claims_list = [c.strip() for c in rc.split(",")] if rc else []
        
        registry.append({
            "substance": f"{substance} ({sp['weight_mg']}mg)",
            "dose": f"{sp['weight_mg']}mg",
            "delivery": "morning wellness capsule",  # v3: light botanicals live in morning capsule, not sachet
            "category": "supplement",
            "source": source,
            "health_claims": claims_list,
            "based_on": based_on,
            "what_it_targets": rationale or claim or "General wellness",
            "informed_by": "microbiome" if is_fiber else "questionnaire",
        })
    
    # 6. Evening capsule components (from calc.evening_components + evening_capsule_2)
    _all_evening_for_registry = list(calc.evening_components) + list(getattr(calc, 'evening_capsule_2', []))
    for ec in _all_evening_for_registry:
        registry.append({
            "substance": f"{ec['substance']} ({ec['dose_mg']}mg)",
            "dose": f"{ec['dose_mg']}mg",
            "delivery": "evening capsule",
            "category": "sleep_supplement",
            "source": "questionnaire_only",
            "health_claims": ["Sleep Quality"],
            "based_on": f"Questionnaire (sleep quality {sleep}/10)",
            "what_it_targets": ec.get("rationale", "Sleep/relaxation support"),
            "informed_by": "questionnaire",
        })
    
    # 7. Magnesium capsules
    mg = rule_outputs.get("magnesium", {})
    if mg.get("capsules", 0) > 0:
        mg_needs = mg.get("needs_identified", [])
        registry.append({
            "substance": f"Magnesium Bisglycinate ({mg['mg_bisglycinate_total_mg']}mg / {mg['elemental_mg_total_mg']}mg elemental)",
            "dose": f"{mg['elemental_mg_total_mg']}mg elemental",
            "delivery": "magnesium capsule",
            "category": "mineral",
            "source": "questionnaire_only",
            "health_claims": [n.title() for n in mg_needs],
            "based_on": f"Questionnaire ({'; '.join(mg.get('reasoning', []))})",
            "what_it_targets": _derive_mg_target(mg_needs),
            "informed_by": "questionnaire",
        })
    
    # 8. Polyphenol capsule components (Tier 2 — Curcumin+Piperine, Bergamot)
    for pc in calc.polyphenol_capsules:
        substance = pc["substance"]
        claim = supp_claims_map.get(substance.lower(), "")
        if not claim:
            # Try partial match for renamed substances (e.g., "Curcumin 500mg (+ 5.0mg Piperine)")
            for k, v in supp_claims_map.items():
                if "curcumin" in k and "curcumin" in substance.lower():
                    claim = v
                    break
                elif "bergamot" in k and "bergamot" in substance.lower():
                    claim = v
                    break
        claims_list = [claim] if claim else ["Anti-inflammatory"]
        registry.append({
            "substance": f"{substance} ({pc['dose_mg']}mg)",
            "dose": f"{pc['dose_mg']}mg",
            "delivery": "polyphenol capsule",
            "category": "polyphenol",
            "source": "questionnaire_only",
            "health_claims": claims_list,
            "based_on": f"Questionnaire ({claim})" if claim else "Health questionnaire (anti-inflammatory support)",
            "what_it_targets": pc.get("rationale", claim or "Anti-inflammatory, microbiome modulation"),
            "informed_by": "questionnaire",
        })
    
    # ── Dedup registry by substance name ─────────────────────────────────
    # vitamins/minerals can appear twice: once from sachet_vitamins (section 4)
    # and once from sachet_supplements (section 5) because in v3 both aliases
    # point to morning_pooled_components. Keep first occurrence only.
    seen_substances = set()
    deduped_registry = []
    for entry in registry:
        key = entry.get("substance", "").lower()
        if key not in seen_substances:
            seen_substances.add(key)
            deduped_registry.append(entry)
    registry = deduped_registry

    return registry


# ─── SUPPLEMENT KB LOOKUP HELPERS ─────────────────────────────────────────────

def _load_supplement_kb_lookup() -> Dict:
    """Build normalized lookup from supplements_nonvitamins.json for dose + timing info.
    
    Returns dict: {normalized_name: {min_dose_mg, timing_restriction, rank_priority, health_claim}}
    Normalized name = lowercase, stripped of parenthetical qualifiers.
    """
    import re as _re
    kb_path = KB_DIR / "supplements_nonvitamins.json"
    with open(kb_path, 'r', encoding='utf-8') as f:
        kb = json.load(f)
    
    lookup = {}
    for entry in kb.get("supplements_flat", []):
        substance = entry.get("substance", "")
        parsed = entry.get("parsed", {})
        dose = parsed.get("dose", {})
        
        # Calculate min dose in mg
        min_dose_mg = None
        if "min" in dose:
            unit = dose.get("unit", "mg")
            if unit == "g":
                min_dose_mg = dose["min"] * 1000
            else:
                min_dose_mg = dose["min"]
        elif "value" in dose:
            unit = dose.get("unit", "mg")
            if unit == "g":
                min_dose_mg = dose["value"] * 1000
            else:
                min_dose_mg = dose["value"]
        
        timing = entry.get("timing_restriction", "any")
        rank = parsed.get("rank_priority", 3)
        claims = parsed.get("health_claims", [])
        
        # Build multiple name variants for fuzzy matching
        names = set()
        # Full name
        names.add(substance.lower().strip())
        # Without parenthetical (e.g., "Foeniculum Vulgare (Fennel)" → "foeniculum vulgare")
        base = _re.sub(r'\s*\(.*?\)\s*', '', substance).strip().lower()
        if base:
            names.add(base)
        # The parenthetical itself (e.g., "Fennel")
        paren = _re.search(r'\(([^)]+)\)', substance)
        if paren:
            names.add(paren.group(1).strip().lower())
        # ID as fallback
        names.add(entry.get("id", "").lower())
        
        delivery_constraint = entry.get("delivery_constraint", "any")
        
        info = {
            "min_dose_mg": min_dose_mg,
            "timing_restriction": timing,
            "rank_priority": rank,
            "health_claims": claims,
            "substance_full": substance,
            "delivery_constraint": delivery_constraint,
        }
        for n in names:
            if n:
                lookup[n] = info
    
    return lookup


def _resolve_sachet_overflow(calc, supplements_data: Dict, capacity_g: float = SACHET_CAPACITY_G) -> set:
    """Smart sachet capacity resolution — 4-step algorithm.
    
    Instead of blindly dropping the heaviest supplement, this function:
      Step 1: Reduce doses to KB minimums
      Step 2: Reroute compatible supplements to evening capsule
      Step 3: Drop redundant supplements (same health claim, lower rank)
      Step 4: Alert if still over capacity
    
    Args:
        calc: FormulationCalculator instance (modified in place)
        supplements_data: Original supplement selection dict (for health_claim lookup)
        capacity_g: Sachet capacity in grams (default 19g)
    
    Returns:
        set of lowercased substance names that were trimmed/rerouted (for presence check)
    """
    from weight_calculator import EVENING_CAPSULE_CAPACITY_MG
    
    kb_lookup = _load_supplement_kb_lookup()
    capacity_trimmed_names = set()
    
    def _current_sachet_g():
        pb_g = sum(c["weight_g"] for c in calc.sachet_prebiotics)
        vm_g = sum(c["weight_mg"] for c in calc.sachet_vitamins) / 1000
        sp_g = sum(c["weight_mg"] for c in calc.sachet_supplements) / 1000
        return pb_g + vm_g + sp_g
    
    def _find_kb_entry(substance_name):
        """Fuzzy match substance name against KB lookup."""
        name_lower = substance_name.lower().strip()
        # Direct match
        if name_lower in kb_lookup:
            return kb_lookup[name_lower]
        # Partial match — check if any KB key is contained in the name or vice versa
        for kb_key, kb_val in kb_lookup.items():
            if kb_key in name_lower or name_lower in kb_key:
                return kb_val
        return None
    
    # Build health_claim map from original supplement data
    supp_claims = {}
    for sp in supplements_data.get("supplements", []):
        supp_claims[sp.get("substance", "").lower()] = sp.get("health_claim", "")
    
    total_g = _current_sachet_g()
    if total_g <= capacity_g:
        return capacity_trimmed_names  # No overflow
    
    print(f"  ⚠️ SACHET OVERFLOW: {total_g:.1f}g > {capacity_g}g — initiating smart resolution...")
    
    # ── STEP 1: Reduce doses to KB minimums ──────────────────────────────
    print(f"    Step 1: Reducing supplement doses to KB minimums...")
    step1_saved = 0
    for supp in calc.sachet_supplements:
        kb_entry = _find_kb_entry(supp["substance"])
        if kb_entry and kb_entry["min_dose_mg"] is not None:
            current_mg = supp["weight_mg"]
            min_mg = kb_entry["min_dose_mg"]
            if current_mg > min_mg:
                saved = current_mg - min_mg
                step1_saved += saved
                print(f"      → {supp['substance']}: {current_mg}mg → {min_mg}mg (saved {saved}mg)")
                supp["weight_mg"] = min_mg
                supp["dose_mg"] = min_mg
            else:
                print(f"      · {supp['substance']}: already at/below min ({current_mg}mg ≤ {min_mg}mg)")
        else:
            print(f"      · {supp['substance']}: no KB min dose found — keeping {supp['weight_mg']}mg")
    
    if step1_saved > 0:
        print(f"    Step 1 result: saved {step1_saved}mg ({step1_saved/1000:.1f}g)")
    
    total_g = _current_sachet_g()
    if total_g <= capacity_g:
        print(f"    ✅ Resolved at Step 1: {total_g:.1f}g ≤ {capacity_g}g")
        return capacity_trimmed_names
    
    # ── STEP 2: Reroute compatible supplements to evening capsule ────────
    print(f"    Step 2: Checking evening capsule rerouting (sachet still {total_g:.1f}g)...")
    existing_evening_mg = sum(c.get("dose_mg", 0) for c in calc.evening_components)
    evening_headroom = EVENING_CAPSULE_CAPACITY_MG - existing_evening_mg
    
    # Check what's already in evening (for conflict detection)
    evening_has_stimulant = any(
        _find_kb_entry(c["substance"]) and _find_kb_entry(c["substance"])["timing_restriction"] == "morning_only"
        for c in calc.evening_components
    )
    evening_has_calming = any(
        _find_kb_entry(c["substance"]) and _find_kb_entry(c["substance"])["timing_restriction"] == "evening_ok"
        for c in calc.evening_components
    )
    
    rerouted = []
    remaining_supps = []
    for supp in calc.sachet_supplements:
        if total_g <= capacity_g:
            remaining_supps.append(supp)
            continue
        
        kb_entry = _find_kb_entry(supp["substance"])
        timing = kb_entry["timing_restriction"] if kb_entry else "any"
        dose_mg = supp["weight_mg"]
        
        # Check all reroute conditions
        can_reroute = True
        reason_no = ""
        
        if dose_mg > evening_headroom:
            can_reroute = False
            reason_no = f"exceeds evening headroom ({dose_mg}mg > {evening_headroom}mg)"
        elif timing == "morning_only":
            can_reroute = False
            reason_no = f"stimulant — morning only"
        elif timing == "morning_only" and evening_has_calming:
            can_reroute = False
            reason_no = "stimulant conflicts with calming agents in evening"
        
        if can_reroute:
            calc.add_evening_component(supp["substance"], dose_mg, rationale=supp.get("rationale", ""))
            evening_headroom -= dose_mg
            rerouted.append(supp)
            # NOTE: rerouted items are NOT added to capacity_trimmed_names — they ARE in the formulation (evening capsule)
            # Track weight reduction manually (calc.sachet_supplements not updated yet during iteration)
            total_g -= dose_mg / 1000
            print(f"      → REROUTED: {supp['substance']} ({dose_mg}mg) → evening capsule (timing={timing})")
        else:
            remaining_supps.append(supp)
            print(f"      · Cannot reroute {supp['substance']}: {reason_no}")
    
    calc.sachet_supplements = remaining_supps
    total_g = _current_sachet_g()  # Recalculate from actual state for accuracy
    
    if total_g <= capacity_g:
        print(f"    ✅ Resolved at Step 2: {total_g:.1f}g ≤ {capacity_g}g (rerouted {len(rerouted)} supplement(s))")
        return capacity_trimmed_names
    
    # ── STEP 3: Redundancy-based drop ────────────────────────────────────
    print(f"    Step 3: Checking redundancy-based drops (sachet still {total_g:.1f}g)...")
    
    # Group sachet supplements by health claim
    claim_groups = {}
    for i, supp in enumerate(calc.sachet_supplements):
        claim = supp_claims.get(supp["substance"].lower(), "")
        if claim:
            claim_groups.setdefault(claim, []).append((i, supp))
    
    # Drop lowest-ranked supplement from claims with 2+ supplements
    indices_to_drop = set()
    for claim, group in claim_groups.items():
        if len(group) >= 2 and total_g > capacity_g:
            # Sort by rank (higher rank number = lower priority = drop first)
            group_with_rank = []
            for idx, supp in group:
                kb_entry = _find_kb_entry(supp["substance"])
                rank = kb_entry["rank_priority"] if kb_entry else 3
                group_with_rank.append((rank, idx, supp))
            group_with_rank.sort(key=lambda x: -x[0])  # Highest rank number first (3rd choice)
            
            # Drop the lowest-ranked one
            drop_rank, drop_idx, drop_supp = group_with_rank[0]
            indices_to_drop.add(drop_idx)
            capacity_trimmed_names.add(drop_supp["substance"].lower())
            total_g -= drop_supp["weight_mg"] / 1000
            print(f"      → DROPPED (redundant): {drop_supp['substance']} ({drop_supp['weight_mg']}mg) — "
                  f"claim '{claim}' covered by {len(group)-1} other supplement(s)")
    
    if not indices_to_drop and total_g > capacity_g:
        # No redundancy found — drop lowest-priority supplement overall
        all_with_rank = []
        for i, supp in enumerate(calc.sachet_supplements):
            if i not in indices_to_drop:
                kb_entry = _find_kb_entry(supp["substance"])
                rank = kb_entry["rank_priority"] if kb_entry else 3
                all_with_rank.append((rank, i, supp))
        
        if all_with_rank:
            all_with_rank.sort(key=lambda x: -x[0])  # Highest rank number first
            drop_rank, drop_idx, drop_supp = all_with_rank[0]
            indices_to_drop.add(drop_idx)
            capacity_trimmed_names.add(drop_supp["substance"].lower())
            total_g -= drop_supp["weight_mg"] / 1000
            print(f"      → DROPPED (lowest priority): {drop_supp['substance']} ({drop_supp['weight_mg']}mg) — rank {drop_rank}")
    
    # Remove dropped supplements and recalculate from actual state
    if indices_to_drop:
        calc.sachet_supplements = [s for i, s in enumerate(calc.sachet_supplements) if i not in indices_to_drop]
        calc.warnings.append(f"Smart sachet resolution: dropped {len(indices_to_drop)} supplement(s) for capacity")
    
    total_g = _current_sachet_g()  # Recalculate from actual state (avoids drift from manual tracking)
    if total_g <= capacity_g:
        print(f"    ✅ Resolved at Step 3: {total_g:.1f}g ≤ {capacity_g}g")
        return capacity_trimmed_names
    
    # ── STEP 4: Still over — continue dropping until resolved or empty ───
    print(f"    Step 4: Still over capacity ({total_g:.1f}g) — dropping remaining supplements by priority...")
    while total_g > capacity_g and calc.sachet_supplements:
        # Find lowest-priority supplement (ties broken by weight — drop lighter first to preserve heavier)
        worst_idx = 0
        worst_rank = 0
        worst_weight = float('inf')
        for i, supp in enumerate(calc.sachet_supplements):
            kb_entry = _find_kb_entry(supp["substance"])
            rank = kb_entry["rank_priority"] if kb_entry else 3
            weight = supp["weight_mg"]
            if rank > worst_rank or (rank == worst_rank and weight < worst_weight):
                worst_rank = rank
                worst_weight = weight
                worst_idx = i
        
        dropped = calc.sachet_supplements.pop(worst_idx)
        capacity_trimmed_names.add(dropped["substance"].lower())
        total_g = _current_sachet_g()
        print(f"      → DROPPED: {dropped['substance']} ({dropped['weight_mg']}mg) — rank {worst_rank}")
    
    total_g = _current_sachet_g()
    if total_g > capacity_g:
        print(f"    🚨🚨🚨 SACHET OVERFLOW: Cannot fit within {capacity_g}g even after all optimization! "
              f"Current: {total_g:.1f}g (prebiotics: {sum(c['weight_g'] for c in calc.sachet_prebiotics):.1f}g)")
        calc.warnings.append(f"CRITICAL: Sachet overflow at {total_g:.1f}g even after smart resolution")
    else:
        print(f"    ✅ Resolved at Step 4: {total_g:.1f}g ≤ {capacity_g}g")
    
    return capacity_trimmed_names


def generate_formulation(
    sample_dir: str,
    use_llm: bool = True,
    copy_to_sample: bool = True,
    force_keep: bool = False,
    compact: bool = False,
    _skip_evening_redirect: bool = False,
) -> Dict:
    """
    Generate complete formulation for a sample.

    Args:
        sample_dir: Path to sample directory
        use_llm: Whether to use Bedrock LLM (False for offline testing)
        copy_to_sample: Whether to copy output to sample's supplement_formulation dir
        force_keep: If True, high-severity interactions are flagged but NOT auto-removed
        compact: If True, suppress all pipeline detail — only show formulation summary + sanity check
        _skip_evening_redirect: Internal flag — prevents infinite recursion when called
            from generate_formulation_evening.py. Never set this manually.

    Returns:
        Master formulation JSON dict
    """
    # Force line-buffered stdout so output streams in real time
    # (Python switches to full buffering when piped through tee/scripts)
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass  # Python < 3.7 fallback — ignore

    # ── Start TeeWriter to capture full stdout for pipeline log ──────────
    _tee = TeeWriter()
    _tee.start()

    # ── Compact mode: suppress verbose output until summary ──────────────
    # Redirects stdout to devnull for stages A-F, restores for summary + sanity check
    _real_stdout = sys.stdout
    if compact:
        sys.stdout = open(os.devnull, 'w')

    sample_dir = Path(sample_dir)
    sample_id = sample_dir.name

    # ── Decision trace event collector ───────────────────────────────────
    # Lightweight list that captures "because X → we did Y" events as they
    # happen throughout the pipeline. The trace printer at the end assembles
    # them into a readable decision audit.
    _trace_events = []

    print(f"\n{'═'*60}")
    print(f"  FORMULATION PIPELINE — {sample_id}")
    print(f"  Mode: {'LLM (Bedrock)' if use_llm else 'OFFLINE (no LLM)'}")
    if force_keep:
        print(f"  ⚠️ --force-keep active: high-severity interactions will NOT be auto-removed")
    if compact:
        print(f"  📋 Compact mode — detail suppressed")
    print(f"{'═'*60}\n")

    # ── STAGE A: Parse Inputs ────────────────────────────────────────────
    print("─── A. INPUTS ──────────────────────────────────────────────")
    unified_input = parse_inputs(str(sample_dir))
    guilds = unified_input['microbiome']['guilds']
    clr = unified_input['microbiome']['clr_ratios']
    q = unified_input['questionnaire']

    # ── QUESTIONNAIRE VALIDATION GUARD ───────────────────────────────────
    q_completion = q.get('completion', {}).get('completion_pct', 0)
    if q_completion == 0:
        print(f"\n{'='*60}")
        print(f"  ⚠️  QUESTIONNAIRE REQUIRED")
        print(f"{'='*60}")
        print(f"  Sample: {sample_id}")
        print(f"  Status: No questionnaire data available (0% completion)")
        print(f"")
        print(f"  This sample cannot be processed without a completed questionnaire.")
        print(f"  The formulation pipeline requires questionnaire data to:")
        print(f"    • Determine sensitivity classification")
        print(f"    • Select appropriate supplements")
        print(f"    • Calculate personalized doses")
        print(f"    • Route components by timing (morning/evening)")
        print(f"")
        print(f"  Action required: Complete questionnaire for sample {sample_id}")
        print(f"{'='*60}\n")
        return None  # Exit gracefully without crashing

    # ── MICROBIOME DATA MISSING GUARD ────────────────────────────────────
    if not guilds:
        print(f"\n  🚨🚨🚨 WARNING: NO MICROBIOME GUILD DATA for {sample_id}")
        print(f"  🚨 Mix selection will DEFAULT to Mix 6 (Maintenance) — this is likely WRONG.")
        print(f"  🚨 Ensure microbiome analysis (generate_report.py) has been run BEFORE formulation.")
        print(f"  🚨 Expected file: {sample_dir}/reports/reports_json/microbiome_analysis_master_{sample_id}.json")
        print(f"  🚨 The formulation will proceed but should be REVIEWED before production.\n")

    print(f"  Sample: {unified_input['sample_id']} | Batch: {unified_input['batch_id']} | Q: {q['completion']['completion_pct']:.0f}%")
    # Guild icons: priority-level-based coloring
    _PRIO_ICON = {"CRITICAL": "🔴", "1A": "🟠", "1B": "🟡", "Monitor": "🟢"}
    def _guild_icon(key, prio_level):
        return _PRIO_ICON.get(prio_level, "⚪")

    _guild_parts = []
    for gk, gv in guilds.items():
        icon = _guild_icon(gk, gv.get("priority_level") or "Monitor")
        pct = gv.get("abundance_pct", 0)
        name = gv.get("name", gk)
        prio = gv.get("priority_level") or "Monitor"
        _guild_parts.append(f"{icon} {name} {pct:.1f}%{' [' + prio + ']' if prio not in ('Monitor',) else ''}")
    print(f"  Guilds: {' · '.join(_guild_parts)}")
    print(f"  CLR: CUR={clr.get('CUR')}  FCR={clr.get('FCR')}  MDR={clr.get('MDR')}  PPR={clr.get('PPR')}")
    _sleep_val = q['lifestyle'].get('sleep_quality')
    _goals_short = ' · '.join(g.replace('_', ' ') for g in q['goals'].get('ranked', []))
    print(f"  Client: {_goals_short} | stress {q['lifestyle'].get('stress_level')}/10 | sleep {_sleep_val}/10 ({_sleep_label(_sleep_val)}) | bloating {q['digestive'].get('bloating_severity') or 'n/a'}/10")

    # ── STAGE A.5: LLM Questionnaire Clinical Analysis ───────────────────
    # Runs BEFORE rules engine so inferred health signals feed into supplement selection.
    # Produces: profile_narrative, inferred_health_signals, clinical_review_flags
    print("\n─── A.5 CLINICAL PROFILE ───────────────────────────────────")
    clinical_summary = {"profile_narrative": [], "inferred_health_signals": [], "clinical_review_flags": []}
    if use_llm:
        try:
            from llm_decisions import analyze_questionnaire_clinical
            print("  🧠 LLM: Analysing clinical questionnaire profile...")
            clinical_summary = analyze_questionnaire_clinical(unified_input, use_bedrock=use_llm)
        except Exception as _cqe:
            print(f"  ⚠️ Clinical questionnaire analysis failed: {_cqe}")

    # Print profile narrative
    if clinical_summary.get("profile_narrative"):
        print(f"  CLIENT PROFILE:")
        for _bullet in clinical_summary["profile_narrative"]:
            print(f"    {_bullet}")

    # Print inferred signals — support both old (str) and new ({signal, reason}) format
    _inferred = clinical_summary.get("inferred_health_signals", [])
    if _inferred:
        _inferred_display = [s.get("signal", s) if isinstance(s, dict) else s for s in _inferred]
        print(f"  Inferred health signals: {_inferred_display}")

    # Print clinical review flags — big box, hard to miss
    _flags = clinical_summary.get("clinical_review_flags", [])
    if _flags:
        _flag_w = 68
        print(f"\n  ┌─ 🚨 CLINICAL REVIEW REQUIRED {'─' * (_flag_w - 33)}")
        for _flag in _flags:
            _sev = _flag.get("severity", "medium").upper()
            _icon = "🔴" if _sev == "HIGH" else ("🟡" if _sev == "MEDIUM" else "🔵")
            print(f"  │ {_icon} [{_sev}] {_flag.get('title', '?')}")
            # Word-wrap detail
            _detail = _flag.get("detail", "")
            import textwrap as _tw
            for _dline in _tw.wrap(_detail, width=62):
                print(f"  │       {_dline}")
            print(f"  │")
        print(f"  └{'─' * (_flag_w + 2)}\n")

    # ── STAGE A.5b: LLM Medication Interaction Screening ─────────────────
    # Scans ALL client medications against the full supplement/vitamin KB.
    # Returns a definitive exclusion set enforced at every pipeline stage.
    medication_exclusion_set = set()  # lowercased substance names
    medication_exclusion_reasons = []
    if use_llm:
        try:
            from llm_decisions import screen_medication_interactions
            print("\n─── A.5b MEDICATION SCREENING ──────────────────────────")
            print("  🧠 LLM: Screening medications against supplement database...")
            _med_screen = screen_medication_interactions(unified_input, use_bedrock=use_llm)
            medication_exclusion_set = _med_screen.get("excluded_substances", set())
            medication_exclusion_reasons = _med_screen.get("exclusion_reasons", [])
            if _med_screen.get("skipped"):
                print("  ⚠️ Medication screening skipped (LLM unavailable)")
            elif medication_exclusion_set:
                print(f"  🚫 EXCLUDED (medication interaction):")
                for _reason in medication_exclusion_reasons:
                    print(f"    → {_reason.get('substance', '?')} — {_reason.get('medication', '?')}: {_reason.get('mechanism', '?')}")
                print(f"  ✅ {len(medication_exclusion_set)} substance(s) excluded — enforced at all pipeline stages")
            else:
                print("  ✅ No high-severity medication interactions found")
        except Exception as _med_e:
            print(f"  ⚠️ Medication screening failed: {_med_e}")

    # ── STAGE A.6: Deterministic Medication Rules (KB-driven) ────────────
    # Fires BEFORE all LLM decisions. Rules from medication_interactions.json
    # are clinician-reviewed and reproducible. They produce:
    #   - timing_override (Tier A): move all units to dinner (e.g., Levothyroxine)
    #   - substance removal (Tier B): remove magnesium (e.g., Ramipril)
    #   - clinical flags (all tiers): shown in pipeline log + decision trace
    # Unmatched medications fall through to Elicit/LLM evidence retrieval (Tier C only).
    from rules_engine import apply_medication_rules
    print("\n─── A.6 DETERMINISTIC MEDICATION RULES (KB) ────────────────")
    med_rules_result = apply_medication_rules(unified_input)
    _med_timing_override = med_rules_result.get("timing_override")
    _med_substances_to_remove = med_rules_result.get("substances_to_remove", set())
    _med_magnesium_removed = med_rules_result.get("magnesium_removed", False)
    _med_clinical_flags = med_rules_result.get("clinical_flags", [])
    _med_unmatched = med_rules_result.get("unmatched_medications", [])

    if med_rules_result.get("matched_rules"):
        for _mr in med_rules_result["matched_rules"]:
            _rule = _mr["rule"]
            _tier_icon = {"A": "🔴", "B": "🟡", "C": "🔵"}.get(_rule.get("tier", "C"), "⚪")
            print(f"  {_tier_icon} MATCHED [{_rule.get('tier')}] {_rule.get('rule_id')}: {_mr['medication_raw']} → {_rule.get('medication')}")
            print(f"    Class: {_rule.get('interaction_class')} | Severity: {_rule.get('severity')}")

        if _med_timing_override:
            _to = _med_timing_override.get("move_to", "dinner")
            print(f"  ⏰ TIMING OVERRIDE: ALL units moved to {_to.upper()}")
            print(f"    Reason: {_med_timing_override.get('reason', '')}")
            print(f"    Clinical note: {_med_timing_override.get('clinical_note', '')}")

        if _med_substances_to_remove:
            print(f"  🚫 SUBSTANCES TO REMOVE: {_med_substances_to_remove}")
            for _rr in med_rules_result.get("removal_reasons", []):
                print(f"    → {_rr['substance']}: {_rr.get('mechanism', '')} ({_rr.get('reason', '')})")

        if _med_magnesium_removed:
            print(f"  💊 Magnesium capsule SUPPRESSED (medication interaction rule)")
    else:
        print(f"  ✅ No KB medication rules matched")

    if _med_unmatched:
        _unmatched_names = [m.get("name", "?") for m in _med_unmatched]
        print(f"  📋 Unmatched medications (will query Elicit/LLM): {_unmatched_names}")

    # Print clinical flags (all tiers)
    if _med_clinical_flags:
        _flag_w = 68
        print(f"\n  ┌─ 💊 MEDICATION INTERACTION FLAGS {'─' * (_flag_w - 36)}")
        for _cf in _med_clinical_flags:
            _cf_icon = {"A": "🔴", "B": "🟡", "C": "🔵"}.get(_cf.get("tier", "C"), "⚪")
            _cf_exec = " [AUTO-EXECUTED]" if _cf.get("auto_executed") else " [PENDING CLINICIAN REVIEW]"
            print(f"  │ {_cf_icon} [{_cf.get('tier')}] {_cf.get('title', '?')}{_cf_exec}")
            import textwrap as _tw_med
            for _dline in _tw_med.wrap(_cf.get("detail", ""), width=62):
                print(f"  │       {_dline}")
            print(f"  │")
        print(f"  └{'─' * (_flag_w + 2)}\n")

    # Merge KB-driven exclusions into the medication exclusion set
    # (these override anything the LLM might decide differently)
    for _kb_substance in _med_substances_to_remove:
        medication_exclusion_set.add(_kb_substance)

    # ── EVENING PIPELINE REDIRECT ────────────────────────────────────────
    # If a Tier A timing override is active (e.g., MED_001 for levothyroxine),
    # delegate to the evening pipeline which produces identical JSON but with
    # corrected display labels (dinner instead of morning) throughout.
    # This avoids the display-before-override timing gap where the standard
    # pipeline prints "Morning cap" in the delivery box before Stage 8.5
    # rewrites timings in the master JSON.
    if _med_timing_override and not _skip_evening_redirect:
        # Stop TeeWriter — evening pipeline manages its own output
        _tee_partial = _tee.stop()
        # Restore real stdout if compact mode redirected to devnull
        if compact:
            try:
                sys.stdout.close()
            except Exception:
                pass
            sys.stdout = _real_stdout
        # Print what we've captured so far (A → A.6) so the user sees early stages
        print(_tee_partial, end='')
        print(f"\n  ⏰ Timing override detected → delegating to evening pipeline...\n")
        from generate_formulation_evening import generate_formulation as _gen_evening
        return _gen_evening(
            str(sample_dir),
            use_llm=use_llm,
            copy_to_sample=copy_to_sample,
            force_keep=force_keep,
            compact=compact,
        )

    # ── A.6b: Elicit/LLM evidence retrieval for unmatched medications ────
    _elicit_evidence_result = {"evidence_flags": [], "evidence_objects": [], "source": "skipped"}
    if _med_unmatched and use_llm:
        try:
            from llm_decisions import retrieve_medication_evidence
            print(f"\n─── A.6b EVIDENCE RETRIEVAL (unmatched medications) ─────────")
            # Build list of supplement names currently selected (for cross-reference)
            # At this stage we don't have LLM selections yet, so use an empty list
            # The evidence retrieval will flag based on pharmacological properties
            _elicit_evidence_result = retrieve_medication_evidence(
                medication_entries=_med_unmatched,
                selected_supplements=[],  # Will be re-checked post-LLM if needed
                use_bedrock=use_llm,
            )
            _elicit_flags = _elicit_evidence_result.get("evidence_flags", [])
            if _elicit_flags:
                print(f"  ⚠️ EXTERNAL EVIDENCE — {len(_elicit_flags)} flag(s) generated:")
                for _ef in _elicit_flags:
                    _ef_icon = "🔵"  # Always Tier C
                    print(f"    {_ef_icon} [{_ef.get('severity', 'low').upper()}] {_ef.get('title', '?')}")
                    if _ef.get("detail"):
                        print(f"       {_ef['detail'][:100]}")
                    print(f"       Status: {_ef.get('review_status', 'PENDING_CLINICIAN')}")
                print(f"  ⚡ External evidence flags are Tier C ONLY — NO formulation changes applied")
                print(f"  ⚡ To promote to deterministic rule: add to medication_interactions.json")
            else:
                print(f"  ✅ No evidence flags generated for unmatched medications")
        except Exception as _elicit_err:
            print(f"  ⚠️ Evidence retrieval failed: {_elicit_err}")

    # Helper: check if a substance is medication-excluded (fuzzy match)
    def _is_medication_excluded(substance_name: str) -> bool:
        """Check if a substance is in the medication exclusion set (fuzzy match)."""
        if not medication_exclusion_set:
            return False
        name_lower = substance_name.lower().strip()
        for excluded in medication_exclusion_set:
            if excluded in name_lower or name_lower in excluded:
                return True
        return False

    # ── STAGE B: Deterministic Rules ─────────────────────────────────────
    print("\n─── B. RULES ───────────────────────────────────────────────")
    rule_outputs = apply_rules(unified_input)

    sens = rule_outputs['sensitivity']
    hc = rule_outputs['health_claims']
    ther = rule_outputs['therapeutic_triggers']
    mg = rule_outputs['magnesium']
    tm = rule_outputs['timing']

    # ── Merge inferred health signals from clinical analysis into supplement claims ──
    # Supports both old (plain string) and new ({signal, reason}) formats
    _existing_claims = set(hc.get("supplement_claims", []))
    _inferred_to_merge = []
    for _sig in clinical_summary.get("inferred_health_signals", []):
        _sig_str = _sig.get("signal", _sig) if isinstance(_sig, dict) else _sig
        if _sig_str and _sig_str not in _existing_claims:
            _inferred_to_merge.append(_sig_str)
    if _inferred_to_merge:
        hc["supplement_claims"] = hc.get("supplement_claims", []) + _inferred_to_merge
        print(f"  ✅ Merged inferred signals into supplement claims: {_inferred_to_merge}")

    print(f"  Sensitivity: {sens['classification'].upper()}  |  Prebiotics: {rule_outputs['prebiotic_range']['min_g']}–{rule_outputs['prebiotic_range']['max_g']}g  |  CFU tier: {rule_outputs['prebiotic_range']['cfu_tier']}")
    _supp_claims = ', '.join(hc['supplement_claims']) if hc['supplement_claims'] else 'none'
    _vit_claims = ', '.join(hc['vitamin_claims']) if hc['vitamin_claims'] else 'none'
    print(f"  Claims: supplement=[{_supp_claims}]  vitamin=[{_vit_claims}]")
    if hc['microbiome_vitamin_needs']:
        for mv in hc['microbiome_vitamin_needs']:
            print(f"  ⚡ Microbiome signal: {mv['vitamin']} ({mv['trigger']})")
    if ther['therapeutic_vitamins']:
        for tv in ther['therapeutic_vitamins']:
            print(f"  💊 THERAPEUTIC: {tv['vitamin']} {tv['dose']} ({tv['reason']})")
    if ther['enhanced_vitamins']:
        for ev in ther['enhanced_vitamins']:
            print(f"  ↑ ENHANCED: {ev['vitamin']} {ev['dose']} ({ev['reason']})")
    _mg_needs = ', '.join(mg['needs_identified']) if mg.get('needs_identified') else 'none'
    _eve_comps = ', '.join(tm['evening_components']) if tm.get('evening_components') else 'none'
    print(f"  Mg: {mg['capsules']}× ({_mg_needs}, {mg['elemental_mg_total_mg']}mg elemental)  |  Evening cap: {'YES — ' + _eve_comps + ' (template — pending sleep/supplement selection)' if tm['evening_capsule_needed'] else 'No'}")

    # ── STAGE C: Formulation decisions ───────────────────────────────────
    # Architecture: Mix = ALWAYS deterministic | Supplements + Prebiotics = LLM or offline
    print(f"\n─── C. DECISIONS (mix=deterministic, supplements={'LLM' if use_llm else 'offline'}) ───")
    llm_results = run_llm_decisions(unified_input, rule_outputs, use_bedrock=use_llm)
    mix = llm_results["mix_selection"]
    supplements = llm_results["supplement_selection"]
    prebiotics = llm_results["prebiotic_design"]

    print(f"\n  ┌─ PROBIOTIC MIX ──────────────────────────────────────────")
    print(f"  │ Mix {mix.get('mix_id')}: {mix.get('mix_name')}")
    print(f"  │ Trigger: {mix.get('primary_trigger')}")
    if mix.get('clr_context'):
        print(f"  │ CLR: {mix['clr_context']}")
    print(f"  │ Confidence: {mix.get('confidence', '?')}")
    mix_strains = mix.get('strains', [])
    if mix_strains:
        print(f"  │ Strains ({len(mix_strains)}):")
        for s in mix_strains:
            cfu = s.get('cfu_billions', '?')
            print(f"  │   · {s.get('name', '?')} — {cfu}B CFU")
    if mix.get('lp815_added'):
        print(f"  │ + LP815 psychobiotic (5B CFU) — stress/gut-brain support")
    total_cfu = mix.get('total_cfu_billions', sum(s.get('cfu_billions', 0) for s in mix_strains))
    print(f"  │ Total: {total_cfu}B CFU")
    if mix.get('alternative_considered'):
        print(f"  │ Alt considered: {mix['alternative_considered']}")
    # Non-expert explanation from KB
    try:
        with open(KB_DIR / "synbiotic_mixes.json", 'r', encoding='utf-8') as _mf:
            _mix_kb = json.load(_mf).get("mixes", {}).get(str(mix.get("mix_id")), {})
        _client_expl = _mix_kb.get("ecological_rationale", {}).get("client_friendly", "")
        if _client_expl:
            # Word-wrap at ~70 chars for terminal readability
            import textwrap
            _wrapped = textwrap.wrap(_client_expl, width=56)
            print(f"  │")
            print(f"  │ 💡 Why this mix?")
            for _line in _wrapped:
                print(f"  │   {_line}")
    except Exception:
        pass
    print(f"  └────────────────────────────────────────────────────────")


    # Compact supplement/prebiotic listing — one line per category
    _n_vm = len(supplements.get('vitamins_minerals', []))
    _n_sp = len(supplements.get('supplements', []))
    omega = supplements.get('omega3', {})
    _vm_parts = []
    for vm in supplements.get('vitamins_minerals', []):
        _dose = vm.get('dose', '')
        _tag = ' [T]' if vm.get('therapeutic') else ''
        _vm_parts.append(f"{vm['substance']} {_dose}{_tag}")
    _sp_parts = []
    for sp in supplements.get('supplements', []):
        _rank = sp.get('rank', '')
        _rank_tag = f' ({_rank})' if _rank else ''
        _sp_parts.append(f"{sp['substance']} {sp.get('dose_mg', '')}mg{_rank_tag}")
    print(f"\n  Vitamins [{_n_vm}]:    {' · '.join(_vm_parts) if _vm_parts else 'none'}")
    print(f"  Supplements [{_n_sp}]: {' · '.join(_sp_parts) if _sp_parts else 'none'}")
    print(f"  Omega-3: {omega.get('dose_daily_mg', 1500)}mg daily (softgel)")

    # Compact prebiotic listing — one line
    _pb_parts = []
    _has_fodmap = False
    for pb in prebiotics.get('prebiotics', []):
        _fodmap_tag = '[*]' if pb.get('fodmap') else ''
        _pb_parts.append(f"{pb['substance']} {pb['dose_g']}g{_fodmap_tag}")
        if pb.get('fodmap'):
            _has_fodmap = True
    _fodmap_note = '  [*]=FODMAP' if _has_fodmap else ''
    print(f"  Prebiotics [{prebiotics.get('total_grams', 0)}g / {prebiotics.get('total_fodmap_grams', 0)}g FODMAP]: {' · '.join(_pb_parts)}{_fodmap_note}")
    if prebiotics.get('contradictions_found'):
        print(f"  ⚠️ Prebiotic contradictions: {prebiotics['contradictions_found']}")
    if prebiotics.get('overrides_applied'):
        print(f"  ⚠️ Prebiotic overrides: {prebiotics['overrides_applied']}")

    # ── STAGE C.5a: Medication Exclusion Enforcement ─────────────────────
    # Remove any LLM-selected supplements/vitamins that are in the medication exclusion set.
    # This runs BEFORE all downstream routing so excluded substances never enter the formula.
    if medication_exclusion_set:
        _med_excluded_items = []
        # Filter vitamins/minerals
        _vm_before = len(supplements.get("vitamins_minerals", []))
        supplements["vitamins_minerals"] = [
            vm for vm in supplements.get("vitamins_minerals", [])
            if not _is_medication_excluded(vm.get("substance", ""))
            or not _med_excluded_items.append(vm.get("substance", ""))  # side-effect: track removed
        ]
        # Re-filter properly (the above trick doesn't work cleanly)
        _filtered_vms_med = []
        for vm in supplements.get("vitamins_minerals", []):
            if _is_medication_excluded(vm.get("substance", "")):
                _med_excluded_items.append(vm["substance"])
            else:
                _filtered_vms_med.append(vm)
        supplements["vitamins_minerals"] = _filtered_vms_med

        _filtered_supps_med = []
        for sp in supplements.get("supplements", []):
            if _is_medication_excluded(sp.get("substance", "")):
                _med_excluded_items.append(sp["substance"])
            else:
                _filtered_supps_med.append(sp)
        supplements["supplements"] = _filtered_supps_med

        # Filter condition-specific additions
        _filtered_csa_med = []
        for csa in prebiotics.get("condition_specific_additions", []):
            if _is_medication_excluded(csa.get("substance", "")):
                _med_excluded_items.append(csa["substance"])
            else:
                _filtered_csa_med.append(csa)
        prebiotics["condition_specific_additions"] = _filtered_csa_med

        if _med_excluded_items:
            print(f"\n  🚫 MEDICATION EXCLUSION ENFORCEMENT: Removed {len(_med_excluded_items)} substance(s) from LLM selections:")
            for _mei in _med_excluded_items:
                print(f"    → {_mei}")

    # ── STAGE C.5b: Must-Include Substrate Enforcement Gate ──────────────
    # After LLM prebiotic design, check that all must_include substrates from
    # the KB are present. If any are missing, add them at minimum effective dose
    # from synbiotic_mixes.json default formula, reducing PHGG to make room.
    try:
        _prebiotic_kb = json.load(open(KB_DIR / "prebiotic_rules.json", 'r', encoding='utf-8'))
        _mixes_kb = json.load(open(KB_DIR / "synbiotic_mixes.json", 'r', encoding='utf-8'))
        _mix_key = f"mix_{mix.get('mix_id', 0)}"
        _must_include_list = _prebiotic_kb.get("per_mix_prebiotics", {}).get(_mix_key, {}).get("must_include", [])
        _mix_default_formula = _mixes_kb.get("mixes", {}).get(str(mix.get("mix_id", 0)), {}).get("default_prebiotic_formula", {})
        _default_components = {c["substance"].lower(): c for c in _mix_default_formula.get("components", [])}

        if _must_include_list:
            # Normalize existing prebiotic names for comparison
            _existing_pb_names = set()
            for pb in prebiotics.get("prebiotics", []):
                _existing_pb_names.add(pb["substance"].lower().strip())
                # Also add common aliases
                _name_l = pb["substance"].lower().strip()
                if "inulin" in _name_l:
                    _existing_pb_names.add("inulin")
                if "fos" in _name_l or "oligofructose" in _name_l:
                    _existing_pb_names.add("fos")
                if "gos" in _name_l or "galactooligosaccharide" in _name_l:
                    _existing_pb_names.add("gos")
                if "beta" in _name_l and "glucan" in _name_l:
                    _existing_pb_names.add("beta-glucans")

            _substrate_added = []
            _substrate_adjusted = []
            _pb_max_g = rule_outputs.get("prebiotic_range", {}).get("max_g", 99)
            for _mi_name in _must_include_list:
                _mi_lower = _mi_name.lower().strip()
                # Check if already present (fuzzy)
                _found = any(_mi_lower in existing or existing in _mi_lower for existing in _existing_pb_names)

                # If present, check if dose is at or above KB minimum
                if _found:
                    _default_entry_dose_check = None
                    for _dk, _dv in _default_components.items():
                        if _mi_lower in _dk or _dk in _mi_lower:
                            _default_entry_dose_check = _dv
                            break
                    if _default_entry_dose_check:
                        _kb_min_dose = _default_entry_dose_check.get("dose_g", 0.5)
                        # Find the actual prebiotic entry and check its dose
                        for _pb in prebiotics.get("prebiotics", []):
                            _pb_lower = _pb["substance"].lower().strip()
                            if _mi_lower in _pb_lower or _pb_lower in _mi_lower or \
                               ("inulin" in _mi_lower and "inulin" in _pb_lower) or \
                               ("fos" in _mi_lower and "fos" in _pb_lower) or \
                               ("gos" in _mi_lower and "gos" in _pb_lower):
                                if _pb["dose_g"] < _kb_min_dose:
                                    _old_dose = _pb["dose_g"]
                                    _dose_increase = _kb_min_dose - _old_dose
                                    # Check if increasing would exceed max — reduce PHGG if needed
                                    _current_total = sum(p.get("dose_g", 0) for p in prebiotics.get("prebiotics", []))
                                    if _current_total + _dose_increase > _pb_max_g:
                                        _need_to_free = (_current_total + _dose_increase) - _pb_max_g
                                        for _p in prebiotics.get("prebiotics", []):
                                            if "phgg" in _p["substance"].lower() and _p["dose_g"] > _need_to_free:
                                                _old_phgg = _p["dose_g"]
                                                _p["dose_g"] = round(_p["dose_g"] - _need_to_free, 2)
                                                print(f"  🔧 PHGG reduced {_old_phgg}g → {_p['dose_g']}g to increase {_mi_name} to minimum dose")
                                                break
                                    _pb["dose_g"] = _kb_min_dose
                                    _substrate_adjusted.append(f"{_pb['substance']} {_old_dose}g → {_kb_min_dose}g (KB minimum)")
                                break
                    continue  # Substance is present (dose now checked/adjusted)

                if not _found:
                    # Look up minimum effective dose from default formula
                    _default_entry = None
                    for _dk, _dv in _default_components.items():
                        if _mi_lower in _dk or _dk in _mi_lower:
                            _default_entry = _dv
                            break
                    if _default_entry:
                        _min_dose = _default_entry.get("dose_g", 0.5)
                        _fodmap = _default_entry.get("fodmap", False)
                        _rationale = _default_entry.get("rationale", f"Must-include substrate for Mix {mix.get('mix_id')} strains")
                        # Check if adding would exceed max — if so, reduce PHGG
                        _current_total = sum(p.get("dose_g", 0) for p in prebiotics.get("prebiotics", []))
                        if _current_total + _min_dose > _pb_max_g:
                            _need_to_free = (_current_total + _min_dose) - _pb_max_g
                            # Reduce PHGG first (it's the non-essential filler)
                            for _p in prebiotics.get("prebiotics", []):
                                if "phgg" in _p["substance"].lower() and _p["dose_g"] > _need_to_free:
                                    _old_phgg = _p["dose_g"]
                                    _p["dose_g"] = round(_p["dose_g"] - _need_to_free, 2)
                                    print(f"  🔧 PHGG reduced {_old_phgg}g → {_p['dose_g']}g to make room for must-include {_mi_name}")
                                    break

                        prebiotics["prebiotics"].append({
                            "substance": _default_entry.get("substance", _mi_name),
                            "dose_g": _min_dose,
                            "fodmap": _fodmap,
                            "rationale": f"MUST-INCLUDE substrate enforcement: {_rationale}",
                        })
                        _substrate_added.append(f"{_default_entry.get('substance', _mi_name)} {_min_dose}g")
                        _existing_pb_names.add(_mi_lower)

            if _substrate_added:
                # Recalculate totals
                prebiotics["total_grams"] = round(sum(p["dose_g"] for p in prebiotics["prebiotics"]), 2)
                prebiotics["total_fodmap_grams"] = round(sum(p["dose_g"] for p in prebiotics["prebiotics"] if p.get("fodmap")), 2)
                print(f"  📌 SUBSTRATE ENFORCEMENT: Added {len(_substrate_added)} missing must-include substrate(s):")
                for _sa in _substrate_added:
                    print(f"    → {_sa}")
                print(f"    New prebiotic total: {prebiotics['total_grams']}g ({prebiotics['total_fodmap_grams']}g FODMAP)")
    except Exception as _substrate_err:
        print(f"  ⚠️ Must-include substrate enforcement failed: {_substrate_err}")

    # ── STAGE D: Post-Processing ─────────────────────────────────────────
    print("\n─── D. ROUTING ─────────────────────────────────────────────")

    # ── Build effective_goals: ranked goals + inferred health signals ─────
    # Inferred signals (stress_anxiety, sleep_quality, etc.) represent real client
    # needs discovered by the LLM clinical analysis. They must influence timing
    # decisions, not just supplement selection. Map them to goal-equivalent strings
    # so the existing keyword-based timing engine recognizes them.
    INFERRED_SIGNAL_TO_GOAL = {
        "stress_anxiety": "reduce_stress_anxiety",
        "sleep_quality": "improve_sleep_quality",
        "fatigue": "boost_energy_reduce_fatigue",
        "bowel_function": "improve_digestion_gut_comfort",
        "skin_quality": "improve_skin_health",
        "immune_system": "strengthen_immune_resilience",
        "infection_susceptibility": "strengthen_immune_resilience",
        "heart_health": "support_heart_health",
        "weight_management": "manage_weight",
        "bone_health": "support_bone_health",
        "hormone_balance": "support_hormone_balance",
        "anti_inflammatory": "reduce_inflammation",
    }
    _effective_goals_list = list(unified_input["questionnaire"]["goals"].get("ranked", []))
    _effective_goals_set = set(g.lower() for g in _effective_goals_list)
    _inferred_goals_added = []
    for _sig in clinical_summary.get("inferred_health_signals", []):
        _sig_str = _sig.get("signal", _sig) if isinstance(_sig, dict) else _sig
        _mapped_goal = INFERRED_SIGNAL_TO_GOAL.get(_sig_str)
        if _mapped_goal and _mapped_goal.lower() not in _effective_goals_set:
            _effective_goals_list.append(_mapped_goal)
            _effective_goals_set.add(_mapped_goal.lower())
            _inferred_goals_added.append(f"{_sig_str} → {_mapped_goal}")
    if _inferred_goals_added:
        print(f"  ✅ Effective goals expanded: {_inferred_goals_added}")

    _effective_goals = {"ranked": _effective_goals_list, "top_goal": unified_input["questionnaire"]["goals"].get("top_goal", "")}

    # Re-apply timing with knowledge of selected components + effective goals
    selected_components = [s.get("substance", "") for s in supplements.get("supplements", [])]
    timing = apply_timing_rules(
        unified_input["questionnaire"]["lifestyle"],
        _effective_goals,
        selected_components
    )
    rule_outputs["timing"] = timing
    _gate_passes = []  # Collect silent gate passes for compact display
    if timing['evening_components']:
        print(f"  Timing: {len(timing['evening_components'])} evening components — {timing['evening_components']}")
    else:
        print(f"  Timing: All morning")

    # Remove deterministic-handled items from LLM selections (safety filter)
    # Use word-boundary-aware matching to avoid false positives (e.g., "dha" matching "ashwagandha")
    import re
    EXCLUDED_SUBSTANCES = {"magnesium", "vitamin d", "vitamin d3", "vitamin e", "omega-3", "omega",
                           "dha", "epa", "astaxanthin", "melatonin", "l-theanine", "valerian",
                           "valerian root", "valeriana"}
    # Prebiotic fibers must NOT be selected by supplement LLM — handled by prebiotic design step
    EXCLUDED_FIBERS = {"phgg", "psyllium", "psyllium husk", "inulin", "pure inulin", "fos",
                       "oligofructose", "gos", "galactooligosaccharides", "beta-glucans",
                       "beta-glucans (oats)", "resistant starch", "glucomannan"}
    def _is_excluded(substance_name: str) -> bool:
        name_lower = substance_name.lower()
        for ex in EXCLUDED_SUBSTANCES:
            # Use word boundary matching to avoid "dha" matching "ashwagandha"
            if re.search(r'\b' + re.escape(ex) + r'\b', name_lower):
                return True
            # Also check if the full name IS the excluded term
            if name_lower.strip() == ex:
                return True
        return False

    def _is_excluded_fiber(substance_name: str) -> bool:
        """Check if supplement is a prebiotic fiber (handled by prebiotic design step)."""
        name_lower = substance_name.lower().strip()
        return name_lower in EXCLUDED_FIBERS

    filtered_vms = []
    removed = []
    removed_fibers = []
    for vm in supplements.get("vitamins_minerals", []):
        if _is_excluded(vm.get("substance", "")):
            removed.append(vm["substance"])
        else:
            filtered_vms.append(vm)
    filtered_supps = []
    for sp in supplements.get("supplements", []):
        if _is_excluded(sp.get("substance", "")):
            removed.append(sp["substance"])
        elif _is_excluded_fiber(sp.get("substance", "")):
            removed_fibers.append(sp["substance"])
        else:
            filtered_supps.append(sp)
    supplements["vitamins_minerals"] = filtered_vms
    supplements["supplements"] = filtered_supps
    if removed:
        print(f"  ⚠️ Excluded {len(removed)} LLM-selected items (handled deterministically): {removed}")
    if removed_fibers:
        print(f"  ⚠️ Excluded {len(removed_fibers)} fiber(s) from supplement LLM (handled by prebiotic design): {removed_fibers}")

    # ── STAGE D.0a-ext: Delivery-Aware Exclusion Sweep ───────────────────
    # LLM sometimes routes excluded substances (e.g., L-Theanine) to evening_capsule
    # delivery, bypassing the sachet-only exclusion filter above. This second pass
    # catches excluded substances regardless of their delivery target.
    _delivery_aware_removed = []
    filtered_supps_ext = []
    for sp in supplements.get("supplements", []):
        if _is_excluded(sp.get("substance", "")):
            _delivery_aware_removed.append(f"{sp['substance']} (delivery={sp.get('delivery', '?')})")
        else:
            filtered_supps_ext.append(sp)
    if _delivery_aware_removed:
        supplements["supplements"] = filtered_supps_ext
        print(f"  ⚠️ Delivery-aware exclusion sweep: removed {len(_delivery_aware_removed)} item(s) with non-sachet delivery:")
        for r in _delivery_aware_removed:
            print(f"    → {r}")

    # ── STAGE D.0b: Vitamin Inclusion Gate (deterministic post-LLM filter) ──
    # Prevents LLM from adding vitamins without clinical justification
    vitamin_gate_removed = []
    _sex = unified_input.get("questionnaire", {}).get("demographics", {}).get("biological_sex", "").lower()
    _reported_deficiencies_lower = [d.lower() for d in rule_outputs.get("therapeutic_triggers", {}).get("reported_deficiencies", []) if d]
    _mb_vitamin_needs = [n.get("vitamin", "").lower() for n in rule_outputs.get("health_claims", {}).get("microbiome_vitamin_needs", [])]
    _vitamin_claims = rule_outputs.get("health_claims", {}).get("vitamin_claims", [])
    _ranked_goals = unified_input.get("questionnaire", {}).get("goals", {}).get("ranked", [])
    _ranked_goals_lower = [g.lower() for g in _ranked_goals]

    # B-vitamin justification keywords in goals
    B_VITAMIN_GOAL_KEYWORDS = {"fatigue", "energy", "immune", "metabolism", "skin"}
    _has_b_vitamin_goal = any(
        any(kw in g for kw in B_VITAMIN_GOAL_KEYWORDS)
        for g in _ranked_goals_lower
    )
    _has_b_vitamin_claim = any(
        c in ("Fatigue", "Immune System", "Metabolism", "Skin Quality")
        for c in _vitamin_claims
    )
    _has_b_vitamin_mb_need = any("b" in n for n in _mb_vitamin_needs)

    B_VITAMIN_IDS = {"thiamin", "b1", "riboflavin", "b2", "niacin", "b3", "pantothenic", "b5",
                     "b6", "b12", "folate", "b9", "biotin", "b7"}

    filtered_vms_gate = []
    for vm in supplements.get("vitamins_minerals", []):
        substance_lower = vm.get("substance", "").lower()

        # Rule 1: Iron gate — males should not receive iron unless deficiency reported
        if "iron" in substance_lower:
            if _sex == "male" and not any("iron" in d for d in _reported_deficiencies_lower):
                vitamin_gate_removed.append(f"{vm['substance']} (iron excluded for males — KB rule: 'men avoid')")
                continue

        # Rule 1b: B6 restricted — rare deficiency, neuropathy risk at >200mg
        # Only include if explicitly reported as deficient or microbiome-flagged
        if "b6" in substance_lower:
            has_b6_deficiency = any("b6" in d for d in _reported_deficiencies_lower)
            has_b6_mb_signal = any("b6" in n for n in _mb_vitamin_needs)
            is_b6_therapeutic = vm.get("therapeutic", False)
            if not has_b6_deficiency and not has_b6_mb_signal and not is_b6_therapeutic:
                vitamin_gate_removed.append(f"{vm['substance']} (B6 restricted — rare deficiency, neuropathy risk; not reported as deficient)")
                continue

        # Rule 2: B-vitamin gate — only include if goal/claim/microbiome justifies
        is_b_vitamin = any(bid in substance_lower for bid in B_VITAMIN_IDS)
        if is_b_vitamin:
            # Check if this specific B vitamin has a microbiome signal
            has_specific_mb_need = any(substance_lower.split("(")[0].strip() in n or substance_lower in n for n in _mb_vitamin_needs)
            # Check if it was therapeutically triggered
            has_therapeutic = vm.get("therapeutic", False)
            # Check if it was marked as microbiome-informed
            is_mb_informed = vm.get("informed_by") in ("microbiome", "both")

            if has_specific_mb_need or has_therapeutic or is_mb_informed or _has_b_vitamin_goal or _has_b_vitamin_claim or _has_b_vitamin_mb_need:
                # Justified — keep
                filtered_vms_gate.append(vm)
            else:
                vitamin_gate_removed.append(f"{vm['substance']} (B-vitamin with no goal/claim/microbiome justification)")
                continue
        else:
            filtered_vms_gate.append(vm)
            continue

    if vitamin_gate_removed:
        supplements["vitamins_minerals"] = filtered_vms_gate
        print(f"  🚫 Vitamin gate removed {len(vitamin_gate_removed)} unjustified vitamin(s):")
        for vr in vitamin_gate_removed:
            print(f"    → {vr}")
    else:
        _n_v = len(supplements.get('vitamins_minerals', []))
        if _n_v > 0: _gate_passes.append(f"{_n_v}/{_n_v} vitamins justified")

    # Enforce Delivery Format Rules (DETERMINISTIC OVERRIDE)
    # Fat-soluble vitamins → softgel; all other vitamins/minerals → morning_wellness_capsule
    FAT_SOLUBLE = {"vitamin a", "vitamin d", "vitamin d3", "vitamin e"}
    overridden = []
    for vm in supplements.get("vitamins_minerals", []):
        substance_lower = vm.get("substance", "").lower()
        is_fat_soluble = any(fs in substance_lower for fs in FAT_SOLUBLE)
        old_delivery = vm.get("delivery", "?")
        if is_fat_soluble:
            vm["delivery"] = "softgel"
        else:
            vm["delivery"] = "morning_wellness_capsule"
        if old_delivery != vm["delivery"]:
            overridden.append(f"{vm['substance']}: {old_delivery} → {vm['delivery']}")
    if overridden:
        print(f"  Delivery overrides: {len(overridden)} corrected")
        for o in overridden:
            print(f"    → {o}")
    else:
        _gate_passes.append("delivery correct")

    # ── STAGE D.0c: Capsule-Only Substance Enforcement ───────────────────
    # Deterministic override: substances listed in KB capsule_only_substances
    # must NEVER go in the sachet (bitter/pungent taste). Reroute to capsule.
    _dfr_path = KB_DIR / "delivery_format_rules.json"
    with open(_dfr_path, 'r', encoding='utf-8') as _dfrf:
        _dfr_kb = json.load(_dfrf)
    _capsule_only_list = [s.lower() for s in _dfr_kb.get("capsule_only_substances", {}).get("substances", [])]
    _capsule_only_rerouted = []
    for sp in supplements.get("supplements", []):
        if sp.get("delivery") == "sachet":
            sp_lower = sp.get("substance", "").lower()
            # Check if any capsule-only keyword matches (substring for renamed substances like "Curcumin 500mg (+ 5.0mg Piperine)")
            matched_keyword = None
            for co_name in _capsule_only_list:
                if co_name in sp_lower:
                    matched_keyword = co_name
                    break
            if matched_keyword:
                # Reroute: polyphenols handled by tier routing downstream, non-polyphenols → evening capsule
                old_delivery = sp["delivery"]
                sp["delivery"] = "evening_capsule"
                _capsule_only_rerouted.append(f"{sp['substance']}: sachet → evening_capsule (KB rule: '{matched_keyword}' is capsule-only — bitter/pungent)")
    if _capsule_only_rerouted:
        print(f"  🔄 Rerouted {len(_capsule_only_rerouted)} capsule-only substance(s) out of jar/morning capsule:")
        for r in _capsule_only_rerouted:
            print(f"    → {r}")
    else:
        _gate_passes.append("no capsule-only mis-routes")

    # ── STAGE D.1b: Polyphenol Exclusion Guards (pregnancy, kidney, anticoagulant) ──
    from rules_engine import check_polyphenol_exclusions
    polyphenol_exclusions = check_polyphenol_exclusions(
        unified_input.get("questionnaire", {}).get("medical", {}),
        unified_input.get("questionnaire", {}).get("demographics", {})
    )
    excluded_polyphenols = set(polyphenol_exclusions.get("excluded_substances", []))
    if excluded_polyphenols:
        for reason in polyphenol_exclusions.get("reasoning", []):
            print(f"  🚨 {reason}")
        # Remove excluded polyphenols from LLM selections
        before_sp = len(supplements.get("supplements", []))
        supplements["supplements"] = [
            sp for sp in supplements.get("supplements", [])
            if sp.get("substance", "").lower() not in excluded_polyphenols
        ]
        removed_pp = before_sp - len(supplements.get("supplements", []))
        if removed_pp > 0:
            print(f"  🗑️  Removed {removed_pp} excluded polyphenol(s) from supplement selection")
        # Also remove from condition_specific_additions in prebiotics
        before_csa = len(prebiotics.get("condition_specific_additions", []))
        prebiotics["condition_specific_additions"] = [
            csa for csa in prebiotics.get("condition_specific_additions", [])
            if csa.get("substance", "").lower() not in excluded_polyphenols
        ]
        removed_csa = before_csa - len(prebiotics.get("condition_specific_additions", []))
        if removed_csa > 0:
            print(f"  🗑️  Removed {removed_csa} excluded polyphenol(s) from condition-specific additions")
    else:
        _gate_passes.append("no exclusions")
    for flag in polyphenol_exclusions.get("flagged_interactions", []):
        print(f"  ⚠️ FLAGGED: {flag['substance']} — {flag['warning']}")

    # ── STAGE D.1c: Piperine Auto-Addition for Curcumin ──────────────────
    _piperine_applied = False
    for i, sp in enumerate(supplements.get("supplements", [])):
        substance_lower = sp.get("substance", "").lower()
        if "curcumin" in substance_lower:
            curcumin_dose = sp.get("dose_mg", 500)
            piperine_dose = round(curcumin_dose / 100, 1)
            total_weight = curcumin_dose + piperine_dose
            new_name = f"Curcumin {curcumin_dose}mg (+ {piperine_dose}mg Piperine)"
            print(f"  → Curcumin detected: {curcumin_dose}mg")
            print(f"  → Auto-adding Piperine at 1:100 ratio: {piperine_dose}mg")
            print(f"  → Bundled: {new_name} (total weight: {total_weight}mg)")
            sp["substance"] = new_name
            sp["dose_mg"] = total_weight
            sp["piperine_auto_added"] = True
            sp["curcumin_dose_mg"] = curcumin_dose
            sp["piperine_dose_mg"] = piperine_dose
            _piperine_applied = True
            break
    if not _piperine_applied:
        pass  # No curcumin — piperine auto-addition skipped

    # ── Tracking sets for presence check explanations (declared early — used in D.1d and D.2b) ──
    # These track WHY supplements were removed so the presence check can explain
    evening_overflow_dropped = set()  # Dropped due to evening capsule overflow
    conflict_removed_names = set()    # Dropped due to mineral absorption conflict
    polyphenol_cap_dropped = set()    # Dropped due to 1000mg polyphenol cap

    # ── STAGE D.1d: Polyphenol Diversity Rule (1000mg total cap + tier routing) ──
    # Load polyphenol classification from KB (single source of truth)
    _pp_kb_path = KB_DIR / "delivery_format_rules.json"
    with open(_pp_kb_path, 'r', encoding='utf-8') as _ppf:
        _pp_kb = json.load(_ppf).get("polyphenol_delivery_classification", {})
    POLYPHENOL_MASS_CAP_MG = _pp_kb.get("total_polyphenol_mass_cap_mg", 1000)
    # Build tier sets from KB
    TIER_2_IDS_RAW = set(_pp_kb.get("capsule_only_tier_2", {}).get("substances", {}).keys())
    TIER_1_IDS_RAW = set(_pp_kb.get("capsule_only_tier_1", {}).get("substances", {}).keys())
    SACHET_SAFE_IDS_RAW = set(_pp_kb.get("sachet_safe", {}).get("substances", {}).keys())
    # Normalize: replace underscores with spaces + extract individual keywords from compound keys
    # e.g., "curcumin_piperine" → {"curcumin_piperine", "curcumin piperine", "curcumin", "bergamot"}
    def _expand_polyphenol_ids(raw_ids):
        expanded = set()
        for k in raw_ids:
            expanded.add(k)  # original: "curcumin_piperine"
            expanded.add(k.replace("_", " "))  # space version: "curcumin piperine"
            # Add first word as standalone keyword for substring matching
            # e.g., "curcumin_piperine" → "curcumin", "bergamot_polyphenolic_fraction" → "bergamot"
            first_word = k.split("_")[0]
            if len(first_word) >= 4:  # Only meaningful words (skip "gos", "fos" etc.)
                expanded.add(first_word)
        return expanded
    TIER_2_IDS = _expand_polyphenol_ids(TIER_2_IDS_RAW)
    TIER_1_IDS = _expand_polyphenol_ids(TIER_1_IDS_RAW)
    SACHET_SAFE_IDS = _expand_polyphenol_ids(SACHET_SAFE_IDS_RAW)
    POLYPHENOL_IDS = TIER_2_IDS | TIER_1_IDS | SACHET_SAFE_IDS | {"piperine"}

    def _is_polyphenol(name):
        nl = name.lower()
        return any(pid in nl for pid in POLYPHENOL_IDS)

    def _polyphenol_tier(name):
        nl = name.lower()
        if any(pid in nl for pid in TIER_2_IDS):
            return 2
        if any(pid in nl for pid in TIER_1_IDS):
            return 1
        if any(pid in nl for pid in SACHET_SAFE_IDS):
            return "sachet"
        return None

    # Collect all polyphenol supplements from LLM selection
    polyphenol_supps = []
    non_polyphenol_supps = []
    for sp in supplements.get("supplements", []):
        if _is_polyphenol(sp.get("substance", "")):
            polyphenol_supps.append(sp)
        else:
            non_polyphenol_supps.append(sp)

    # Also check condition-specific additions
    polyphenol_csa = []
    non_polyphenol_csa = []
    for csa in prebiotics.get("condition_specific_additions", []):
        if _is_polyphenol(csa.get("substance", "")):
            polyphenol_csa.append(csa)
        else:
            non_polyphenol_csa.append(csa)

    if polyphenol_supps or polyphenol_csa:

        total_polyphenol_mg = 0
        tier2_items = []
        tier1_items = []
        sachet_items = []

        # ── Source attribution log ────────────────────────────────
        _pp_source_lines = []

        # Classify and sum
        for sp in polyphenol_supps:
            tier = _polyphenol_tier(sp.get("substance", ""))
            dose = sp.get("dose_mg", 0)
            _rank_tag = f" ({sp.get('rank', '?')})" if sp.get("rank") else ""
            _tier_label = f"Tier {tier}" if isinstance(tier, int) else "jar-safe"
            _pp_source_lines.append(f"    · {sp.get('substance', '?')} {dose}mg — supplement LLM{_rank_tag}, {_tier_label}")
            if tier == 2:
                tier2_items.append(sp)
                total_polyphenol_mg += dose
            elif tier == 1:
                tier1_items.append(sp)
                total_polyphenol_mg += dose
            elif tier == "sachet":
                sachet_items.append(sp)
                total_polyphenol_mg += dose
            else:
                # Unknown polyphenol — treat as sachet-safe
                sachet_items.append(sp)
                total_polyphenol_mg += dose

        for csa in polyphenol_csa:
            dose_str = str(csa.get("dose_g_or_mg", "0mg"))
            if "mg" in dose_str.lower():
                _num_match = re.search(r'[\d.]+', dose_str)
                dose = float(_num_match.group()) if _num_match else 0
            else:
                dose = 0
            csa_item = {"substance": csa["substance"], "dose_mg": dose, "_from_csa": True, "_csa": csa,
                        "health_claim": csa.get("condition", ""), "rationale": csa.get("rationale", "")}
            # Classify CSA polyphenols by their ACTUAL tier (same logic as supplement polyphenols)
            csa_tier = _polyphenol_tier(csa["substance"])
            _csa_condition = csa.get("condition", "prebiotic CSA")
            _csa_tier_label = f"Tier {csa_tier}" if isinstance(csa_tier, int) else "jar-safe"
            _pp_source_lines.append(f"    · {csa['substance']} {dose}mg — prebiotic CSA ({_csa_condition}), {_csa_tier_label}")
            if csa_tier == 2:
                tier2_items.append(csa_item)
                total_polyphenol_mg += dose
            elif csa_tier == 1:
                tier1_items.append(csa_item)
                total_polyphenol_mg += dose
            else:
                # Sachet-safe CSA polyphenol — stays in condition_specific_additions for downstream handling
                sachet_items.append(csa_item)
                total_polyphenol_mg += dose

        # ── Print polyphenol source attribution ──────────────────────
        if _pp_source_lines:
            _pp_status = "⚠️ over cap" if total_polyphenol_mg > POLYPHENOL_MASS_CAP_MG else "✅ within cap"
            print(f"  Polyphenol sources ({total_polyphenol_mg:.0f}mg total, {POLYPHENOL_MASS_CAP_MG}mg cap — {_pp_status}):")
            for _line in _pp_source_lines:
                print(_line)

        # Enforce 1000mg cap — LLM-informed dropping with deterministic fallback
        # Snapshot original total BEFORE any dose reductions or drops
        _orig_pp_total_snapshot = total_polyphenol_mg

        if total_polyphenol_mg > POLYPHENOL_MASS_CAP_MG:
            overage = total_polyphenol_mg - POLYPHENOL_MASS_CAP_MG

            # ── STEP 0: Try reducing doses before dropping a whole item ────
            # Sort all polyphenol items largest-first and reduce toward KB mins
            # If this closes the overage, skip the LLM whole-item drop entirely
            _pp_kb_lkp = _load_supplement_kb_lookup()
            def _pp_min(item):
                """Get KB minimum dose for a polyphenol item."""
                name = item.get("substance", "").lower()
                # Direct lookup
                kb = _pp_kb_lkp.get(name)
                if not kb:
                    for k, v in _pp_kb_lkp.items():
                        if k in name or name in k:
                            kb = v
                            break
                if kb and kb.get("min_dose_mg") is not None:
                    return kb["min_dose_mg"]
                # Fallback: allow reducing to 60% of current dose
                return round(item.get("dose_mg", 0) * 0.6)

            _all_pp_for_reduce = tier2_items + tier1_items
            _all_pp_for_reduce.sort(key=lambda x: -x.get("dose_mg", 0))  # Largest first
            _step0_overage = overage
            _step0_reductions = []
            for _pp_item in _all_pp_for_reduce:
                if _step0_overage <= 0:
                    break
                _current = _pp_item.get("dose_mg", 0)
                _min = _pp_min(_pp_item)
                _can_save = _current - _min
                if _can_save > 0:
                    _reduce_by = min(_step0_overage, _can_save)
                    _new_dose = _current - _reduce_by
                    _step0_reductions.append((_pp_item, _current, _new_dose))
                    _pp_item["dose_mg"] = _new_dose
                    total_polyphenol_mg -= _reduce_by
                    _step0_overage -= _reduce_by

            if _step0_overage <= 0.5:
                # Dose reduction resolved it — log and skip LLM drop
                for _reduced_item, _old_dose, _new_dose in _step0_reductions:
                    print(f"  ↓ {_reduced_item['substance']} {_old_dose}mg → {_new_dose}mg (dose reduced, kept in formulation)")
                overage = 0  # Signal downstream: no LLM drop needed
            else:
                # Restore reductions (couldn't fully resolve) and proceed to LLM drop
                for _reduced_item, _old_dose, _new_dose in _step0_reductions:
                    _reduced_item["dose_mg"] = _old_dose
                    total_polyphenol_mg += (_old_dose - _new_dose)
                _step0_reductions = []
                overage = total_polyphenol_mg - POLYPHENOL_MASS_CAP_MG

            # ── STEP 0b: Reduce least-important polyphenol to its clinical minimum ──
            # Only reduce the single least-important item (by rank_priority), not all.
            # This avoids the label/dose mismatch caused by proportional scaling (which
            # changes dose_mg but leaves the substance name string stale).
            #
            # After any dose reduction, names are re-synced via _sync_pp_name() so the
            # substance string always reflects the actual dose_mg.
            if overage > 0.5:
                def _pp_clinical_min(item):
                    """Get clinical minimum dose from KB — single source of truth.
                    
                    Uses _pp_kb_lkp (already loaded above) so any KB update is
                    automatically picked up without touching this code.
                    Falls back to 150mg generic floor for unknown polyphenols.
                    """
                    name_lower = item.get("substance", "").lower()
                    kb = _pp_kb_lkp.get(name_lower)
                    if not kb:
                        for _k, _v in _pp_kb_lkp.items():
                            if _k in name_lower or name_lower in _k:
                                kb = _v
                                break
                    if kb and kb.get("min_dose_mg") is not None:
                        return kb["min_dose_mg"]
                    return 150  # generic floor for unknown polyphenols

                def _sync_pp_name(item):
                    """Rebuild substance name string after a dose change.
                    
                    Keeps the displayed name consistent with dose_mg so there is
                    never a mismatch (e.g. name says '500mg' but fill is 437mg).
                    Handles Curcumin+Piperine (piperine is always 1% of curcumin dose).
                    """
                    name = item.get("substance", "")
                    dose = item.get("dose_mg", 0)
                    if "curcumin" in name.lower():
                        # Reconstruct: total dose_mg includes piperine (1%)
                        # curcumin_dose = dose / 1.01, piperine = dose - curcumin_dose
                        curcumin_dose = round(dose / 1.01)
                        piperine_dose = round(dose - curcumin_dose, 1)
                        item["substance"] = f"Curcumin {curcumin_dose}mg (+ {piperine_dose}mg Piperine)"
                        item["curcumin_dose_mg"] = curcumin_dose
                        item["piperine_dose_mg"] = piperine_dose
                    # Other polyphenols don't embed dose in name — no update needed

                # Build list of all polyphenols sorted by priority (least important first)
                # rank_priority: higher number = lower priority = reduce first
                _all_pp = tier2_items + tier1_items + sachet_items
                _pp_with_rank = []
                for _item in _all_pp:
                    _kb = _pp_kb_lkp.get(_item.get("substance", "").lower())
                    if not _kb:
                        for _k, _v in _pp_kb_lkp.items():
                            if _k in _item.get("substance", "").lower() or _item.get("substance", "").lower() in _k:
                                _kb = _v
                                break
                    _rank = _kb.get("rank_priority", 3) if _kb else 3
                    # Also consider tier: tier 1 is less important than tier 2
                    # (tier 2 = large dedicated capsule = primary; tier 1 = evening overflow = secondary)
                    _tier_weight = 0 if _item in tier2_items else 1
                    _pp_with_rank.append((_rank + _tier_weight * 0.5, _item))

                # Sort: highest rank (= lowest priority) first → reduce these first
                _pp_with_rank.sort(key=lambda x: -x[0])

                _step0b_resolved = False
                for _priority_score, _target_item in _pp_with_rank:
                    if overage <= 0.5:
                        _step0b_resolved = True
                        break
                    _current_dose = _target_item.get("dose_mg", 0)
                    _min_dose = _pp_clinical_min(_target_item)
                    _can_reduce = _current_dose - _min_dose
                    if _can_reduce > 0:
                        _reduce_by = min(overage, _can_reduce)
                        _new_dose = round(_current_dose - _reduce_by)
                        _old_name = _target_item["substance"]
                        _target_item["dose_mg"] = _new_dose
                        total_polyphenol_mg -= _reduce_by
                        overage -= _reduce_by
                        _sync_pp_name(_target_item)
                        _new_name = _target_item["substance"]
                        if _new_name != _old_name:
                            print(f"  ↓ {_old_name} → {_new_name} {_new_dose}mg (least important reduced to fit {POLYPHENOL_MASS_CAP_MG}mg cap)")
                        else:
                            print(f"  ↓ {_old_name} {_current_dose}mg → {_new_dose}mg (least important reduced to fit {POLYPHENOL_MASS_CAP_MG}mg cap)")

                if overage <= 0.5:
                    overage = 0  # Resolved — skip LLM drop
                    print(f"  ✅ Step 0b least-important reduction resolved polyphenol cap — no items dropped")
                else:
                    print(f"  ⚠️ Step 0b: minimum achievable total still exceeds {POLYPHENOL_MASS_CAP_MG}mg cap ({total_polyphenol_mg:.0f}mg) — deterministic drop")
                    # ── STEP 0c: Deterministic drop of lowest-priority item ──────
                    # Mathematical impossibility: min doses of selected polyphenols
                    # exceed the cap. Drop the single lowest-priority item.
                    # Priority rule: tier2 items (dedicated capsule) are ALWAYS
                    # more important than tier1 items (evening overflow). Within
                    # each tier, lower rank_priority number = more important.
                    # This means tier1 items are always dropped before tier2.
                    _all_pp_for_drop = tier2_items + tier1_items + sachet_items
                    _drop_candidates = []
                    for _dp_item in _all_pp_for_drop:
                        _dp_kb = _pp_kb_lkp.get(_dp_item.get("substance", "").lower())
                        if not _dp_kb:
                            for _k, _v in _pp_kb_lkp.items():
                                if _k in _dp_item.get("substance", "").lower() or _dp_item.get("substance", "").lower() in _k:
                                    _dp_kb = _v
                                    break
                        _dp_rank = _dp_kb.get("rank_priority", 3) if _dp_kb else 3
                        _dp_tier_weight = 0 if _dp_item in tier2_items else 1
                        _drop_candidates.append((_dp_rank + _dp_tier_weight * 10, _dp_item))
                    # Sort: highest score = lowest priority = drop first
                    _drop_candidates.sort(key=lambda x: -x[0])
                    if _drop_candidates:
                        _drop_score, _drop_item = _drop_candidates[0]
                        _drop_name = _drop_item["substance"]
                        _drop_dose = _drop_item.get("dose_mg", 0)
                        if _drop_dose <= overage:
                            # Item dose is ≤ overage — removing it entirely resolves the gap
                            for _tlist in [sachet_items, tier1_items, tier2_items]:
                                if _drop_item in _tlist:
                                    _tlist.remove(_drop_item)
                                    break
                            total_polyphenol_mg -= _drop_dose
                            overage = total_polyphenol_mg - POLYPHENOL_MASS_CAP_MG
                            polyphenol_cap_dropped.add(_drop_name.lower())
                            print(f"  🗑️ Step 0c drop: {_drop_name} ({_drop_dose}mg removed) — dose ≤ overage, item removed to fit {POLYPHENOL_MASS_CAP_MG}mg cap")
                        else:
                            # Item dose > overage — reduce by exactly the overage IF result stays above KB min
                            _new_dose = round(_drop_dose - overage)
                            _drop_kb_min = _pp_clinical_min(_drop_item)
                            if _new_dose < _drop_kb_min:
                                # Reduction would push below KB minimum → drop entirely
                                # (a sub-clinical dose wastes capsule space and gets dropped downstream anyway)
                                for _tlist in [sachet_items, tier1_items, tier2_items]:
                                    if _drop_item in _tlist:
                                        _tlist.remove(_drop_item)
                                        break
                                total_polyphenol_mg -= _drop_dose
                                overage = total_polyphenol_mg - POLYPHENOL_MASS_CAP_MG
                                polyphenol_cap_dropped.add(_drop_name.lower())
                                # Build budget explanation showing what consumed the cap
                                _kept_pp_names = " + ".join(
                                    f"{it.get('substance','?')} ({it.get('dose_mg',0)}mg)"
                                    for it in tier2_items + tier1_items + sachet_items
                                )
                                print(f"  🗑️ Step 0c drop: {_drop_name} — reducing to fit cap would give {_new_dose}mg (below KB min {_drop_kb_min}mg), dropped entirely")
                                print(f"    Budget consumed by: {_kept_pp_names}")
                            else:
                                _drop_item["dose_mg"] = _new_dose
                                _sync_pp_name(_drop_item)
                                _new_name = _drop_item["substance"]
                                total_polyphenol_mg -= overage
                                overage = 0
                                if _new_name != _drop_name:
                                    print(f"  ↓ Step 0c reduce: {_drop_name} → {_new_name} {_new_dose}mg (reduced by exact overage to fit {POLYPHENOL_MASS_CAP_MG}mg cap — item kept)")
                                else:
                                    print(f"  ↓ Step 0c reduce: {_drop_name} {_drop_dose}mg → {_new_dose}mg (reduced by exact overage to fit {POLYPHENOL_MASS_CAP_MG}mg cap — item kept)")
                        if overage <= 0.5:
                            overage = 0
                            print(f"  ✅ Step 0c resolved polyphenol cap — total now {total_polyphenol_mg:.0f}mg")
                        else:
                            print(f"  ⚠️ Step 0c: still {overage:.0f}mg over after adjustment — falling through to LLM")

            # ── LLM-INFORMED DROP: Ask which polyphenol to remove ──
            # Collect all polyphenol items with their claims for the LLM
            all_pp_items = []
            for si in sachet_items:
                all_pp_items.append({"substance": si.get("substance", "?"), "dose_mg": si.get("dose_mg", 0),
                                     "health_claim": si.get("health_claim", ""), "tier": "sachet"})
            for t1 in tier1_items:
                all_pp_items.append({"substance": t1.get("substance", "?"), "dose_mg": t1.get("dose_mg", 0),
                                     "health_claim": t1.get("health_claim", ""), "tier": 1})
            for t2 in tier2_items:
                all_pp_items.append({"substance": t2.get("substance", "?"), "dose_mg": t2.get("dose_mg", 0),
                                     "health_claim": t2.get("health_claim", ""), "tier": 2})

            llm_drop_name = None
            if overage > 0 and use_llm and len(all_pp_items) >= 2:
                from llm_decisions import resolve_polyphenol_conflict
                client_goals = unified_input.get("questionnaire", {}).get("goals", {}).get("ranked", [])
                llm_drop_name = resolve_polyphenol_conflict(
                    polyphenol_items=all_pp_items,
                    client_goals=client_goals,
                    mix_name=mix.get("mix_name", ""),
                    mix_trigger=mix.get("primary_trigger", ""),
                    use_bedrock=use_llm,
                )

            if overage > 0 and llm_drop_name:
                # LLM chose a specific polyphenol to drop — remove it from the correct tier list
                print(f"  🧠 LLM polyphenol decision: DROP '{llm_drop_name}'")
                dropped = False
                for items_list in [sachet_items, tier1_items, tier2_items]:
                    for i, item in enumerate(items_list):
                        if item.get("substance", "").lower() == llm_drop_name:
                            removed_item = items_list.pop(i)
                            total_polyphenol_mg -= removed_item.get("dose_mg", 0)
                            polyphenol_cap_dropped.add(llm_drop_name)
                            print(f"    → Dropped {removed_item['substance']} ({removed_item.get('dose_mg', 0)}mg) — LLM: least relevant to primary goal")
                            dropped = True
                            break
                    if dropped:
                        break
                if not dropped:
                    print(f"  ⚠️ LLM drop target '{llm_drop_name}' not found in lists — falling through to deterministic")
                    llm_drop_name = None  # Force deterministic fallback below

            # Deterministic fallback (also runs if LLM drop wasn't enough to resolve overage)
            overage = total_polyphenol_mg - POLYPHENOL_MASS_CAP_MG
            if overage > 0:
                if llm_drop_name:
                    pass  # LLM drop applied — deterministic trimming may still run
                else:
                    pass  # No LLM drop — deterministic trimming only
        
            # First: try to reduce sachet polyphenol doses
            for si in sachet_items[:]:
                if overage <= 0:
                    break
                dose = si.get("dose_mg", 0)
                # Check if dose can be reduced to minimum (300mg for most)
                min_dose = 300  # default min
                if "apple" in si.get("substance", "").lower():
                    min_dose = 300
                elif "pomegranate" in si.get("substance", "").lower():
                    min_dose = 500

                if dose > min_dose:
                    reduce_by = min(overage, dose - min_dose)
                    si["dose_mg"] = dose - reduce_by
                    overage -= reduce_by
                    total_polyphenol_mg -= reduce_by

            # Second: drop sachet polyphenols below minimum dose
            remaining_sachet = []
            for si in sachet_items:
                if overage <= 0:
                    remaining_sachet.append(si)
                    continue
                dose = si.get("dose_mg", 0)
                min_dose = 300
                if "pomegranate" in si.get("substance", "").lower():
                    min_dose = 500
                if dose < min_dose:
                    overage -= dose
                    total_polyphenol_mg -= dose
                else:
                    remaining_sachet.append(si)
            sachet_items = remaining_sachet

            # Third: drop tier 1 polyphenols by priority if still over
            if overage > 0 and tier1_items:
                # Sort by rank priority (higher number = lower priority = drop first)
                tier1_items.sort(key=lambda x: -x.get("rank_priority", 3) if isinstance(x.get("rank_priority"), (int, float)) else -3)
                remaining_t1 = []
                for t1 in tier1_items:
                    if overage <= 0:
                        remaining_t1.append(t1)
                        continue
                    dose = t1.get("dose_mg", 0)
                    overage -= dose
                    total_polyphenol_mg -= dose
                tier1_items = remaining_t1

        # Route polyphenols to correct delivery
        # Remove polyphenols from main supplement list (they'll be re-routed)
        supplements["supplements"] = non_polyphenol_supps

        # Tier 2 → dedicated polyphenol capsule (morning)
        for t2 in tier2_items:
            supplements["supplements"].append({
                **t2,
                "delivery": "polyphenol_capsule",
                "_polyphenol_tier": 2,
            })

        # Tier 1 → evening capsule headroom
        for t1 in tier1_items:
            supplements["supplements"].append({
                **t1,
                "delivery": "evening_capsule",
                "_polyphenol_tier": 1,
            })

        # Sachet-safe → sachet
        for ss in sachet_items:
            if ss.get("_from_csa"):
                pass  # Keep in condition_specific_additions (already handled downstream)
            else:
                supplements["supplements"].append({
                    **ss,
                    "delivery": "sachet",
                    "_polyphenol_tier": "sachet",
                })

        # Update condition-specific additions (remove polyphenols that were handled)
        prebiotics["condition_specific_additions"] = non_polyphenol_csa

        # Compact polyphenol summary
        _pp_dropped = [i.get("substance","?") + f" {i.get('dose_mg',0)}mg" for i in ([] if not polyphenol_cap_dropped else
            [x for x in tier2_items + tier1_items + sachet_items if x.get("substance","").lower() in polyphenol_cap_dropped])]
        _pp_kept_tier1 = [f"{i.get('substance','?')} {i.get('dose_mg',0)}mg" for i in tier1_items]
        _pp_kept_tier2 = [f"{i.get('substance','?')} {i.get('dose_mg',0)}mg" for i in tier2_items]
        _pp_kept_sachet = [f"{i.get('substance','?')} {i.get('dose_mg',0)}mg" for i in sachet_items if not i.get("_from_csa")]
        _pp_all_kept = _pp_kept_tier2 + _pp_kept_tier1 + _pp_kept_sachet
        _pp_over = total_polyphenol_mg + sum(i.get("dose_mg",0) for i in ([] if not polyphenol_cap_dropped else []))
        _pp_header = f"  Polyphenols ({int(total_polyphenol_mg + sum(x.get('dose_mg',0) for x in (tier2_items + tier1_items + sachet_items if False else [])))}mg"
        # Simple header showing original total
        _orig_pp_total = _orig_pp_total_snapshot  # Use pre-mutation snapshot
        if polyphenol_cap_dropped:
            print(f"  Polyphenols ({_orig_pp_total:.0f}mg > {POLYPHENOL_MASS_CAP_MG}mg cap):")
            for _d in polyphenol_cap_dropped:
                print(f"    ✗ {_d}  dropped  [LLM · over cap]")
        else:
            print(f"  Polyphenols ({total_polyphenol_mg:.0f}mg / {POLYPHENOL_MASS_CAP_MG}mg cap):")
        if _pp_all_kept:
            _dest = "evening cap" if _pp_kept_tier1 else ("morning cap" if _pp_kept_tier2 else "jar")
            print(f"    ✓ {' · '.join(_pp_all_kept)}  →  {_dest}")
    else:
        pass
    
    # ── STAGE D.2: Post-LLM Validation ───────────────────────────────────

    # D.2a: Medication interaction check
    medications = unified_input.get("questionnaire", {}).get("medical", {}).get("medications", [])
    if medications:
        med_names_lower = [m.lower() if isinstance(m, str) else str(m).lower() for m in medications]
        for vm in supplements.get("vitamins_minerals", []):
            note = (vm.get("interaction_note") or "").lower()
            for med in med_names_lower:
                if med in note or any(kw in note for kw in ["avoid with " + med, med]):
                    print(f"  🚨🚨🚨 INTERACTION ALERT: {vm['substance']} — interaction_note mentions '{med}': {vm.get('interaction_note')}")
        for sp in supplements.get("supplements", []):
            note = (sp.get("interaction_note") or sp.get("rationale", "")).lower()
            for med in med_names_lower:
                if med in note:
                    print(f"  🚨🚨🚨 INTERACTION ALERT: {sp['substance']} — may interact with '{med}'")
    else:
        _gate_passes.append("no medications")

    # D.2b: Supplement-to-supplement conflict check
    # Includes mineral absorption conflicts, herb-herb, and herb-drug interactions
    KNOWN_CONFLICTS = {
        # Mineral absorption conflicts
        frozenset({"zinc", "calcium"}): {"warning": "Zinc and Calcium compete for absorption — space by 2 hours", "severity": "medium"},
        frozenset({"zinc", "iron"}): {"warning": "Zinc and Iron compete for absorption — space by 2 hours", "severity": "medium"},
        frozenset({"calcium", "iron"}): {"warning": "Calcium inhibits Iron absorption — space by 2 hours", "severity": "medium"},
        frozenset({"zinc", "copper"}): {"warning": "Zinc inhibits Copper absorption at high doses", "severity": "medium"},
        frozenset({"calcium", "magnesium"}): {"warning": "Calcium and Magnesium compete — space by 2 hours", "severity": "medium"},
        # Herb-herb interactions
        frozenset({"ashwagandha", "rhodiola"}): {"warning": "Ashwagandha + Rhodiola: both adaptogens — may over-stimulate HPA axis. Consider using only one.", "severity": "low"},
        frozenset({"valerian", "melatonin"}): {"warning": "Valerian + Melatonin: additive sedation risk — use lowest doses, monitor grogginess", "severity": "medium"},
        frozenset({"guarana", "panax ginseng"}): {"warning": "Guarana + Ginseng: both stimulants — excessive CNS stimulation risk", "severity": "medium"},
        frozenset({"ginger", "blood thinner"}): {"warning": "Ginger may potentiate anticoagulant effects", "severity": "high"},
    }
    # Herb-drug interactions (checked against medications)
    HERB_DRUG_INTERACTIONS = {
        "ashwagandha": {"drugs": ["thyroid", "levothyroxine", "synthroid"], "warning": "Ashwagandha may affect thyroid hormone levels — monitor with thyroid medication", "severity": "high"},
        "rhodiola": {"drugs": ["ssri", "snri", "antidepressant", "sertraline", "fluoxetine", "citalopram", "escitalopram", "venlafaxine"], "warning": "Rhodiola may interact with serotonergic medications — serotonin syndrome risk", "severity": "high"},
        "valerian": {"drugs": ["benzodiazepine", "lorazepam", "diazepam", "alprazolam", "sedative", "zolpidem"], "warning": "Valerian + sedatives: additive CNS depression — excessive drowsiness risk", "severity": "high"},
        "ginseng": {"drugs": ["warfarin", "coumadin", "blood thinner", "anticoagulant"], "warning": "Ginseng may reduce anticoagulant efficacy", "severity": "high"},
        "curcumin": {"drugs": ["warfarin", "coumadin", "blood thinner", "anticoagulant"], "warning": "Curcumin may potentiate anticoagulant effects — monitor INR", "severity": "high"},
        "quercetin": {"drugs": ["warfarin", "coumadin", "blood thinner", "anticoagulant", "antiplatelet", "aspirin", "clopidogrel"], "warning": "Quercetin may potentiate anticoagulant/antiplatelet effects — auto-exclude", "severity": "high"},
        "ginger": {"drugs": ["warfarin", "coumadin", "blood thinner", "anticoagulant"], "warning": "Ginger may potentiate anticoagulant effects", "severity": "medium"},
        "guarana": {"drugs": ["lithium", "stimulant", "adhd", "methylphenidate", "amphetamine"], "warning": "Guarana (caffeine) may interact with stimulant/lithium medications", "severity": "high"},
    }
    all_selected = set()
    for vm in supplements.get("vitamins_minerals", []):
        all_selected.add(vm.get("substance", "").lower().split("(")[0].strip())
    for sp in supplements.get("supplements", []):
        all_selected.add(sp.get("substance", "").lower().split("(")[0].strip())
    # Also include deterministic components
    mg_needs = rule_outputs.get("magnesium", {})
    if mg_needs.get("capsules", 0) > 0:
        all_selected.add("magnesium")
    if rule_outputs.get("softgel", {}).get("include_softgel", False):
        all_selected.add("vitamin d")
        all_selected.add("vitamin e")

    conflict_found = False
    # Track which goal keywords each mineral addresses (for conflict resolution priority)
    MINERAL_GOAL_KEYWORDS = {
        "zinc": {"immune", "infection", "skin"},
        "calcium": {"bone", "longevity", "aging"},
        "iron": {"fatigue", "energy", "anemia"},
        "copper": {"skin", "antioxidant"},
        "magnesium": {"sleep", "stress", "sport", "muscle"},
    }
    ranked_goals_lower = [g.lower() for g in unified_input.get("questionnaire", {}).get("goals", {}).get("ranked", [])]

    for pair, conflict_info in KNOWN_CONFLICTS.items():
        sides_present = sum(1 for p in pair if any(p in s for s in all_selected))
        if sides_present >= 2:
            severity = conflict_info["severity"]
            icon = "🚨🚨🚨" if severity == "high" else "⚠️ " if severity == "medium" else "ℹ️ "
            print(f"  {icon} CONFLICT [{severity.upper()}]: {conflict_info['warning']}")
            conflict_found = True

            # Auto-resolve MEDIUM severity mineral absorption conflicts
            if severity == "medium" and not force_keep:
                pair_list = list(pair)
                # Score each mineral by how many client goals it supports
                scores = {}
                for mineral in pair_list:
                    goal_kws = MINERAL_GOAL_KEYWORDS.get(mineral, set())
                    score = sum(1 for g in ranked_goals_lower if any(kw in g for kw in goal_kws))
                    # Boost score for earlier-ranked goals
                    for i, g in enumerate(ranked_goals_lower):
                        if any(kw in g for kw in goal_kws):
                            score += (len(ranked_goals_lower) - i)  # Earlier goals score higher
                    scores[mineral] = score

                # Drop the mineral with lowest goal relevance
                sorted_minerals = sorted(scores.items(), key=lambda x: x[1])
                drop_mineral = sorted_minerals[0][0]
                keep_mineral = sorted_minerals[1][0]

                # ── LLM TIE-BREAKER: If scores are tied, ask the LLM ──
                if scores[drop_mineral] == scores[keep_mineral] and use_llm:
                    from llm_decisions import resolve_mineral_conflict
                    # Build dose/claim info for LLM context
                    mineral_claims = {}
                    mineral_doses = {}
                    for vm in supplements.get("vitamins_minerals", []):
                        vm_lower = vm.get("substance", "").lower()
                        for m in pair_list:
                            if m in vm_lower:
                                mineral_claims[m] = vm.get("rationale", "") or ", ".join(MINERAL_GOAL_KEYWORDS.get(m, set()))
                                mineral_doses[m] = vm.get("dose", f"{vm.get('dose_value', '?')}{vm.get('dose_unit', 'mg')}")

                    llm_keep = resolve_mineral_conflict(
                        mineral_a=pair_list[0],
                        mineral_a_dose=mineral_doses.get(pair_list[0], "?"),
                        mineral_a_claim=mineral_claims.get(pair_list[0], ", ".join(MINERAL_GOAL_KEYWORDS.get(pair_list[0], set()))),
                        mineral_b=pair_list[1],
                        mineral_b_dose=mineral_doses.get(pair_list[1], "?"),
                        mineral_b_claim=mineral_claims.get(pair_list[1], ", ".join(MINERAL_GOAL_KEYWORDS.get(pair_list[1], set()))),
                        client_goals=unified_input.get("questionnaire", {}).get("goals", {}).get("ranked", []),
                        use_bedrock=use_llm,
                    )
                    if llm_keep:
                        keep_mineral = llm_keep
                        drop_mineral = [m for m in pair_list if m != llm_keep][0]
                        print(f"    🧠 LLM tie-breaker: KEEP {keep_mineral.title()}, DROP {drop_mineral.title()} (scores tied at {scores[drop_mineral]})")

                # Remove from vitamins_minerals list
                before_count = len(supplements.get("vitamins_minerals", []))
                supplements["vitamins_minerals"] = [
                    vm for vm in supplements.get("vitamins_minerals", [])
                    if drop_mineral not in vm.get("substance", "").lower()
                ]
                after_count = len(supplements.get("vitamins_minerals", []))
                if before_count > after_count:
                    conflict_removed_names.add(drop_mineral)
                    print(f"    🗑️  AUTO-RESOLVED: Removed {drop_mineral.title()} (goal score {scores[drop_mineral]}) — "
                          f"keeping {keep_mineral.title()} (goal score {scores[keep_mineral]})")
                else:
                    print(f"    ℹ️  Note: {drop_mineral.title()} not in sachet vitamins — conflict may involve deterministic Mg capsule (spacing advised)")

    # D.2b-ext: Herb-drug interaction check with auto-removal for HIGH severity
    interaction_removed_names = set()  # Track auto-removed supplements
    if medications:
        for supp_key, interaction in HERB_DRUG_INTERACTIONS.items():
            # Check if this herb is in selected supplements
            herb_present = any(supp_key in s for s in all_selected)
            if herb_present:
                for drug_keyword in interaction["drugs"]:
                    if any(drug_keyword in med for med in med_names_lower):
                        severity = interaction["severity"]
                        icon = "🚨🚨🚨" if severity == "high" else "⚠️ "
                        print(f"  {icon} HERB-DRUG [{severity.upper()}]: {interaction['warning']} (medication: {drug_keyword})")
                        conflict_found = True
                        # Auto-remove HIGH severity herb-drug interactions (unless --force-keep)
                        if severity == "high" and not force_keep:
                            # Remove from supplements list (pre-routing)
                            before_vm = len(supplements.get("vitamins_minerals", []))
                            before_sp = len(supplements.get("supplements", []))
                            supplements["vitamins_minerals"] = [
                                vm for vm in supplements.get("vitamins_minerals", [])
                                if supp_key not in vm.get("substance", "").lower()
                            ]
                            supplements["supplements"] = [
                                sp for sp in supplements.get("supplements", [])
                                if supp_key not in sp.get("substance", "").lower()
                            ]
                            after_vm = len(supplements.get("vitamins_minerals", []))
                            after_sp = len(supplements.get("supplements", []))
                            removed_count = (before_vm - after_vm) + (before_sp - after_sp)
                            if removed_count > 0:
                                interaction_removed_names.add(supp_key)
                                print(f"    🗑️  AUTO-REMOVED: {supp_key} ({removed_count} item(s)) — high-severity herb-drug interaction")
                                print(f"       (override with --force-keep to keep this supplement)")
                        elif severity == "high" and force_keep:
                            print(f"    ⚠️  FORCE-KEPT: {supp_key} — --force-keep flag active, supplement retained despite HIGH interaction")
                        break  # One match per herb is enough

    if not conflict_found:
        _gate_passes.append("no conflicts")
    elif interaction_removed_names:
        print(f"  🗑️  Auto-removed {len(interaction_removed_names)} supplement(s) for safety: {interaction_removed_names}")

    # D.2c: Soft health claim redundancy check
    claim_to_supplements = {}
    for sp in supplements.get("supplements", []):
        claim = sp.get("health_claim", "")
        if claim:
            claim_to_supplements.setdefault(claim, []).append(sp.get("substance", "?"))
    for claim, supps in claim_to_supplements.items():
        if len(supps) > 1:
            print(f"  ℹ️  REDUNDANCY NOTE: {len(supps)} supplements for '{claim}': {supps} — verify complementary mechanisms")

    # D.2d: Post-LLM FODMAP classification enforcement
    # Known FODMAP substances must always be marked fodmap=true regardless of LLM output
    KNOWN_FODMAP_SUBSTANCES = {"lactulose", "gos", "fos", "inulin"}
    _fodmap_corrections = 0
    for pb in prebiotics.get("prebiotics", []):
        name_lower = pb.get("substance", "").lower().strip()
        if any(fodmap_name in name_lower for fodmap_name in KNOWN_FODMAP_SUBSTANCES):
            if not pb.get("fodmap", False):
                print(f"  🔧 FODMAP CORRECTION: '{pb['substance']}' forced to fodmap=true (was false)")
                pb["fodmap"] = True
                _fodmap_corrections += 1
    if _fodmap_corrections > 0:
        # Recalculate total FODMAP grams
        new_fodmap_total = sum(pb.get("dose_g", 0) for pb in prebiotics.get("prebiotics", []) if pb.get("fodmap", False))
        prebiotics["total_fodmap_grams"] = new_fodmap_total
        print(f"  ✅ Corrected {_fodmap_corrections} FODMAP classification(s). New total FODMAP: {new_fodmap_total}g")
    else:
        _gate_passes.append("FODMAP correct")

    # D.2e: Zinc dose — default to female dose (8mg) when sex is unknown/empty
    # Conservative approach: lower dose is safer when sex cannot be determined
    _zinc_sex = unified_input.get("questionnaire", {}).get("demographics", {}).get("biological_sex", "")
    if not _zinc_sex or _zinc_sex.lower() not in ("male", "female"):
        for vm in supplements.get("vitamins_minerals", []):
            if "zinc" in vm.get("substance", "").lower():
                if vm.get("dose_value", 0) > 8:
                    print(f"  🔧 ZINC DOSE: Sex unknown ('{_zinc_sex}') → defaulting to 8mg (female/conservative)")
                    vm["dose_value"] = 8
                    vm["dose"] = "8 mg/d"
                    vm["standard_dose"] = "8 mg/d"
                break


    # ── STAGE E: Weight Calculation ──────────────────────────────────────
    if _gate_passes:
        _gates_str = " · ".join(_gate_passes)
        print(f"  ✅ Gates: {_gates_str}")

    print("\n─── E. WEIGHTS & VALIDATION ────────────────────────────────")
    calc = FormulationCalculator(sample_id=sample_id)

    # Add probiotics — with capacity guard (650mg max = 65B CFU max)
    MAX_CAPSULE_CFU = 65  # 65B × 10 = 650mg = capsule capacity
    strains = mix.get("strains", [])
    if strains:
        # Calculate total CFU from LLM strains
        total_cfu_requested = sum(s.get("cfu_billions", 10) for s in strains)
        if total_cfu_requested * 10 > 650:
            # Scale down to fit capsule — redistribute evenly
            cfu_per = distribute_cfu_evenly(MAX_CAPSULE_CFU, len(strains))
            print(f"  ⚠️ Probiotic capacity guard: {total_cfu_requested}B CFU ({total_cfu_requested*10}mg) exceeds 650mg → redistributed to {cfu_per}B × {len(strains)} strains")
            for strain in strains:
                calc.add_probiotic(
                    strain["name"],
                    cfu_per,
                    mix_id=mix["mix_id"],
                    mix_name=mix["mix_name"],
                    rationale=strain.get("role", ""),
                )
        else:
            for strain in strains:
                calc.add_probiotic(
                    strain["name"],
                    strain.get("cfu_billions", 10),
                    mix_id=mix["mix_id"],
                    mix_name=mix["mix_name"],
                    rationale=strain.get("role", ""),
                )
    else:
        # Fallback: distribute evenly with placeholder names
        total_cfu = min(mix.get("total_cfu_billions", 50), MAX_CAPSULE_CFU)
        num_strains = 5  # Default
        cfu_per = distribute_cfu_evenly(total_cfu, num_strains)
        for i in range(num_strains):
            calc.add_probiotic(
                f"Mix {mix['mix_id']} Strain {i+1}",
                cfu_per,
                mix_id=mix["mix_id"],
                mix_name=mix["mix_name"],
            )

    # Add softgels only if client needs any component (decision-based, not automatic)
    softgel_decision = rule_outputs.get("softgel", {})
    if softgel_decision.get("include_softgel", False):
        calc.add_fixed_softgels(daily_count=softgel_decision["daily_count"])
        print(f"  Softgels: {softgel_decision['daily_count']}× fixed (needs: {softgel_decision['needs_identified']})")
        for r in softgel_decision.get("reasoning", []):
            print(f"    → {r}")
    else:
        print(f"  Softgels: NONE — no component need identified")
        if softgel_decision.get("contraindications"):
            print(f"    ⚠️ Contraindications: {softgel_decision['contraindications']}")

    # Add prebiotics to sachet
    calc.set_prebiotic_strategy(prebiotics.get("strategy", ""))
    for pb in prebiotics.get("prebiotics", []):
        calc.add_prebiotic(
            pb["substance"],
            pb["dose_g"],
            fodmap=pb.get("fodmap", False),
            rationale=pb.get("rationale", ""),
        )

    # Add condition-specific additions from prebiotic design (e.g., Apple polyphenols, Quercetin)
    for csa in prebiotics.get("condition_specific_additions", []):
        substance = csa.get("substance", "Unknown")
        rationale = csa.get("rationale", "")

        # ── GUARD 1: Skip LLM placeholder "no addition needed" entries ──
        # LLM returns {"substance": "None", "dose_g_or_mg": "0"} when no CSA is needed.
        # These must be silently skipped — not routed to any delivery format.
        if not substance or substance.strip().lower() in ("none", "n/a", "null", "", "unknown"):
            print(f"  · CSA skipped (placeholder): '{substance}' — LLM indicated no condition-specific addition needed")
            continue

        dose_str = str(csa.get("dose_g_or_mg", ""))
        # Robust dose parsing: extract numeric value with regex (handles LLM strings like "included in base 3.0g")
        _dose_num = re.search(r'([\d.]+)', dose_str)
        if not _dose_num:
            print(f"  ⚠️ CSA skipped (no numeric dose): {substance} — dose_str='{dose_str}'")
            continue
        _dose_val = float(_dose_num.group(1))

        # ── GUARD 2: Skip zero-dose entries ──
        # A dose of 0 means no actual substance — skip rather than routing "None 0mg" to capsule.
        if _dose_val == 0:
            print(f"  · CSA skipped (zero dose): {substance} — dose_g_or_mg='{dose_str}'")
            continue
        if "mg" in dose_str.lower():
            calc.add_sachet_supplement(substance, _dose_val, rationale=rationale)
            print(f"  Condition-specific addition: {substance} {_dose_val}mg → sachet ({csa.get('condition', '')})")
        elif "g" in dose_str.lower():
            calc.add_prebiotic(substance, _dose_val, fodmap=False, rationale=rationale)
            print(f"  Condition-specific addition: {substance} {_dose_val}g → sachet prebiotic ({csa.get('condition', '')})")
        else:
            # Default to mg if unit unclear
            calc.add_sachet_supplement(substance, _dose_val, rationale=rationale)
            print(f"  Condition-specific addition: {substance} {_dose_val}mg → sachet (unit assumed mg) ({csa.get('condition', '')})")

    # ── Load heavy botanical threshold from KB ───────────────────────────
    _heavy_threshold_mg = _dfr_kb.get("heavy_botanical_threshold_mg", 650)

    # Add vitamins/minerals → morning pooled capsules (or softgel if fat-soluble)
    # Load KB once for min/max dose lookup (used by CapsuleStackingOptimizer)
    try:
        _vm_kb_all = json.load(open(KB_DIR / "vitamins_minerals.json", 'r', encoding='utf-8'))
    except Exception:
        _vm_kb_all = {}

    for vm in supplements.get("vitamins_minerals", []):
        delivery = vm.get("delivery", "morning_wellness_capsule")
        if delivery in ("sachet", "morning_wellness_capsule"):
            _vm_kb_entry = None
            _vm_name_lower = vm.get("substance", "").lower()
            for _kbe in _vm_kb_all.get("vitamins_and_minerals", []):
                if _vm_name_lower in _kbe.get("substance", "").lower() or _kbe.get("substance", "").lower() in _vm_name_lower:
                    _vm_kb_entry = _kbe.get("parsed", {}).get("dose", {})
                    break
            _min_val = _vm_kb_entry.get("min") if _vm_kb_entry and "min" in _vm_kb_entry else None
            _max_val = _vm_kb_entry.get("max") if _vm_kb_entry and "max" in _vm_kb_entry else None
            ther_tag = " [THERAPEUTIC]" if vm.get("therapeutic") else ""
            adj_tag = " [fixed — not adjustable]" if vm.get("therapeutic") else (f" [adjustable {_min_val}–{_max_val}{vm.get('dose_unit','mg')}]" if _min_val and _max_val else "")
            calc.add_morning_pooled_component(
                substance=vm["substance"],
                dose_value=vm.get("dose_value", 0),
                dose_unit=vm.get("dose_unit", "mg"),
                min_dose_value=_min_val,
                max_dose_value=_max_val,
                adjustable=not vm.get("therapeutic", False),
                therapeutic=vm.get("therapeutic", False),
                standard_dose=vm.get("standard_dose", ""),
                rationale=vm.get("rationale", ""),
                clinical_note=vm.get("interaction_note", ""),
                informed_by=vm.get("informed_by", "questionnaire"),
                source_type="vitamin_mineral",
            )

    # Add supplements → route based on delivery field
    for supp in supplements.get("supplements", []):
        delivery = supp.get("delivery", "morning_wellness_capsule")
        dose_mg = supp.get("dose_mg", 0)
        if delivery == "jar":
            # Heavy non-bitter botanical → jar (dose > threshold)
            dose_g = round(dose_mg / 1000, 3)
            calc.add_jar_botanical(supp["substance"], dose_g, rationale=supp.get("rationale", ""))
        elif delivery in ("sachet", "morning_wellness_capsule"):
            # Light non-bitter botanical → morning pooled capsules (dose ≤ threshold)
            calc.add_light_botanical_to_morning(
                supp["substance"], dose_mg, rationale=supp.get("rationale", "")
            )
        elif delivery == "evening_capsule":
            # LLM-selected supplements or Tier 1 polyphenols routed to evening capsule
            calc.add_evening_component(
                supp["substance"],
                dose_mg,
                rationale=supp.get("rationale", ""),
            )
            tier_tag = " [Tier 1 polyphenol]" if supp.get("_polyphenol_tier") == 1 else ""
        elif delivery == "polyphenol_capsule":
            # Tier 2 polyphenols get dedicated morning capsule
            calc.add_polyphenol_capsule(
                supp["substance"],
                dose_mg,
                rationale=supp.get("rationale", ""),
                timing="morning",
            )

    # Evening capsule capacity guard for LLM-assigned supplements (pre-sleep check)
    # Smart resolution: reduce doses → split into 2 evening capsules if needed
    from weight_calculator import EVENING_CAPSULE_CAPACITY_MG
    _evening_kb_lookup = _load_supplement_kb_lookup()
    
    def _find_evening_kb(name):
        nl = name.lower().strip()
        if nl in _evening_kb_lookup:
            return _evening_kb_lookup[nl]
        for k, v in _evening_kb_lookup.items():
            if k in nl or nl in k:
                return v
        return None
    
    llm_evening_total = sum(c.get("dose_mg", 0) for c in calc.evening_components)
    if llm_evening_total > EVENING_CAPSULE_CAPACITY_MG:
        
        # Save original doses BEFORE any reduction (for restore if split needed)
        import copy
        _original_evening = copy.deepcopy(calc.evening_components)
        
        # Step 1: Try reducing evening doses to KB minimums (single capsule attempt)
        for comp in calc.evening_components:
            kb = _find_evening_kb(comp["substance"])
            if kb and kb["min_dose_mg"] is not None and comp["dose_mg"] > kb["min_dose_mg"]:
                saved = comp["dose_mg"] - kb["min_dose_mg"]
                comp["_original_dose_mg"] = comp["dose_mg"]  # Tag for rebalancing restoration
                comp["dose_mg"] = kb["min_dose_mg"]
                comp["weight_mg"] = kb["min_dose_mg"]
        
        llm_evening_total = sum(c.get("dose_mg", 0) for c in calc.evening_components)
        if llm_evening_total <= EVENING_CAPSULE_CAPACITY_MG:
            pass
        else:
            # Step 2: Can't fit in 1 capsule — REVERT to original doses and split into 2
            calc.evening_components = _original_evening  # Restore original therapeutic doses
            llm_evening_total = sum(c.get("dose_mg", 0) for c in calc.evening_components)
            
            capsule1 = []
            capsule2_overflow = []
            cap1_total = 0
            for comp in calc.evening_components:
                if cap1_total + comp["dose_mg"] <= EVENING_CAPSULE_CAPACITY_MG:
                    capsule1.append(comp)
                    cap1_total += comp["dose_mg"]
                else:
                    capsule2_overflow.append(comp)
            
            if capsule2_overflow:
                calc.evening_components = capsule1
                # Add overflow to a second evening capsule via the existing mechanism
                if not hasattr(calc, 'evening_capsule_2'):
                    calc.evening_capsule_2 = []
                for comp in capsule2_overflow:
                    calc.evening_capsule_2.append(comp)
                cap2_total = sum(c["dose_mg"] for c in capsule2_overflow)
                print(f"  ⚠️ Evening overflow: {llm_evening_total}mg → 2 capsules (cap: {EVENING_CAPSULE_CAPACITY_MG}mg each)")
            else:
                pass

    # Add deterministic sleep supplements — route to correct delivery unit based on timing
    sleep_supps = rule_outputs.get("sleep_supplements", {})
    timing_assignments = rule_outputs.get("timing", {}).get("timing_assignments", {})
    if sleep_supps.get("supplements"):
        evening_candidates = []
        print(f"  Sleep supplements ({len(sleep_supps['supplements'])} selected):")
        for ss in sleep_supps["supplements"]:
            # Check timing assignment for this substance
            substance_key = ss["substance"].lower().replace("-", "_").replace(" ", "_")
            timing_info = timing_assignments.get(substance_key, {})
            assigned_timing = timing_info.get("timing", "morning")

            if assigned_timing == "evening":
                evening_candidates.append(ss)
                escalation_note = ""
                if "theanine" in ss["substance"].lower() and ss["dose_mg"] > 200:
                    escalation_note = " [ESCALATED: stress≥7 + sleep≤5 → 400mg per KB rule]"
                print(f"    → {ss['substance']}: {ss['dose_mg']}mg → EVENING capsule ({timing_info.get('reason', '')}){escalation_note}")
            else:
                # Morning sleep supplement → morning pooled capsules (light)
                print(f"    → {ss['substance']}: {ss['dose_mg']}mg → morning wellness capsule (sleep/timing: morning)")
                calc.add_light_botanical_to_morning(ss["substance"], ss["dose_mg"], rationale=ss.get("rationale", ""))

        # Evening capsule capacity guard (650mg max)
        # Must account for LLM supplements already added to evening (e.g., Ashwagandha)
        if evening_candidates:
            existing_evening_mg = sum(c.get("dose_mg", 0) for c in calc.evening_components)
            total_evening_mg = existing_evening_mg + sum(c["dose_mg"] for c in evening_candidates)
            if total_evening_mg > 650:
                print(f"  ⚠️ Evening capsule capacity guard: {total_evening_mg}mg exceeds 650mg (existing: {existing_evening_mg}mg + sleep: {total_evening_mg - existing_evening_mg}mg)")
                # Clamp L-Theanine to 200mg if capacity exceeded
                for c in evening_candidates:
                    if "theanine" in c["substance"].lower() and c["dose_mg"] > 200:
                        print(f"    → L-Theanine clamped {c['dose_mg']}mg → 200mg")
                        c["dose_mg"] = 200
                # Re-check after clamping
                total_after_clamp = existing_evening_mg + sum(c["dose_mg"] for c in evening_candidates)
                if total_after_clamp > 650:
                    print(f"  ⚠️ Evening still {total_after_clamp}mg after clamping — splitting sleep supplements to evening capsule 2...")
            # Add sleep supplements — overflow to capsule 2 if needed
            for c in evening_candidates:
                current_evening_mg = sum(comp.get("dose_mg", 0) for comp in calc.evening_components)
                if current_evening_mg + c["dose_mg"] <= EVENING_CAPSULE_CAPACITY_MG:
                    calc.add_evening_component(c["substance"], c["dose_mg"], rationale=c.get("rationale", ""))
                else:
                    # Overflow to evening capsule 2
                    if not hasattr(calc, 'evening_capsule_2'):
                        calc.evening_capsule_2 = []
                    calc.evening_capsule_2.append({
                        "substance": c["substance"], "dose_mg": c["dose_mg"],
                        "weight_mg": round(c["dose_mg"], 2), "rationale": c.get("rationale", ""),
                    })
                    print(f"    → {c['substance']}: {c['dose_mg']}mg → evening capsule 2 (overflow)")

    # Add Magnesium bisglycinate capsules (timing from timing engine — separate from sachet/other capsules)
    # ── MEDICATION RULE: Suppress magnesium if KB rule removed it (e.g., Ramipril interaction) ──
    mg_needs = rule_outputs.get("magnesium", {})
    if _med_magnesium_removed:
        print(f"  💊 Mg capsules: SUPPRESSED by medication interaction rule (KB rule)")
        for _rr in med_rules_result.get("removal_reasons", []):
            if "magnesium" in _rr.get("substance", "").lower():
                print(f"    → {_rr.get('medication', '?')}: {_rr.get('mechanism', '')}")
                print(f"    → Reason: {_rr.get('reason', '')}")
        # Zero out magnesium in rule_outputs so downstream consumers see 0 capsules
        rule_outputs["magnesium"]["capsules"] = 0
        rule_outputs["magnesium"]["mg_bisglycinate_total_mg"] = 0
        rule_outputs["magnesium"]["elemental_mg_total_mg"] = 0
        rule_outputs["magnesium"]["needs_identified"] = []
        rule_outputs["magnesium"]["reasoning"] = ["SUPPRESSED: medication interaction rule"]
        _trace_events.append({
            "type": "medication_rule",
            "substance": "magnesium",
            "description": "Magnesium SUPPRESSED — KB medication interaction rule (Tier B conditional removal)"
        })
    elif mg_needs.get("capsules", 0) > 0:
        mg_timing_info = rule_outputs.get("timing", {}).get("timing_assignments", {}).get("magnesium", {})
        mg_timing = mg_timing_info.get("timing", "evening")
        calc.add_magnesium_capsules(
            mg_needs["capsules"],
            needs=mg_needs.get("needs_identified", []),
            reasoning=mg_needs.get("reasoning", []),
            timing=mg_timing
        )
        print(f"  Mg capsules: {mg_needs['capsules']}× {mg_timing} ({mg_needs['mg_bisglycinate_total_mg']}mg bisglycinate = {mg_needs['elemental_mg_total_mg']}mg elemental)")

    # ── FINAL EVENING REBALANCING ────────────────────────────────────────
    # After ALL evening components are assigned (LLM supplements + sleep supplements),
    # rebalance across capsules to maximize therapeutic doses.
    # Algorithm: collect all → restore full doses → balanced bin-pack → per-capsule reduce
    _ec2_list = getattr(calc, 'evening_capsule_2', [])
    _all_evening = calc.evening_components + _ec2_list
    _has_reduced = any(c.get("_original_dose_mg") for c in _all_evening)
    if len(_all_evening) > 1 and (_ec2_list or _has_reduced):
        # Only rebalance if there's a split OR dose reductions happened
        # Step 1: Restore reduced doses to originals before rebalancing
        for comp in _all_evening:
            orig = comp.get("_original_dose_mg")
            if orig and orig > comp.get("dose_mg", 0):
                print(f"  🔄 Restoring {comp['substance']}: {comp['dose_mg']}mg → {orig}mg (original)")
                comp["dose_mg"] = orig
                comp["weight_mg"] = orig
                del comp["_original_dose_mg"]

        _total_evening_mg = sum(c.get("dose_mg", 0) for c in _all_evening)
        if _total_evening_mg > EVENING_CAPSULE_CAPACITY_MG:

            # Step 2: Balanced bin-pack (largest-first, assign to capsule with more headroom)
            _all_evening.sort(key=lambda c: -c.get("dose_mg", 0))  # Largest first
            cap1, cap2 = [], []
            cap1_mg, cap2_mg = 0, 0
            for comp in _all_evening:
                dose = comp.get("dose_mg", 0)
                if cap1_mg + dose <= EVENING_CAPSULE_CAPACITY_MG:
                    cap1.append(comp)
                    cap1_mg += dose
                elif cap2_mg + dose <= EVENING_CAPSULE_CAPACITY_MG:
                    cap2.append(comp)
                    cap2_mg += dose
                elif cap1_mg <= cap2_mg:
                    cap1.append(comp)
                    cap1_mg += dose
                else:
                    cap2.append(comp)
                    cap2_mg += dose

            # Step 3: Per-capsule reduction if over 650mg
            for cap_name, cap_list in [("cap1", cap1), ("cap2", cap2)]:
                cap_total = sum(c.get("dose_mg", 0) for c in cap_list)
                if cap_total > EVENING_CAPSULE_CAPACITY_MG:
                    overage = cap_total - EVENING_CAPSULE_CAPACITY_MG
                    # Sort by priority — reduce lowest-rank first
                    for comp in sorted(cap_list, key=lambda c: -(_find_evening_kb(c["substance"]) or {}).get("rank_priority", 3)):
                        if overage <= 0:
                            break
                        kb = _find_evening_kb(comp["substance"])
                        if kb and kb.get("min_dose_mg") is not None and comp["dose_mg"] > kb["min_dose_mg"]:
                            can_save = comp["dose_mg"] - kb["min_dose_mg"]
                            reduce_by = min(overage, can_save)
                            old_dose = comp["dose_mg"]
                            comp["dose_mg"] = comp["dose_mg"] - reduce_by
                            comp["weight_mg"] = comp["dose_mg"]
                            overage -= reduce_by

            # Apply rebalanced capsules
            calc.evening_components = cap1
            if cap2:
                calc.evening_capsule_2 = cap2
            else:
                calc.evening_capsule_2 = []

            cap1_total = sum(c.get("dose_mg", 0) for c in cap1)
            cap2_total = sum(c.get("dose_mg", 0) for c in cap2)
            cap1_names = ", ".join(f"{c['substance']} {c['dose_mg']}mg" for c in cap1)
            cap2_names = ", ".join(f"{c['substance']} {c['dose_mg']}mg" for c in cap2)
            if cap2:
                pass  # cap2 has components — kept separate
            else:
                calc.evening_capsule_2 = []
    
    # ── CROSS-CAPSULE EVENING DEDUPLICATION ──────────────────────────────
    # LLM can select the same substance twice (e.g., Propolis 250mg + Propolis 300mg).
    # After rebalancing, deduplicate across caps 1+2: keep higher dose, drop duplicate.
    _ec2_for_dedup = getattr(calc, 'evening_capsule_2', [])
    _all_eve_for_dedup = calc.evening_components + _ec2_for_dedup
    if len(_all_eve_for_dedup) > 1:
        _seen_eve_substances = {}
        _deduped_eve_all = []
        _eve_dedup_count = 0
        for comp in _all_eve_for_dedup:
            key = comp["substance"].lower().strip()
            if key in _seen_eve_substances:
                existing = _seen_eve_substances[key]
                if comp["dose_mg"] > existing["dose_mg"]:
                    _deduped_eve_all.remove(existing)
                    _deduped_eve_all.append(comp)
                    _seen_eve_substances[key] = comp
                    print(f"  🔄 Evening cross-capsule dedup: {comp['substance']} — kept {comp['dose_mg']}mg, dropped {existing['dose_mg']}mg (duplicate)")
                else:
                    print(f"  🔄 Evening cross-capsule dedup: {comp['substance']} — kept {existing['dose_mg']}mg, dropped {comp['dose_mg']}mg (duplicate)")
                _eve_dedup_count += 1
            else:
                _seen_eve_substances[key] = comp
                _deduped_eve_all.append(comp)

        if _eve_dedup_count > 0:
            print(f"  🔄 Removed {_eve_dedup_count} cross-capsule duplicate(s) — re-splitting...")
            # Re-split into capsules using balanced bin-pack (same logic as rebalancing)
            _deduped_eve_all.sort(key=lambda c: -c.get("dose_mg", 0))
            _d_cap1, _d_cap2 = [], []
            _d_cap1_mg, _d_cap2_mg = 0, 0
            for comp in _deduped_eve_all:
                dose = comp.get("dose_mg", 0)
                if _d_cap1_mg + dose <= EVENING_CAPSULE_CAPACITY_MG:
                    _d_cap1.append(comp)
                    _d_cap1_mg += dose
                elif _d_cap2_mg + dose <= EVENING_CAPSULE_CAPACITY_MG:
                    _d_cap2.append(comp)
                    _d_cap2_mg += dose
                elif _d_cap1_mg <= _d_cap2_mg:
                    _d_cap1.append(comp)
                    _d_cap1_mg += dose
                else:
                    _d_cap2.append(comp)
                    _d_cap2_mg += dose
            calc.evening_components = _d_cap1
            calc.evening_capsule_2 = _d_cap2
            if _d_cap2:
                print(f"    ✅ After dedup: cap1={_d_cap1_mg}mg, cap2={_d_cap2_mg}mg")
            else:
                print(f"    ✅ After dedup: all fit in 1 capsule ({_d_cap1_mg}mg)")

    # ── FINAL SAFETY CLAMP: Evening capsule 2 > 650mg ───────────────────
    # After rebalancing + dedup, capsule 2 can still exceed 650mg if components
    # are too large. Apply same logic as capsule 1: reduce to KB mins → drop.
    _ec2_clamp = getattr(calc, 'evening_capsule_2', [])
    if _ec2_clamp:
        _ec2_clamp_total = sum(c.get("dose_mg", 0) for c in _ec2_clamp)
        if _ec2_clamp_total > EVENING_CAPSULE_CAPACITY_MG:
            _ec2_overage = _ec2_clamp_total - EVENING_CAPSULE_CAPACITY_MG
            print(f"  ⚠️ Evening capsule 2 safety clamp: {_ec2_clamp_total}mg > {EVENING_CAPSULE_CAPACITY_MG}mg (overage {_ec2_overage}mg)")
            # Step A: Reduce lowest-priority components to KB minimums
            for comp in sorted(_ec2_clamp, key=lambda c: -(_find_evening_kb(c["substance"]) or {}).get("rank_priority", 3)):
                if _ec2_overage <= 0:
                    break
                kb = _find_evening_kb(comp["substance"])
                if kb and kb.get("min_dose_mg") is not None and comp["dose_mg"] > kb["min_dose_mg"]:
                    can_save = comp["dose_mg"] - kb["min_dose_mg"]
                    reduce_by = min(_ec2_overage, can_save)
                    old_dose = comp["dose_mg"]
                    comp["dose_mg"] -= reduce_by
                    comp["weight_mg"] = comp["dose_mg"]
                    _ec2_overage -= reduce_by
                    print(f"    → Clamp reduce: {comp['substance']}: {old_dose}mg → {comp['dose_mg']}mg (saved {reduce_by}mg)")
            # Step B: If still over, drop lowest-priority components entirely
            if _ec2_overage > 0:
                _ec2_clamp.sort(key=lambda c: -(_find_evening_kb(c["substance"]) or {}).get("rank_priority", 3))
                while _ec2_overage > 0 and _ec2_clamp:
                    drop = _ec2_clamp.pop(0)
                    _ec2_overage -= drop["dose_mg"]
                    evening_overflow_dropped.add(drop["substance"].lower())
                    print(f"    → Clamp drop: {drop['substance']} ({drop['dose_mg']}mg) — evening capsule 2 overflow")
            calc.evening_capsule_2 = _ec2_clamp
            _ec2_final = sum(c.get("dose_mg", 0) for c in _ec2_clamp)
            print(f"    ✅ Evening capsule 2 clamped to {_ec2_final}mg")

    # ── EVENING CAPSULE DOSE OPTIMIZATION (JSON-driven) ──────────────────
    # The DoseOptimizer is the ONLY layer allowed to change upstream-selected doses.
    # Rules are in knowledge_base/dose_optimization_rules.json (clinical policy outside code).
    # After optimization, unused capsule space is filled with MCC excipient.
    _dose_optimizer = DoseOptimizer()
    
    # Collect ALL evening components (cap1 + cap2) for optimizer
    _ec2_pre_opt = getattr(calc, 'evening_capsule_2', [])
    _all_evening_for_opt = calc.evening_components + _ec2_pre_opt
    
    if _all_evening_for_opt:
        opt_result = _dose_optimizer.optimize(_all_evening_for_opt)
        for log_line in opt_result["log"]:
            print(log_line)
        
        if opt_result["applied_rules"]:
            print(f"  ✅ Optimization rules applied: {opt_result['applied_rules']}")
            # After optimization, re-split into capsules (components may now fit in 1)
            _optimized = opt_result["components"]
            _opt_total = sum(c.get("dose_mg", 0) for c in _optimized)
            if _opt_total <= EVENING_CAPSULE_CAPACITY_MG:
                # All fit in capsule 1 — no capsule 2 needed
                calc.evening_components = _optimized
                calc.evening_capsule_2 = []
                print(f"  ✅ All evening components fit in 1 capsule ({_opt_total}mg) — capsule 2 eliminated")
            else:
                # Still need 2 capsules — re-split
                _optimized.sort(key=lambda c: -c.get("dose_mg", 0))
                _opt_cap1, _opt_cap2 = [], []
                _opt_cap1_mg, _opt_cap2_mg = 0, 0
                for comp in _optimized:
                    dose = comp.get("dose_mg", 0)
                    if _opt_cap1_mg + dose <= EVENING_CAPSULE_CAPACITY_MG:
                        _opt_cap1.append(comp)
                        _opt_cap1_mg += dose
                    else:
                        _opt_cap2.append(comp)
                        _opt_cap2_mg += dose
                calc.evening_components = _opt_cap1
                calc.evening_capsule_2 = _opt_cap2
                print(f"  ✅ Re-split after optimization: cap1={_opt_cap1_mg}mg, cap2={_opt_cap2_mg}mg")
        else:
            pass  # No optimization rules matched — doses preserved as-is

    # ── UNIVERSAL SMALL-CAPSULE MERGE ────────────────────────────────────
    # If a secondary capsule (evening capsule 2, or any overflow capsule) has
    # very little total weight (≤ MERGE_THRESHOLD_MG), it's wasteful to use a
    # whole 650mg capsule shell for it. Instead, shave a tiny amount from the
    # primary capsule's largest components and absorb the small capsule's
    # contents into it. This is clinically negligible (e.g., 6mg from 450mg =
    # 1.3%) but eliminates absurd packaging waste.
    #
    # Applies to ALL capsule pairs: evening cap1+cap2, polyphenol, etc.
    MERGE_THRESHOLD_MG = 50  # Max total weight in secondary capsule to trigger merge

    def _try_merge_small_capsule(primary_components, secondary_components, cap_name, capacity_mg=EVENING_CAPSULE_CAPACITY_MG):
        """Attempt to merge a tiny secondary capsule into the primary one.

        Algorithm:
          1. Check if secondary total ≤ MERGE_THRESHOLD_MG
          2. Calculate how much space to free in primary (need = secondary total)
          3. Shave proportionally from primary's largest components (respect KB mins)
          4. Move secondary contents into primary, return True if merged

        Returns (merged: bool, primary: list, secondary: list)
        """
        if not secondary_components:
            return False, primary_components, secondary_components

        secondary_total = sum(c.get("dose_mg", 0) for c in secondary_components)
        if secondary_total > MERGE_THRESHOLD_MG:
            return False, primary_components, secondary_components
        if secondary_total <= 0:
            return False, primary_components, secondary_components

        primary_total = sum(c.get("dose_mg", 0) for c in primary_components)

        # Check if there's already room
        headroom = capacity_mg - primary_total
        if headroom >= secondary_total:
            # Already fits — just merge
            print(f"    → {cap_name}: secondary ({secondary_total}mg) fits in existing headroom ({headroom}mg) — merging directly")
            primary_components.extend(secondary_components)
            return True, primary_components, []

        # Need to shave (secondary_total - headroom) mg from primary
        need_to_shave = secondary_total - headroom
        print(f"    → {cap_name}: secondary={secondary_total}mg, primary headroom={headroom}mg, need to shave={need_to_shave}mg")

        # Sort primary by dose descending — shave from largest first
        shave_candidates = sorted(primary_components, key=lambda c: -c.get("dose_mg", 0))

        total_shaved = 0
        shave_plan = []
        for comp in shave_candidates:
            if total_shaved >= need_to_shave:
                break
            current_dose = comp.get("dose_mg", 0)
            # Find KB minimum for this substance
            kb_entry = _find_evening_kb(comp.get("substance", ""))
            kb_min = kb_entry.get("min_dose_mg") if kb_entry and kb_entry.get("min_dose_mg") is not None else current_dose * 0.9  # Default: don't go below 90%
            max_shaveable = current_dose - kb_min
            if max_shaveable <= 0:
                continue
            shave_amount = min(need_to_shave - total_shaved, max_shaveable)
            if shave_amount > 0:
                shave_plan.append((comp, shave_amount))
                total_shaved += shave_amount

        if total_shaved < need_to_shave:
            # Can't shave enough without violating KB minimums — abort
            print(f"    ✗ {cap_name}: Cannot shave enough ({total_shaved}mg < {need_to_shave}mg needed) — keeping split")
            return False, primary_components, secondary_components

        # Execute shave plan
        for comp, shave_amt in shave_plan:
            old_dose = comp["dose_mg"]
            comp["dose_mg"] = round(comp["dose_mg"] - shave_amt, 2)
            comp["weight_mg"] = comp["dose_mg"]
            print(f"      · {comp['substance']}: {old_dose}mg → {comp['dose_mg']}mg (shaved {shave_amt}mg)")

        # Merge secondary into primary
        for sec_comp in secondary_components:
            print(f"      + Absorbed: {sec_comp['substance']} {sec_comp.get('dose_mg', 0)}mg")
        primary_components.extend(secondary_components)

        new_total = sum(c.get("dose_mg", 0) for c in primary_components)
        print(f"    ✅ {cap_name}: merged! New total={new_total}mg / {capacity_mg}mg capacity")
        return True, primary_components, []

    # Apply to evening capsule 1 + 2
    _ec2_merge = getattr(calc, 'evening_capsule_2', [])
    if _ec2_merge:
        _ec2_merge_total = sum(c.get("dose_mg", 0) for c in _ec2_merge)
        if _ec2_merge_total <= MERGE_THRESHOLD_MG:
            merged, calc.evening_components, remaining = _try_merge_small_capsule(
                calc.evening_components, _ec2_merge, "Evening cap1←cap2"
            )
            if merged:
                calc.evening_capsule_2 = []
            else:
                calc.evening_capsule_2 = remaining
        else:
            pass
    else:
        pass

    # Apply to polyphenol capsule — check if it's tiny and could merge with morning probiotic
    # (Skip for now — polyphenol capsules are typically 500+mg, unlikely to hit threshold)


    # ── Mandatory Supplement Presence Check (deterministic add-back) ──────
    # If goal-triggered mandatory items are missing from the formulation, add them
    goal_triggered = rule_outputs.get("goal_triggered_supplements", {})
    _existing_vm_names = {v["substance"].lower().split("(")[0].strip() for v in calc.sachet_vitamins}
    _existing_sp_names = {s["substance"].lower() for s in calc.sachet_supplements}
    
    # Load KB once (cached outside loop)
    _kb_vms = json.load(open(KB_DIR / "vitamins_minerals.json", 'r', encoding='utf-8')) if (KB_DIR / "vitamins_minerals.json").exists() else {}
    
    # Add missing mandatory vitamins (B9, B12, Vitamin C for energy goal)
    for mv in goal_triggered.get("mandatory_vitamins", []):
        mv_name = mv["substance"].lower()
        if not any(mv_name.split("(")[0].strip() in existing or existing in mv_name for existing in _existing_vm_names):
            _kb_dose = None
            for _kbv in _kb_vms.get("vitamins_and_minerals", []):
                if mv_name.split("(")[0].strip() in _kbv.get("substance", "").lower() or _kbv.get("id", "") in mv_name:
                    _kb_dose = _kbv.get("parsed", {}).get("dose", {})
                    _kb_substance = _kbv.get("substance", mv["substance"])
                    break
            if _kb_dose:
                _dose_val = _kb_dose.get("value", 0)
                _dose_unit = _kb_dose.get("unit", "mg")
                calc.add_sachet_vitamin(_kb_substance, _dose_val, _dose_unit, rationale=mv["reason"])
                print(f"  📌 MANDATORY ADD-BACK: {_kb_substance} ({_dose_val}{_dose_unit}) — {mv['reason']}")
            else:
                print(f"  ⚠️ Mandatory vitamin {mv['substance']} not found in KB — skipped")
    
    # Add missing mandatory supplements (if any deterministic rules require them)
    for ms in goal_triggered.get("mandatory_supplements", []):
        ms_name = ms["substance"].lower()
        if ms_name not in _existing_sp_names:
            calc.add_sachet_supplement(ms["substance"], ms.get("dose_mg", 250), rationale=ms["reason"])
            print(f"  📌 MANDATORY ADD-BACK: {ms['substance']} ({ms.get('dose_mg', 250)}mg) — {ms['reason']}")

    # ── Global deduplication across ALL delivery units ────────────────────
    # No substance should appear twice within the same delivery unit.
    # Priority: prebiotics > vitamins > supplements (keep first occurrence)
    
    # Sachet: deduplicate across prebiotics, vitamins, supplements
    # Prebiotics use PARTIAL name matching to catch variants like
    # "Beta-glucans" vs "Beta-glucans (additional metabolic support)"
    seen_sachet = set()
    seen_prebiotic_base_names = set()  # For partial name matching
    deduped_prebiotics = []
    for p in calc.sachet_prebiotics:
        key = p["substance"].lower()
        # Extract base name: strip parenthetical qualifiers for matching
        import re as _re_dedup
        base_name = _re_dedup.sub(r'\s*\(.*?\)\s*', '', key).strip()
        if key in seen_sachet or base_name in seen_prebiotic_base_names:
            print(f"    → Removed duplicate prebiotic: {p['substance']} ({p['dose_g']}g) — base name '{base_name}' already present")
        else:
            seen_sachet.add(key)
            seen_prebiotic_base_names.add(base_name)
            deduped_prebiotics.append(p)
    calc.sachet_prebiotics = deduped_prebiotics

    # Post-dedup: enforce prebiotic range max from rules engine
    _pb_range = rule_outputs.get("prebiotic_range", {})
    _pb_max_g = _pb_range.get("max_g", 99)
    _pb_actual_g = sum(p["dose_g"] for p in calc.sachet_prebiotics)
    if _pb_actual_g > _pb_max_g + 0.5:
        print(f"  ⚠️ Prebiotic total {_pb_actual_g}g exceeds max {_pb_max_g}g — clamping...")
        # Scale down all prebiotics proportionally to fit max
        _scale = _pb_max_g / _pb_actual_g
        for p in calc.sachet_prebiotics:
            old_dose = p["dose_g"]
            p["dose_g"] = round(old_dose * _scale, 2)
            p["weight_g"] = p["dose_g"]
            if old_dose != p["dose_g"]:
                print(f"    → {p['substance']}: {old_dose}g → {p['dose_g']}g")
        _pb_new = sum(p["dose_g"] for p in calc.sachet_prebiotics)
        print(f"    ✅ Prebiotic total clamped: {_pb_actual_g}g → {_pb_new}g (max={_pb_max_g}g)")

    # Morning pooled capsules — deduplicate as a single unified list
    # (vitamins + minerals + light botanicals all live in morning_pooled_components;
    # the old sachet_vitamins + sachet_supplements aliases both point to the same list,
    # so iterating them separately would process every item twice)
    deduped_morning = []
    for item in calc.morning_pooled_components:
        key = item["substance"].lower()
        if key in seen_sachet:
            dose_str = item.get("dose", item.get("dose_mg", "?"))
            print(f"    → Removed duplicate morning component: {item['substance']} ({dose_str})")
        else:
            seen_sachet.add(key)
            deduped_morning.append(item)
    calc.morning_pooled_components = deduped_morning

    # Evening pooled capsules — deduplicate
    seen_evening = set()
    deduped_evening = []
    for e in calc.evening_pooled_components:
        key = e["substance"].lower()
        if key in seen_evening:
            print(f"    → Removed duplicate evening component: {e['substance']} ({e['dose_mg']}mg)")
        else:
            seen_evening.add(key)
            deduped_evening.append(e)
    calc.evening_pooled_components = deduped_evening

    # ── Sachet capacity guard: smart overflow resolution ─────────────────
    # 4-step algorithm: reduce doses → reroute to evening → drop redundant → alert
    capacity_trimmed_names = _resolve_sachet_overflow(calc, supplements)

    # ── Consolidate evening_capsule_2 → evening_pooled_components ────────
    # evening_capsule_2 is a transient ad-hoc attribute set during the overflow
    # split logic above.  FormulationCalculator.generate() only processes
    # evening_pooled_components (capsule 1), so capsule 2 contents are invisible
    # to weight calculation, unit counts, and all downstream JSON outputs unless
    # we merge them back here — BEFORE calling calc.generate().
    # generate() → _calc_pooled_evening_totals() → CapsuleStackingOptimizer will
    # then correctly pack all components into N capsules (2 in overflow cases).
    _ec2_to_merge = getattr(calc, 'evening_capsule_2', [])
    if _ec2_to_merge:
        calc.evening_pooled_components.extend(_ec2_to_merge)
        calc.evening_capsule_2 = []

    # ── Build Component Registry — single source of truth ────────────────
    # Built from the ACTUAL formulation calculator state (post-dedup, post-trim)
    # Every downstream consumer (rationale table, dashboard, source %) reads this
    component_registry = _build_component_registry(
        calc, mix, supplements, prebiotics, rule_outputs, unified_input
    )

    # Generate validated formulation
    formulation = calc.generate()
    validation = formulation["metadata"]["validation_status"]
    print(f"  {'✅' if validation == 'PASS' else '❌'} Validation: {validation}")
    print(f"  ✅ Total daily weight: {formulation['protocol_summary']['total_daily_weight_g']}g")
    print(f"  ✅ Total units: {formulation['protocol_summary']['total_daily_units']}")
    for w in formulation["metadata"]["warnings"]:
        print(f"  ⚠️ {w}")


    # ── DELIVERY ASSIGNMENTS BOX ─────────────────────────────────────────
    # Single scannable view of every physical unit and its contents.
    # Built after calc.generate() so fill weights are final.
    print("")
    _BOX_W = 70
    print(f"  DELIVERY ASSIGNMENTS:")
    print(f"  ┌" + "─" * _BOX_W)

    # Jar (prebiotics + botanicals)
    _pb_line = " · ".join(
        f"{p['substance']} {p['dose_g']}g{'[*]' if p.get('fodmap') else ''}"
        for p in calc.jar_prebiotics
    )
    _bot_line = " · ".join(
        f"{b['substance']} {b['dose_g']}g"
        for b in calc.jar_botanicals
    )
    _jar_contents = _pb_line + (" + " + _bot_line if _bot_line else "")
    _jar_g = formulation.get("delivery_format_3_powder_jar", {}).get("totals", {}).get("total_weight_g", 0)
    _phased_jar = formulation.get("delivery_format_3_powder_jar", {}).get("totals", {}).get("phased_dosing", {})
    print(f"  │ 🫙 Jar              {_jar_contents}   [{_jar_g}g]")
    if _phased_jar:
        print(f"  │                   ↑ weeks 1-2: {_phased_jar.get('weeks_1_2_g')}g/day → week 3+: {_phased_jar.get('weeks_3_plus_g')}g/day")
    if any(p.get('fodmap') for p in calc.jar_prebiotics):
        print(f"  │                   [*]=FODMAP")

    # Probiotic capsule
    _pc_totals = formulation.get("delivery_format_1_probiotic_capsule", {}).get("totals", {})
    _pc_mg = _pc_totals.get("total_weight_mg", 0)
    _pc_cfu = _pc_totals.get("total_cfu_billions", 0)
    _pc_strains = " · ".join(
        f"{s.get('substance','?').split(' ')[0]} {s.get('cfu_billions','?')}B"
        for s in calc.probiotic_components
    )
    print(f"  │ 💊 Probiotic cap    Mix {mix.get('mix_id')} · {_pc_strains} = {_pc_cfu}B CFU   [{_pc_mg}mg / 650mg]")

    # Softgels
    if calc.softgel_count > 0:
        print(f"  │ 🐟 Softgel ×{calc.softgel_count}       Omega-3 712.5mg · D3 10mcg · Vit E 7.5mg · Astaxanthin 3mg  (per softgel)")

    # Morning wellness capsule(s) — track capsule number for sequential display
    _morning_cap_num = 0
    _mwc_data = formulation.get("delivery_format_4_morning_wellness_capsules")
    if _mwc_data:
        _mwc_totals = _mwc_data.get("totals", {})
        _mwc_caps = _mwc_totals.get("capsules", [])
        _mwc_count = _mwc_totals.get("capsule_count", 1)
        if _mwc_count == 1:
            _morning_cap_num += 1
            _mwc_contents = " · ".join(
                f"{c['substance']} {c.get('dose', str(round(c.get('dose_mg',0),1))+'mg')}"
                for c in calc.morning_pooled_components
            )
            _mwc_fill = _mwc_totals.get("total_weight_mg", 0)
            print(f"  │ 🌅 Morning cap {_morning_cap_num}    {_mwc_contents}   [{_mwc_fill}mg / 650mg]")
        else:
            for _cap in _mwc_caps:
                _morning_cap_num += 1
                _cap_contents = " · ".join(
                    f"{c.get('substance','?')} {c.get('dose', str(round(c.get('dose_mg',0),1))+'mg')}"
                    for c in _cap.get("components", [])
                    if not c.get("weight_note") == "NEGLIGIBLE"
                )
                print(f"  │ 🌅 Morning cap {_morning_cap_num}    {_cap_contents}   [{_cap['fill_mg']}mg / 650mg]")

    # Polyphenol capsule — numbered as the next morning capsule
    _pp_data = formulation.get("delivery_format_6_polyphenol_capsule")
    if _pp_data:
        _morning_cap_num += 1
        _pp_contents = " · ".join(
            f"{c['substance']} {c['dose_mg']}mg"
            for c in _pp_data.get("components", [])
        )
        _pp_fill = _pp_data.get("totals", {}).get("total_weight_mg", 0)
        print(f"  │ 🌅 Morning cap {_morning_cap_num}    {_pp_contents}   [{_pp_fill}mg / 650mg]  ⚠ with food")

    # Evening capsule(s)
    _ewc_data = formulation.get("delivery_format_5_evening_wellness_capsules")
    if _ewc_data:
        _ewc_totals = _ewc_data.get("totals", {})
        _ewc_caps = _ewc_totals.get("capsules", [])
        _ewc_count = _ewc_totals.get("capsule_count", 1)
        _ewc_total_mg = _ewc_totals.get("total_weight_mg", 0)
        if _ewc_count == 1:
            _ewc_contents = " · ".join(
                f"{c['substance']} {c.get('dose_mg','?')}mg"
                for c in calc.evening_pooled_components
            )
            print(f"  │ 🌙 Evening cap      {_ewc_contents}   [{_ewc_total_mg}mg / 650mg]")
        else:
            for _cap in _ewc_caps:
                _cap_contents = " · ".join(
                    f"{c.get('substance','?')} {c.get('dose_mg','?')}mg"
                    for c in _cap.get("components", [])
                )
                print(f"  │ 🌙 Evening cap {_cap['capsule_number']}    {_cap_contents}   [{_cap['fill_mg']}mg / 650mg]")

    # Magnesium capsule
    _mg_data_box = getattr(calc, "mg_capsule_data", None)
    if _mg_data_box:
        _mg_bisglycinate = _mg_data_box["daily_total"]["mg_bisglycinate_mg"]
        _mg_elemental = _mg_data_box["daily_total"]["elemental_mg_mg"]
        print(f"  │ 💤 Mg cap           Magnesium bisglycinate {_mg_bisglycinate}mg  ({_mg_elemental}mg elemental)")

    print(f"  └" + "─" * _BOX_W)
    print("")

    # ── Post-generation: Supplement presence validation ───────────────────
    # Every LLM-selected supplement MUST appear in at least one delivery format
    all_routed = set()
    for p in calc.probiotic_components:
        all_routed.add(p.get("substance", "").lower())
    # Jar: prebiotics + heavy botanicals
    for p in calc.jar_prebiotics:
        all_routed.add(p.get("substance", "").lower())
    for b in calc.jar_botanicals:
        all_routed.add(b.get("substance", "").lower())
    # Morning pooled capsules (vitamins + minerals + light botanicals)
    for c in calc.morning_pooled_components:
        all_routed.add(c.get("substance", "").lower())
    # Evening pooled capsules
    for e in calc.evening_pooled_components:
        all_routed.add(e.get("substance", "").lower())
    for pc in calc.polyphenol_capsules:
        all_routed.add(pc.get("substance", "").lower())

    all_selected = []
    for vm in supplements.get("vitamins_minerals", []):
        if not _is_excluded(vm.get("substance", "")):
            all_selected.append(vm.get("substance", ""))
    for sp in supplements.get("supplements", []):
        if not _is_excluded(sp.get("substance", "")):
            all_selected.append(sp.get("substance", ""))

    lost_count = 0
    trimmed_count = 0
    explained_count = 0
    for name in all_selected:
        if name.lower() not in all_routed:
            if name.lower() in capacity_trimmed_names:
                trimmed_count += 1
                print(f"  ⚠️  TRIMMED (sachet capacity): {name} — removed to fit sachet 19g limit")
            elif name.lower() in evening_overflow_dropped:
                explained_count += 1
                print(f"  ⚠️  DROPPED (evening overflow): {name} — evening capsule exceeded 650mg")
            elif name.lower() in interaction_removed_names:
                explained_count += 1
                print(f"  ⚠️  REMOVED (herb-drug interaction): {name} — high-severity safety concern")
            elif name.lower() in polyphenol_cap_dropped:
                explained_count += 1
                print(f"  ⚠️  DROPPED (polyphenol cap): {name} — exceeded 1000mg polyphenol budget")
            elif name.lower() in conflict_removed_names:
                explained_count += 1
                print(f"  ⚠️  REMOVED (mineral conflict): {name} — absorption conflict with co-selected mineral")
            else:
                print(f"  🚨🚨🚨 LOST SUPPLEMENT: {name} was selected but NOT routed to any delivery format!")
                lost_count += 1
    total_ok = len(all_selected) - lost_count - trimmed_count - explained_count
    if lost_count == 0 and trimmed_count == 0 and explained_count == 0:
        print(f"  ✅ All {len(all_selected)} LLM-selected supplements routed successfully")
    elif lost_count == 0:
        print(f"  ✅ {total_ok}/{len(all_selected)} routed | {trimmed_count + explained_count} adjusted (capacity/conflicts — expected)")
    else:
        print(f"  🚨 {lost_count} supplement(s) LOST — check routing logic")
    # ── Goal-Mineral Affinity Check ──────────────────────────────────────
    # Flag if a primary goal expects a mineral that's missing from the formulation
    _all_minerals_selected = {vm.get("substance", "").lower().split("(")[0].strip() for vm in supplements.get("vitamins_minerals", [])}
    _affinity_warnings = []
    for goal in _ranked_goals_lower:
        for goal_kw, expected_minerals in GOAL_MINERAL_AFFINITIES.items():
            if goal_kw in goal:
                for mineral in expected_minerals:
                    if not any(mineral in s for s in _all_minerals_selected):
                        _affinity_warnings.append(f"ℹ️  Goal '{goal}' typically expects {mineral.title()} — not in formulation (consider adding)")
    if _affinity_warnings:
        for w in _affinity_warnings:
            print(f"  {w}")
    else:
        print(f"  ✅ All goal-mineral affinities satisfied")

    # NOTE: Text truncation check moved to after Stage F (where LLM narratives are generated)


    # ── STAGE F: Opus LLM Narratives + Assembly ──────────────────────────
    print("\n─── F. OUTPUT ──────────────────────────────────────────────")

    # Opus LLM: Ecological rationale (only when alternatives exist)
    ecological_rationale = {}
    input_narratives = {"microbiome_narrative": "", "questionnaire_narrative": ""}
    if use_llm:
        try:
            from llm_decisions import generate_ecological_rationale, generate_input_narratives
            if mix.get("alternative_considered"):
                print("  🧠 Opus: Generating ecological rationale...")
                ecological_rationale = generate_ecological_rationale(unified_input, mix)
            print("  🧠 Opus: Generating input narratives...")
            input_narratives = generate_input_narratives(unified_input, rule_outputs)
        except Exception as e:
            print("  ⚠️ Opus narrative generation failed: %s" % e)

    # Fallback: If no LLM ecological rationale, use KB-based deterministic rationale
    if not ecological_rationale or not ecological_rationale.get("selected_rationale"):
        try:
            with open(KB_DIR / "synbiotic_mixes.json", 'r', encoding='utf-8') as f:
                mixes_kb = json.load(f)
            mix_kb = mixes_kb.get("mixes", {}).get(str(mix.get("mix_id")), {})
            eco_kb = mix_kb.get("ecological_rationale", {})
            if eco_kb:
                # Find the matching scenario based on the trigger
                scenario_text = ""
                for scenario in eco_kb.get("selection_scenarios", []):
                    scenario_text = scenario.get("scientific", "")
                    break  # Use first scenario as default; the trigger is already in primary_trigger
                ecological_rationale = {
                    "selected_rationale": eco_kb.get("scientific", ""),
                    "alternative_analysis": f"Alternative considered: {mix.get('alternative_considered', 'None')}",
                    "combined_assessment": scenario_text,
                    "recommendation": eco_kb.get("client_friendly", ""),
                    "source": "knowledge_base (deterministic)",
                }
                print(f"  📋 Ecological rationale: KB fallback for Mix {mix.get('mix_id')} (no LLM)")
        except Exception as e:
            print(f"  ⚠️ KB ecological rationale fallback failed: {e}")

    # Assess questionnaire coverage
    q_coverage = _assess_questionnaire_coverage(unified_input)
    if q_coverage["coverage_level"] in ("MINIMAL", "LOW"):
        print(f"  ⚠️ QUESTIONNAIRE COVERAGE: {q_coverage['coverage_level']} ({q_coverage['completion_pct']:.0f}%)")
        for area in q_coverage["missing_data_areas"]:
            print(f"    → Missing: {area}")
    else:
        print(f"  ✅ Questionnaire coverage: {q_coverage['coverage_level']} ({q_coverage['completion_pct']:.0f}%)")

    # ── Text Truncation Check (post-Stage F — LLM narratives now available) ──
    _truncation_warnings = []
    for _field_name, _field_source in [
        ("microbiome_narrative", input_narratives.get("microbiome_narrative", "")),
        ("questionnaire_narrative", input_narratives.get("questionnaire_narrative", "")),
        ("ecological_selected", ecological_rationale.get("selected_rationale", "")),
        ("ecological_alternative", ecological_rationale.get("alternative_analysis", "")),
    ]:
        _tw = _check_text_truncation(_field_source, _field_name)
        if _tw:
            _truncation_warnings.append(_tw)
    if _truncation_warnings:
        for tw in _truncation_warnings:
            print(f"  {tw}")
    else:
        print("  ✅ No text truncation detected in LLM narratives")

    # Remove pipeline_version from metadata (internal only)
    clean_metadata = {k: v for k, v in formulation["metadata"].items() if k != "pipeline_version"}

    # ── Build canonical priority interventions (SINGLE SOURCE OF TRUTH) ──
    priority_interventions = build_priority_list(guilds)
    print(f"  📋 Priority interventions (canonical order):")
    for pi in priority_interventions:
        pi_icon = {"red": "🔴", "orange": "🟠", "amber": "🟡", "teal": "🟢"}.get(pi["color"], "⚪")
        print(f"    {pi_icon} [{pi['priority_level']}] {pi['action']}")

    # Build master formulation JSON
    master = {
        "metadata": clean_metadata,
        "questionnaire_coverage": q_coverage,
        "priority_interventions": priority_interventions,
        "input_summary": {
            "microbiome_driven": {
                "guild_status": {k: v["status"] for k, v in unified_input["microbiome"]["guilds"].items()},
                "guild_details": {
                    k: {
                        "name": v.get("name", k),
                        "abundance_pct": v.get("abundance_pct", 0),
                        "status": v.get("status", ""),
                        "priority_level": v.get("priority_level", ""),
                        "clr": v.get("clr"),
                    }
                    for k, v in unified_input["microbiome"]["guilds"].items()
                },
                "clr_ratios": unified_input["microbiome"]["clr_ratios"],
                "vitamin_signals": {k: v["status"] for k, v in unified_input["microbiome"]["vitamin_signals"].items()},
                "overall_score": unified_input["microbiome"]["overall_score"],
                "root_causes": unified_input["microbiome"].get("root_causes", {}),
            },
            "questionnaire_driven": {
                "biological_sex": unified_input["questionnaire"]["demographics"].get("biological_sex", "N/A"),
                "age": unified_input["questionnaire"]["demographics"].get("age", "N/A"),
                "diet": unified_input["questionnaire"].get("diet", {}).get("diet_pattern", "None") if isinstance(unified_input["questionnaire"].get("diet"), dict) else (unified_input["questionnaire"].get("diet") or "None"),
                "goals_ranked": unified_input["questionnaire"]["goals"]["ranked"],
                "other_raw_text": unified_input["questionnaire"]["goals"].get("other_raw_text"),
                "other_resolved_key": unified_input["questionnaire"]["goals"].get("other_resolved_key"),
                "stress_level": unified_input["questionnaire"]["lifestyle"]["stress_level"],
                "sleep_quality": unified_input["questionnaire"]["lifestyle"]["sleep_quality"],
                "sleep_quality_label": _sleep_label(unified_input["questionnaire"]["lifestyle"]["sleep_quality"]),
                "bloating_severity": unified_input["questionnaire"]["digestive"]["bloating_severity"],
                "sensitivity_classification": rule_outputs["sensitivity"]["classification"],
                "reported_deficiencies": rule_outputs["therapeutic_triggers"]["reported_deficiencies"],
            },
        },
        "decisions": {
            "mix_selection": mix,
            "supplement_selection": supplements,
            "prebiotic_design": prebiotics,
            "rule_outputs": {
                "sensitivity": rule_outputs["sensitivity"],
                "health_claims": rule_outputs["health_claims"],
                "therapeutic_triggers": rule_outputs["therapeutic_triggers"],
                "prebiotic_range": rule_outputs["prebiotic_range"],
                "magnesium": rule_outputs["magnesium"],
                "softgel": rule_outputs.get("softgel", {}),
                "sleep_supplements": rule_outputs.get("sleep_supplements", {}),
                "goal_triggered_supplements": rule_outputs.get("goal_triggered_supplements", {}),
                "timing": rule_outputs["timing"],
            },
        },
        "formulation": formulation,
        "ecological_rationale": ecological_rationale,
        "input_narratives": input_narratives,
        "component_registry": component_registry,
        "clinical_summary": clinical_summary,
        "medication_rules": {
            "timing_override": _med_timing_override,
            "substances_removed": list(_med_substances_to_remove),
            "magnesium_removed": _med_magnesium_removed,
            "clinical_flags": _med_clinical_flags,
            "evidence_flags": _elicit_evidence_result.get("evidence_flags", []),
        },
        "vitamin_production_disclaimer": VITAMIN_PRODUCTION_DISCLAIMER,
        "version": 1,
        "revision_history": [],
    }

    # ── STAGE 8.5: Medication Timing Override ────────────────────────────
    # If a Tier A medication rule requires all units to move to dinner/evening,
    # apply the override to the completed master JSON. This rewrites timing
    # fields across all delivery formats, protocol summary, and metadata.
    # Must happen AFTER master assembly, BEFORE platform JSON + output saves.
    if _med_timing_override:
        from apply_medication_timing_override import apply_timing_override, print_timing_override_summary
        print("\n─── 8.5 MEDICATION TIMING OVERRIDE ─────────────────────────")
        master = apply_timing_override(master, _med_timing_override)
        # Re-read formulation from modified master (in-place mutation, but be explicit)
        formulation = master["formulation"]
        print_timing_override_summary(_med_timing_override, formulation)
        _trace_events.append({
            "type": "medication_rule",
            "substance": "ALL UNITS",
            "description": (
                f"MEDICATION TIMING OVERRIDE ({_med_timing_override.get('rule_id', '?')}): "
                f"All morning units moved to {_med_timing_override.get('move_to', 'dinner')} — "
                f"{_med_timing_override.get('medication_normalized', '?').title()} spacing requirement"
            ),
        })

    # Build platform JSON
    platform = build_platform_json(master)

    # Save outputs directly to sample reports directory
    output_dir = sample_dir / "reports" / "reports_json"
    output_dir.mkdir(parents=True, exist_ok=True)

    master_path = output_dir / f"formulation_master_{sample_id}.json"
    platform_path = output_dir / f"formulation_platform_{sample_id}.json"

    with open(master_path, 'w', encoding='utf-8') as f:
        json.dump(master, f, indent=2, ensure_ascii=False)
    with open(platform_path, 'w', encoding='utf-8') as f:
        json.dump(platform, f, indent=2, ensure_ascii=False)

    # Build decision trace (board-readable)
    trace = build_decision_trace(master, trace_events=_trace_events)
    trace_path = output_dir / f"decision_trace_{sample_id}.json"
    with open(trace_path, 'w', encoding='utf-8') as f:
        json.dump(trace, f, indent=2, ensure_ascii=False)

    # Build manufacturing recipe (production-ready)
    recipe = build_manufacturing_recipe(master)
    recipe_path = output_dir / f"manufacturing_recipe_{sample_id}.json"
    with open(recipe_path, 'w', encoding='utf-8') as f:
        json.dump(recipe, f, indent=2, ensure_ascii=False)

    # Build component rationale (how this addresses your health)
    rationale = build_component_rationale(master)
    rationale_path = output_dir / f"component_rationale_{sample_id}.json"
    with open(rationale_path, 'w', encoding='utf-8') as f:
        json.dump(rationale, f, indent=2, ensure_ascii=False)

    # Generate manufacturing recipe markdown + PDF
    md_dir = sample_dir / "reports" / "reports_md"
    pdf_dir = sample_dir / "reports" / "reports_pdf"
    md_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    recipe_md = _generate_manufacturing_recipe_md(recipe, sample_id, q_coverage)
    recipe_md_path = md_dir / f"manufacturing_recipe_{sample_id}.md"
    with open(recipe_md_path, 'w', encoding='utf-8') as f:
        f.write(recipe_md)

    # Convert to PDF
    recipe_pdf_path = pdf_dir / f"manufacturing_recipe_{sample_id}.pdf"
    if _markdown_to_pdf(str(recipe_md_path), str(recipe_pdf_path)):
        print(f"  📄 Recipe PDF: {recipe_pdf_path}")

    print(f"\n  📄 Master:    {master_path}")
    print(f"  📄 Platform:  {platform_path}")
    print(f"  📄 Trace:     {trace_path}")
    print(f"  📄 Recipe:    {recipe_path}")
    print(f"  📄 Recipe MD: {recipe_md_path}")
    print(f"  📄 Rationale: {rationale_path}")

    # Generate dashboards
    try:
        from generate_dashboards import generate_dashboards
        generate_dashboards(sample_id, str(output_dir), str(sample_dir))
    except Exception as e:
        print(f"  ⚠️ Dashboard generation failed: {e}")

    # Save full pipeline log (captured by TeeWriter — includes all emojis and verbose output)
    log_dir = sample_dir / "reports"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"pipeline_log_{sample_id}.txt"
    print(f"  📋 Log:       {log_path}")

    # ── Compact mode: restore stdout for summary + sanity check ──────────
    if compact:
        sys.stdout.close()  # Close /dev/null
        sys.stdout = _real_stdout

    # ── BUILD TRACE EVENTS retrospectively from tracking sets ────────────
    # All the "because X → we did Y" decisions are already tracked in sets/lists
    # throughout the pipeline. Here we assemble them into a chronological list
    # for the decision trace printer.

    # 1. Mix selection
    _trace_events.append({"type": "initial_selection", "substance": f"Mix {mix.get('mix_id')}",
        "description": f"Mix {mix.get('mix_id')} ({mix.get('mix_name')}) selected — deterministic rule based on guild status: {mix.get('primary_trigger', '?')}"})

    # 2. Medication exclusions
    for _mer in medication_exclusion_reasons:
        _trace_events.append({"type": "medication_exclude", "substance": _mer.get("substance", "?"),
            "description": f"{_mer.get('substance','?')} EXCLUDED — medication interaction with {_mer.get('medication','?')}: {_mer.get('mechanism','?')}"})

    # 3. Vitamin gate removals
    for _vgr in vitamin_gate_removed:
        _trace_events.append({"type": "vitamin_gate", "substance": _vgr,
            "description": f"{_vgr} — EXCLUDED by vitamin inclusion gate"})

    # 4. Piperine auto-addition
    if _piperine_applied:
        _trace_events.append({"type": "piperine_auto", "substance": "Curcumin + Piperine",
            "description": "Curcumin detected → auto-added Piperine at 1:100 ratio for bioavailability (bundled into single component)"})

    # 5. Polyphenol dose reductions and drops
    for _ppd in polyphenol_cap_dropped:
        _trace_events.append({"type": "polyphenol_drop", "substance": _ppd,
            "description": f"{_ppd} DROPPED — polyphenol total exceeded 1000mg budget; lowest-priority item removed"})

    # 6. Evening overflow drops
    for _eod in evening_overflow_dropped:
        _trace_events.append({"type": "evening_overflow", "substance": _eod,
            "description": f"{_eod} DROPPED — evening capsule exceeded 650mg capacity after rebalancing"})

    # 7. Mineral conflict removals
    for _crn in conflict_removed_names:
        _trace_events.append({"type": "conflict_remove", "substance": _crn,
            "description": f"{_crn.title()} REMOVED — mineral absorption conflict with co-selected mineral; lower goal-relevance score"})

    # 8. Herb-drug interaction removals
    for _irn in interaction_removed_names:
        _trace_events.append({"type": "interaction_remove", "substance": _irn,
            "description": f"{_irn} REMOVED — HIGH severity herb-drug interaction (auto-removal for safety)"})

    # 9. Sachet capacity trims
    for _scn in capacity_trimmed_names:
        _trace_events.append({"type": "sachet_trim", "substance": _scn,
            "description": f"{_scn} TRIMMED — removed to fit jar/sachet 19g capacity limit"})

    # 10. Optimizer rules
    if _all_evening_for_opt:
        for _applied_rule in opt_result.get("applied_rules", []):
            # Human-readable rule names
            _opt_rule_display = {
                "ashwagandha_theanine_single_capsule": "Ashwagandha + L-Theanine single capsule (reduce Ashwagandha within KB range to co-pack with L-Theanine, eliminating evening capsule 2)",
            }.get(_applied_rule, _applied_rule)
            _trace_events.append({"type": "optimizer", "substance": "",
                "rule_name": _opt_rule_display, "rule_id": _applied_rule,
                "description": f"Dose optimizer — {_opt_rule_display}"})

    # 11. Substrate enforcement events (from prebiotic design)
    try:
        if _substrate_added:
            for _sa in _substrate_added:
                _trace_events.append({"type": "substrate_enforce", "substance": _sa,
                    "description": f"Must-include substrate enforcement: added {_sa} (missing from LLM prebiotic design)"})
        if _substrate_adjusted:
            for _sadj in _substrate_adjusted:
                _trace_events.append({"type": "substrate_enforce", "substance": _sadj,
                    "description": f"Substrate dose adjusted: {_sadj}"})
    except NameError:
        pass  # Variables not defined if substrate enforcement was skipped

    # ── PRINT DECISION TRACE SECTION ─────────────────────────────────────
    _print_decision_trace_section(
        sample_id=sample_id,
        unified_input=unified_input,
        mix=mix,
        supplements=supplements,
        prebiotics=prebiotics,
        rule_outputs=rule_outputs,
        component_registry=component_registry,
        calc=calc,
        formulation=formulation,
        medication_exclusion_set=medication_exclusion_set,
        medication_exclusion_reasons=medication_exclusion_reasons,
        vitamin_gate_removed=vitamin_gate_removed,
        polyphenol_cap_dropped=polyphenol_cap_dropped,
        evening_overflow_dropped=evening_overflow_dropped,
        capacity_trimmed_names=capacity_trimmed_names,
        conflict_removed_names=conflict_removed_names,
        interaction_removed_names=interaction_removed_names,
        clinical_summary=clinical_summary,
        effective_goals=_effective_goals,
        trace_events=_trace_events,
        excluded_polyphenols=excluded_polyphenols if 'excluded_polyphenols' in dir() else None,
    )

    # ── Stop TeeWriter and save full pipeline log ────────────────────────
    _tee_content = _tee.stop()
    try:
        with open(log_path, 'w', encoding='utf-8') as lf:
            lf.write(_tee_content)
    except Exception as e:
        print(f"  ⚠️ Pipeline log save failed: {e}")

    return master


def process_batch(batch_dir: str, use_llm: bool = True, force_keep: bool = False):
    """Process all samples in a batch directory."""
    batch_dir = Path(batch_dir)
    if not batch_dir.exists():
        print(f"❌ Batch directory not found: {batch_dir}")
        return

    # Start batch-level TeeWriter to capture full output for batch log
    _batch_tee = TeeWriter()
    _batch_tee.start()

    samples = sorted([
        d for d in batch_dir.iterdir()
        if d.is_dir() and d.name.isdigit() and len(d.name) == 13
    ])

    print(f"\nBatch: {batch_dir.name} — {len(samples)} samples")

    results = {}
    for sample_dir in samples:
        try:
            result = generate_formulation(str(sample_dir), use_llm=use_llm, force_keep=force_keep)
            if result is None:
                results[sample_dir.name] = "SKIPPED (No questionnaire)"
            else:
                results[sample_dir.name] = result["metadata"]["validation_status"]
        except Exception as e:
            print(f"\n❌ FAILED: {sample_dir.name} — {e}")
            results[sample_dir.name] = f"ERROR: {e}"

    # Summary
    print(f"\n{'='*60}")
    print(f"  BATCH SUMMARY — {batch_dir.name}")
    print(f"{'='*60}")
    for sample_id, status in results.items():
        icon = "✅" if status == "PASS" else "❌"
        print(f"  {icon} {sample_id}: {status}")
    print()

    # Stop batch TeeWriter and save batch-level log
    _batch_log_content = _batch_tee.stop()
    _batch_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    _batch_log_path = batch_dir / f"formulation_batch_log_{batch_dir.name}_{_batch_timestamp}.txt"
    try:
        with open(_batch_log_path, 'w', encoding='utf-8') as lf:
            lf.write(_batch_log_content)
        print(f"  📋 Batch log: {_batch_log_path}")
    except Exception as e:
        print(f"  ⚠️ Batch log save failed: {e}")


# ─── DECISION TRACE PRINTER ──────────────────────────────────────────────────

def _print_decision_trace_section(
    sample_id, unified_input, mix, supplements, prebiotics,
    rule_outputs, component_registry, calc, formulation,
    medication_exclusion_set, medication_exclusion_reasons,
    vitamin_gate_removed, polyphenol_cap_dropped,
    evening_overflow_dropped, capacity_trimmed_names,
    conflict_removed_names, interaction_removed_names,
    clinical_summary, effective_goals, trace_events,
    excluded_polyphenols=None,
):
    """Print a comprehensive decision trace section at the end of the pipeline log.

    This section is a self-contained "decision audit" designed so that anyone
    reading the log can understand *why* the formulation looks the way it does,
    without jumping between sections.
    """
    W = 76  # Box width
    import re  # needed for polyphenol budget math in this function

    print(f"\n{'═' * (W + 4)}")
    print(f"  DECISION TRACE — {sample_id}")
    print(f"{'═' * (W + 4)}\n")

    # ── Collect data ─────────────────────────────────────────────────────
    q = unified_input.get("questionnaire", {})
    goals_ranked = effective_goals.get("ranked", [])
    goals_display = [g.replace("_", " ") for g in goals_ranked]
    inferred_signals = [
        (s.get("signal", s) if isinstance(s, dict) else s)
        for s in clinical_summary.get("inferred_health_signals", [])
    ]
    sens = rule_outputs.get("sensitivity", {})
    timing = rule_outputs.get("timing", {})
    mix_id = mix.get("mix_id", "?")
    mix_name = mix.get("mix_name", "?")

    # Build goal → component mapping from registry
    goal_component_map = {}  # goal_key → [component_name, ...]
    for entry in component_registry:
        claims = entry.get("health_claims", [])
        based_on = entry.get("based_on", "")
        substance = entry.get("substance", "?")
        # Clean substance name (remove dose in parens for readability)
        import re as _re_trace
        clean_name = _re_trace.sub(r'\s*\([\d.]+\w+\)', '', substance).strip()
        for claim in claims:
            claim_key = claim.lower().replace(" ", "_").replace("/", "_")
            goal_component_map.setdefault(claim_key, []).append(clean_name)
        # Also map from based_on keywords
        for goal in goals_ranked:
            goal_lower = goal.lower()
            if goal_lower in based_on.lower() or any(
                kw in based_on.lower()
                for kw in goal_lower.replace("_", " ").split()
                if len(kw) > 3
            ):
                goal_component_map.setdefault(goal_lower, []).append(clean_name)

    # Deduplicate within each goal
    for k in goal_component_map:
        goal_component_map[k] = list(dict.fromkeys(goal_component_map[k]))

    # ── 1. GOAL → COMPONENT MAP ─────────────────────────────────────────
    print(f"##TRACE GOAL_MAP")
    print(f"  ┌─ GOAL → COMPONENT MAP {'─' * (W - 26)}")
    print(f"  │ {'Goal / Signal':<30} │ Components (actives)")
    print(f"  │ {'─' * 30}─┼─{'─' * (W - 35)}")

    shown_goals = set()
    for goal in goals_ranked + inferred_signals:
        goal_key = goal.lower().replace(" ", "_")
        if goal_key in shown_goals:
            continue
        shown_goals.add(goal_key)
        components = goal_component_map.get(goal_key, [])
        # Also try partial matches
        if not components:
            for map_key, map_comps in goal_component_map.items():
                if goal_key in map_key or map_key in goal_key:
                    components = map_comps
                    break
        comp_str = ", ".join(components[:6]) if components else "(no direct match)"
        goal_display = goal.replace("_", " ")
        is_inferred = goal in inferred_signals and goal not in [g.lower() for g in goals_ranked]
        tag = " [inferred]" if is_inferred else ""
        print(f"  │ {goal_display:<30} │ {comp_str}{tag}")

    print(f"  └{'─' * (W + 2)}")
    print()

    # ── 2. PROBIOTIC MIX TRACE ───────────────────────────────────────────
    print(f"##TRACE PROBIOTICS")
    print(f"  ┌─ PROBIOTIC MIX TRACE {'─' * (W - 24)}")

    # Determine rule name from mix_id
    mix_rule_names = {
        1: "M1_DYSBIOSIS: ≥3 beneficial guilds compromised → Dysbiosis Recovery",
        2: "M2_BIFIDO: Bifidobacteria keystone failure (below range, highest score) → Bifidogenic Restore",
        3: "M3_FIBER_SCFA: Fiber below range, substrate-limited → Fiber & SCFA Restoration",
        4: "M4_PROTEOLYTIC: Proteolytic overgrowth + PPR positive → Proteolytic Suppression",
        5: "M5_MUCUS: Mucin degraders elevated + diet-fed MDR → Mucus Barrier Restoration",
        6: "M6_MAINTENANCE: All guilds healthy → Maintenance Gold Standard",
        7: "M7_PSYCHOBIOTIC: Clinician-directed only → Psychobiotic (no auto trigger)",
        8: "M8_FIBER_EXPAND: Akk >10% + MDR >+0.5 + Fiber <30% → Fiber Expansion & Competitive Displacement",
    }
    rule_desc = mix_rule_names.get(mix_id, f"M{mix_id}: deterministic selection")
    print(f"  │ Rule: {rule_desc}")
    print(f"  │ Trigger guilds: {mix.get('primary_trigger', '?')}")
    clr = unified_input.get("microbiome", {}).get("clr_ratios", {})
    clr_str = ", ".join(f"{k}={v}" for k, v in clr.items())
    print(f"  │ CLR pattern: {clr_str}")
    print(f"  │ Confidence: {mix.get('confidence', '?')}")
    if mix.get("alternative_considered"):
        print(f"  │ Alternative considered: {mix['alternative_considered']}")
    print(f"  │ LLM authority: NONE — mix selection is ALWAYS deterministic (never LLM)")
    print(f"  │")

    # Strain decision lines
    print(f"  │ Component Decision Lines:")
    for strain in mix.get("strains", []):
        name = strain.get("name", "?")
        cfu = strain.get("cfu_billions", "?")
        role = strain.get("role", mix_name + " strain set")
        is_lp815 = "LP815" in name
        driver = "stress_anxiety / gut-brain" if is_lp815 else mix_name.lower()
        rule_note = "Rule: stress≥5 OR anxiety goal → add gut-brain strain" if is_lp815 else f"Rule: M{mix_id} strain set"
        constraint = "5B CFU add-on (separate from base 50B)" if is_lp815 else "—"
        print(f"  │   {name} {cfu}B | {driver} | {rule_note} | {constraint}")

    print(f"  └{'─' * (W + 2)}")
    print()

    # ── 3. PREBIOTIC TRACE ───────────────────────────────────────────────
    print(f"##TRACE PREBIOTICS")
    print(f"  ┌─ PREBIOTIC TRACE {'─' * (W - 20)}")

    pb_range = rule_outputs.get("prebiotic_range", {})
    print(f"  │ Sensitivity: {sens.get('classification', '?').upper()} | Range: {pb_range.get('min_g', '?')}–{pb_range.get('max_g', '?')}g | CFU tier: {pb_range.get('cfu_tier', '?')}")
    strategy = prebiotics.get("strategy", "?")
    print(f"  │ Strategy: {strategy}")
    print(f"  │ Inferred signals feeding design: {inferred_signals if inferred_signals else 'none'}")

    # Overrides from trace events
    override_events = [e for e in trace_events if e.get("type") in ("substrate_enforce", "prebiotic_override", "prebiotic_clamp")]
    if override_events:
        print(f"  │ Overrides applied:")
        for ev in override_events:
            print(f"  │   → {ev.get('description', ev)}")
    if prebiotics.get("overrides_applied"):
        print(f"  │ LLM overrides: {prebiotics['overrides_applied']}")

    print(f"  │")
    print(f"  │ Component Decision Lines:")
    for pb in calc.jar_prebiotics:
        substance = pb.get("substance", "?")
        dose_g = pb.get("dose_g", "?")
        fodmap_tag = " [FODMAP]" if pb.get("fodmap") else ""
        rationale = pb.get("rationale", "")
        # Check if this was modified
        modifications = [e for e in trace_events if e.get("substance", "").lower() == substance.lower() and e.get("type") in ("dose_change", "substrate_enforce", "prebiotic_clamp")]
        mod_note = ""
        if modifications:
            mod_note = f" | Modified: {modifications[0].get('description', '')}"
        # Determine driver
        driver = "microbiome"
        if "phgg" in substance.lower():
            driver = "bowel_function / low-FODMAP primary"
        elif "inulin" in substance.lower() or "fos" in substance.lower() or "gos" in substance.lower():
            driver = "bifidogenic substrate"
        elif "beta" in substance.lower() and "glucan" in substance.lower():
            driver = "immune / SCFA production"
        elif "resistant" in substance.lower():
            driver = "butyrate substrate"
        elif "psyllium" in substance.lower():
            driver = "bowel regularity"
        source = "LLM design" if not rationale.startswith("MUST-INCLUDE") else "Rule: must-include substrate enforcement"
        print(f"  │   {substance} {dose_g}g | {driver} | {source}{fodmap_tag}{mod_note}")

    print(f"  └{'─' * (W + 2)}")
    print()

    # ── 4. BOTANICALS / VITAMINS / SUPPLEMENTS TRACE ─────────────────────
    print(f"##TRACE BOTANICALS")
    print(f"  ┌─ BOTANICALS / VITAMINS / SUPPLEMENTS TRACE {'─' * (W - 47)}")

    # Group by goal
    goal_groups = {}  # goal → [(substance, dose, source, delivery)]
    for entry in component_registry:
        if entry.get("category") in ("probiotic", "prebiotic", "omega"):
            continue  # Already covered above
        claims = entry.get("health_claims", [])
        substance = entry.get("substance", "?")
        dose = entry.get("dose", "?")
        delivery = entry.get("delivery", "?")
        source = entry.get("source", "?")
        grouped = False
        for claim in claims:
            claim_key = claim.lower().replace(" ", "_").replace("/", "_")
            goal_groups.setdefault(claim_key, []).append((substance, dose, source, delivery))
            grouped = True
        if not grouped:
            goal_groups.setdefault("general_wellness", []).append((substance, dose, source, delivery))

    # Deduplicate within groups
    for k in goal_groups:
        seen = set()
        deduped = []
        for item in goal_groups[k]:
            if item[0] not in seen:
                seen.add(item[0])
                deduped.append(item)
        goal_groups[k] = deduped

    print(f"  │ Grouped by goal:")
    for goal_key, items in goal_groups.items():
        items_str = " + ".join(f"{s}" for s, d, src, dlv in items[:5])
        print(f"  │   {goal_key.replace('_', ' ')} → {items_str}")

    print(f"  │")
    print(f"  │ Component Decision Lines:")

    # All non-probiotic, non-prebiotic components
    for entry in component_registry:
        if entry.get("category") in ("probiotic", "prebiotic"):
            continue
        substance = entry.get("substance", "?")
        dose = entry.get("dose", "?")
        delivery = entry.get("delivery", "?")
        based_on = entry.get("based_on", "")
        informed_by = entry.get("informed_by", "?")
        source = entry.get("source", "?")

        # Determine driver from health_claims
        claims = entry.get("health_claims", [])
        driver = claims[0].lower().replace(" ", "_") if claims else "general"

        # Determine rule/LLM
        if source == "microbiome_primary":
            rule_tag = "Microbiome-driven"
        elif source == "microbiome_linked":
            rule_tag = "Microbiome-linked"
        elif informed_by == "questionnaire":
            rule_tag = "LLM selected (questionnaire goals)"
        else:
            rule_tag = "Rule-based"

        # Check for modifications in trace events
        substance_lower = substance.lower()
        mods = [e for e in trace_events if substance_lower in e.get("substance", "").lower()]
        mod_str = ""
        if mods:
            mod_str = f" | Journey: {mods[0].get('description', '')}"

        # Delivery shorthand
        dlv_short = {"morning wellness capsule": "morning cap", "evening capsule": "evening cap",
                      "softgel": "softgel", "sachet": "jar", "probiotic capsule": "probiotic cap",
                      "magnesium capsule": "Mg cap", "polyphenol capsule": "morning cap (polyphenol)"}.get(delivery, delivery)

        print(f"  │   {substance} | {driver} | {rule_tag} | → {dlv_short}{mod_str}")

    # ── Exclusions & Drops ───────────────────────────────────────────────
    has_exclusions = (
        medication_exclusion_reasons or vitamin_gate_removed or
        polyphenol_cap_dropped or evening_overflow_dropped or
        capacity_trimmed_names or conflict_removed_names or
        interaction_removed_names or
        (excluded_polyphenols and len(excluded_polyphenols) > 0)
    )

    if has_exclusions:
        print(f"  │")
        print(f"  │ ── Excluded / Dropped Components ──")

        if medication_exclusion_reasons:
            for reason in medication_exclusion_reasons:
                subst = reason.get("substance", "?")
                med = reason.get("medication", "?")
                mechanism = reason.get("mechanism", "?")
                print(f"  │   🚫 {subst} | EXCLUDED (medication interaction) | {med}: {mechanism}")

        if vitamin_gate_removed:
            for vr in vitamin_gate_removed:
                print(f"  │   🚫 {vr} | EXCLUDED (vitamin gate)")

        # Build surviving polyphenol summary for explanatory notes
        _surviving_pp = []
        for _reg_entry in component_registry:
            if _reg_entry.get("category") == "polyphenol":
                _surviving_pp.append(_reg_entry)
            elif _reg_entry.get("category") == "sleep_supplement":
                # Check if it's a Tier 1 polyphenol routed to evening
                _sub_lower = _reg_entry.get("substance", "").lower()
                if any(pid in _sub_lower for pid in ("propolis", "quercetin", "bergamot", "resveratrol")):
                    _surviving_pp.append(_reg_entry)
        _surviving_pp_total = sum(
            float(re.search(r'[\d.]+', str(e.get("dose", "0"))).group()) if re.search(r'[\d.]+', str(e.get("dose", "0"))) else 0
            for e in _surviving_pp
        )

        for dropped_name in polyphenol_cap_dropped:
            dropped_events = [e for e in trace_events if "polyphenol" in e.get("type", "") and dropped_name.lower() in e.get("substance", "").lower()]
            journey = ""
            if dropped_events:
                journey = f" | Journey: {dropped_events[0].get('description', '')}"
            print(f"  │   🗑️  {dropped_name} | DROPPED (polyphenol budget exceeded 1000mg){journey}")
            # 💡 Explanatory note: show budget math so readers understand WHY it was dropped
            if _surviving_pp:
                _kept_parts = " + ".join(f"{e.get('substance', '?').split('(')[0].strip()} ({e.get('dose', '?')})" for e in _surviving_pp)
                print(f"  │       💡 Polyphenol cap is 1000mg/day. Kept: {_kept_parts} = {_surviving_pp_total:.0f}mg.")
                print(f"  │         {dropped_name.title()} at its KB minimum (300mg) would total {_surviving_pp_total + 300:.0f}mg → exceeds cap, so it was deterministically removed.")

        for dropped_name in evening_overflow_dropped:
            print(f"  │   🗑️  {dropped_name} | DROPPED (evening capsule overflow > 650mg)")

        for trimmed_name in capacity_trimmed_names:
            print(f"  │   🗑️  {trimmed_name} | TRIMMED (jar/sachet capacity > 19g)")

        for removed_name in conflict_removed_names:
            print(f"  │   🗑️  {removed_name} | REMOVED (mineral absorption conflict — kept higher-goal-relevance mineral)")

        for removed_name in interaction_removed_names:
            print(f"  │   🗑️  {removed_name} | REMOVED (herb-drug interaction — HIGH severity auto-removal)")

        if excluded_polyphenols:
            for ep in excluded_polyphenols:
                print(f"  │   🚫 {ep} | EXCLUDED (polyphenol safety: pregnancy/kidney/anticoagulant)")

    print(f"  └{'─' * (W + 2)}")
    print()

    # ── 5. DELIVERY ROUTING TRACE ────────────────────────────────────────
    print(f"##TRACE ROUTING")
    print(f"  ┌─ DELIVERY ROUTING TRACE {'─' * (W - 28)}")

    # Jar
    jar_data = formulation.get("delivery_format_3_powder_jar", {})
    jar_g = jar_data.get("totals", {}).get("total_weight_g", 0)
    phased = jar_data.get("totals", {}).get("phased_dosing", {})
    jar_reason = "Daily fiber + digestion substrates for probiotic strains"
    if phased:
        jar_reason += f"; phased ramp ({phased.get('weeks_1_2_g', '?')}g → {phased.get('weeks_3_plus_g', '?')}g) for GI tolerance"
    print(f"  │ 🫙 Jar [{jar_g}g]")
    print(f"  │   Reason: {jar_reason}")

    # Probiotic capsule
    pc_data = formulation.get("delivery_format_1_probiotic_capsule", {})
    pc_mg = pc_data.get("totals", {}).get("total_weight_mg", 0)
    pc_cfu = pc_data.get("totals", {}).get("total_cfu_billions", 0)
    print(f"  │ 💊 Probiotic cap [{pc_mg}mg, {pc_cfu}B CFU]")
    print(f"  │   Reason: Mix {mix_id} ({mix_name}) — microbiome-driven, deterministic strain selection")

    # Softgels
    if calc.softgel_count > 0:
        sg_decision = rule_outputs.get("softgel", {})
        sg_needs = sg_decision.get("needs_identified", [])
        sg_reasoning = sg_decision.get("reasoning", [])
        print(f"  │ 🐟 Softgel ×{calc.softgel_count}")
        for r in sg_reasoning:
            print(f"  │   Reason: {r}")

    # Morning wellness capsule(s)
    mwc_data = formulation.get("delivery_format_4_morning_wellness_capsules")
    if mwc_data:
        mwc_count = mwc_data.get("totals", {}).get("capsule_count", 1)
        print(f"  │ 🌅 Morning wellness cap ×{mwc_count}")
        print(f"  │   Reason: Energy/metabolic/immune support components; morning timing avoids sleep interference")

    # Polyphenol capsule
    pp_data = formulation.get("delivery_format_6_polyphenol_capsule")
    if pp_data:
        pp_fill = pp_data.get("totals", {}).get("total_weight_mg", 0)
        print(f"  │ 🌅 Polyphenol cap [{pp_fill}mg]")
        print(f"  │   Reason: Tier 2 polyphenols require dedicated capsule (bitter/pungent, must take with food)")

    # Evening capsule(s)
    ewc_data = formulation.get("delivery_format_5_evening_wellness_capsules")
    if ewc_data:
        ewc_count = ewc_data.get("totals", {}).get("capsule_count", 1)
        print(f"  │ 🌙 Evening wellness cap ×{ewc_count}")
        print(f"  │   Reason: Sleep/anxiety/relaxation components; evening timing for circadian synergy")

    # Mg capsule
    mg_data = rule_outputs.get("magnesium", {})
    if mg_data.get("capsules", 0) > 0:
        mg_reasoning = mg_data.get("reasoning", [])
        print(f"  │ 💤 Mg cap ×{mg_data['capsules']}")
        mg_reason = "; ".join(mg_reasoning) if mg_reasoning else "Magnesium needs identified"
        print(f"  │   Reason: {mg_reason}; kept separate for timing flexibility (spacing from other minerals)")

    # ── Optimization / routing rules applied ─────────────────────────────
    opt_events = [e for e in trace_events if e.get("type") in ("optimizer", "capacity_guard", "rebalance", "merge", "reroute")]
    if opt_events:
        print(f"  │")
        print(f"  │ ── Optimization & Routing Rules Applied ──")
        for ev in opt_events:
            rule_name = ev.get("rule_name", "?")
            rule_id = ev.get("rule_id", "")
            description = ev.get("description", "?")
            id_tag = f" ({rule_id})" if rule_id else ""
            print(f"  │   Rule: {rule_name}{id_tag}: {description}")

    print(f"  └{'─' * (W + 2)}")
    print()

    # ── 6. FULL DECISION JOURNEY (all events chronologically) ────────────
    if trace_events:
        print(f"##TRACE JOURNEY")
        print(f"  ┌─ DECISION JOURNEY (chronological) {'─' * (W - 38)}")
        for i, ev in enumerate(trace_events, 1):
            ev_type = ev.get("type", "?")
            description = ev.get("description", "?")
            substance = ev.get("substance", "")
            type_icons = {
                "initial_selection": "📋",
                "dose_change": "↕️",
                "substrate_enforce": "📌",
                "prebiotic_override": "🔧",
                "prebiotic_clamp": "📏",
                "polyphenol_reduce": "↓",
                "polyphenol_drop": "🗑️",
                "vitamin_gate": "🚫",
                "medication_exclude": "🚫",
                "conflict_remove": "⚔️",
                "interaction_remove": "💊",
                "evening_overflow": "📦",
                "sachet_trim": "✂️",
                "optimizer": "📐",
                "capacity_guard": "⚠️",
                "rebalance": "🔄",
                "merge": "🔗",
                "reroute": "🔀",
                "piperine_auto": "🔬",
                "delivery_override": "🔄",
                "exclusion_sweep": "🧹",
                "fodmap_correction": "🔧",
                "medication_rule": "⏰",
            }
            icon = type_icons.get(ev_type, "·")
            print(f"  │ {i:2d}. {icon} {description}")
        print(f"  └{'─' * (W + 2)}")
        print()

    print(f"{'═' * (W + 4)}")
    print(f"  END DECISION TRACE — {sample_id}")
    print(f"{'═' * (W + 4)}\n")


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate supplement formulation")
    parser.add_argument("--sample-dir", help="Path to single sample directory")
    parser.add_argument("--batch-dir", help="Path to batch directory (process all samples)")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM calls (offline mode)")
    parser.add_argument("--force-keep", action="store_true", help="Keep supplements even if high-severity interactions detected (override auto-removal)")
    parser.add_argument("--compact", action="store_true", help="Compact output: only show formulation summary + sanity check (suppress pipeline detail)")
    args = parser.parse_args()

    if not args.sample_dir and not args.batch_dir:
        parser.error("Provide --sample-dir or --batch-dir")

    if args.sample_dir:
        generate_formulation(args.sample_dir, use_llm=not args.no_llm, force_keep=args.force_keep, compact=args.compact)
    elif args.batch_dir:
        process_batch(args.batch_dir, use_llm=not args.no_llm, force_keep=args.force_keep)
