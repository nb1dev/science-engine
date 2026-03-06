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
from weight_calculator import FormulationCalculator, distribute_cfu_evenly, SACHET_CAPACITY_G
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
        registry.append({
            "substance": f"{len(non_lp815)} probiotic strains ({mix_name})",
            "dose": f"{sum(p.get('cfu_billions', 10) for p in non_lp815)}B CFU",
            "delivery": "probiotic capsule",
            "category": "probiotic",
            "source": "microbiome_primary",
            "health_claims": [mix_name, mix_trigger.split("(")[0].strip() if "(" in mix_trigger else mix_trigger],
            "based_on": f"Microbiome analysis ({mix_trigger})",
            "what_it_targets": mix.get("mix_name", "Gut microbiome optimization"),
            "informed_by": "microbiome",
        })
    
    # LP815 separately
    lp815_strains = [p for p in calc.probiotic_components if "LP815" in p.get("substance", "")]
    if lp815_strains:
        registry.append({
            "substance": "LP815 psychobiotic strain (5B CFU)",
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
    # Look up health_claim from original supplement decisions
    supp_claims_map = {}
    for sp in supplements.get("supplements", []):
        supp_claims_map[sp.get("substance", "").lower()] = sp.get("health_claim", "")
    
    for sp in calc.sachet_supplements:
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
            "delivery": "sachet",
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
    compact: bool = False
) -> Dict:
    """
    Generate complete formulation for a sample.

    Args:
        sample_dir: Path to sample directory
        use_llm: Whether to use Bedrock LLM (False for offline testing)
        copy_to_sample: Whether to copy output to sample's supplement_formulation dir
        force_keep: If True, high-severity interactions are flagged but NOT auto-removed
        compact: If True, suppress all pipeline detail — only show formulation summary + sanity check

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

    # ── MICROBIOME DATA MISSING GUARD ────────────────────────────────────
    if not guilds:
        print(f"\n  🚨🚨🚨 WARNING: NO MICROBIOME GUILD DATA for {sample_id}")
        print(f"  🚨 Mix selection will DEFAULT to Mix 6 (Maintenance) — this is likely WRONG.")
        print(f"  🚨 Ensure microbiome analysis (generate_report.py) has been run BEFORE formulation.")
        print(f"  🚨 Expected file: {sample_dir}/reports/reports_json/microbiome_analysis_master_{sample_id}.json")
        print(f"  🚨 The formulation will proceed but should be REVIEWED before production.\n")

    print(f"  Sample: {unified_input['sample_id']} | Batch: {unified_input['batch_id']}")
    print(f"  Questionnaire: {q['completion']['completion_pct']:.0f}% complete")
    # Guild icons: use priority-level-based coloring (consistent with priority interventions)
    _PRIO_ICON = {"CRITICAL": "🔴", "1A": "🟠", "1B": "🟡", "Monitor": "🟢"}
    def _guild_icon(key, prio_level):
        return _PRIO_ICON.get(prio_level, "⚪")

    print(f"  Guilds:")
    for gk, gv in guilds.items():
        icon = _guild_icon(gk, gv.get("priority_level") or "Monitor")
        clr_val = f"CLR {gv.get('clr'):+.2f}" if isinstance(gv.get('clr'), (int, float)) else "CLR n/a"
        prio = gv.get("priority_level") or "Monitor"
        print(f"    {icon} {gv.get('name', gk)}: {gv.get('abundance_pct', 0):.1f}% ({gv.get('status','?')}) | {clr_val} | {prio}")
    print(f"  CLR: CUR={clr.get('CUR')}, FCR={clr.get('FCR')}, MDR={clr.get('MDR')}, PPR={clr.get('PPR')}")
    _sleep_val = q['lifestyle'].get('sleep_quality')
    print(f"  Client: {q['goals'].get('ranked', [])} | stress {q['lifestyle'].get('stress_level')}/10 | sleep {_sleep_val}/10 ({_sleep_label(_sleep_val)}) | bloating {q['digestive'].get('bloating_severity') or 'n/a'}/10")

    # ── STAGE B: Deterministic Rules ─────────────────────────────────────
    print("\n─── B. RULES ───────────────────────────────────────────────")
    rule_outputs = apply_rules(unified_input)

    sens = rule_outputs['sensitivity']
    print(f"  Sensitivity: {sens['classification'].upper()}")
    for r in sens.get('reasoning', []):
        print(f"    → {r}")

    hc = rule_outputs['health_claims']
    print(f"  Health claims: supplement={hc['supplement_claims']}, vitamin={hc['vitamin_claims']}")
    if hc['microbiome_vitamin_needs']:
        for mv in hc['microbiome_vitamin_needs']:
            print(f"    → Microbiome signal: {mv['vitamin']} ({mv['trigger']})")

    ther = rule_outputs['therapeutic_triggers']
    if ther['therapeutic_vitamins']:
        for tv in ther['therapeutic_vitamins']:
            print(f"    → THERAPEUTIC: {tv['vitamin']} {tv['dose']} (reason: {tv['reason']})")
    if ther['enhanced_vitamins']:
        for ev in ther['enhanced_vitamins']:
            print(f"    → ENHANCED: {ev['vitamin']} {ev['dose']} (reason: {ev['reason']})")

    print(f"  Prebiotic range: {rule_outputs['prebiotic_range']['min_g']}-{rule_outputs['prebiotic_range']['max_g']}g (CFU tier: {rule_outputs['prebiotic_range']['cfu_tier']})")
    mg = rule_outputs['magnesium']
    print(f"  Magnesium: {mg['capsules']} capsule(s) ({mg['needs_identified']}) — {mg['elemental_mg_total_mg']}mg elemental")
    for r in mg.get('reasoning', []):
        print(f"    → {r}")
    tm = rule_outputs['timing']
    if tm['evening_capsule_needed']:
        print(f"  Evening capsule: YES — {tm['evening_components']}")
        for comp, info in tm['timing_assignments'].items():
            print(f"    → {comp}: {info['timing']} ({info['reason']})")
    else:
        print(f"  Evening capsule: No")

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

    print(f"\n  SUPPLEMENT SELECTION: {len(supplements.get('vitamins_minerals', []))} vitamins/minerals, {len(supplements.get('supplements', []))} non-vitamins")
    for vm in supplements.get('vitamins_minerals', []):
        ther_tag = " [THERAPEUTIC]" if vm.get('therapeutic') else ""
        print(f"    → {vm['substance']}: {vm.get('dose','')} → {vm.get('delivery','?')}{ther_tag} | {vm.get('informed_by','?')} | {vm.get('rationale','')}")
    for sp in supplements.get('supplements', []):
        print(f"    → {sp['substance']}: {sp.get('dose_mg','')}mg → {sp.get('delivery','sachet')} | {sp.get('health_claim','')} ({sp.get('rank','')})")
    omega = supplements.get('omega3', {})
    print(f"    → Omega-3: {omega.get('dose_daily_mg', 1500)}mg daily")

    print(f"\n  PREBIOTIC DESIGN: {prebiotics.get('total_grams', 0)}g total, {prebiotics.get('total_fodmap_grams', 0)}g FODMAP")
    print(f"    Strategy: {prebiotics.get('strategy', '?')}")
    for pb in prebiotics.get('prebiotics', []):
        fodmap_tag = " [FODMAP]" if pb.get('fodmap') else ""
        print(f"    → {pb['substance']}: {pb['dose_g']}g{fodmap_tag}")
    if prebiotics.get('contradictions_found'):
        print(f"    Contradictions: {prebiotics['contradictions_found']}")
    if prebiotics.get('overrides_applied'):
        print(f"    Overrides: {prebiotics['overrides_applied']}")

    # ── STAGE D: Post-Processing ─────────────────────────────────────────
    print("\n─── D. POST-PROCESSING ─────────────────────────────────────")

    # Re-apply timing with knowledge of selected components
    selected_components = [s.get("substance", "") for s in supplements.get("supplements", [])]
    timing = apply_timing_rules(
        unified_input["questionnaire"]["lifestyle"],
        unified_input["questionnaire"]["goals"],
        selected_components
    )
    rule_outputs["timing"] = timing
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
    print("\n── VITAMIN INCLUSION GATE ──────────────────────────────────")
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
        print(f"  ✅ All {len(supplements.get('vitamins_minerals', []))} LLM-selected vitamins justified")

    # Enforce Delivery Format Rules (DETERMINISTIC OVERRIDE)
    FAT_SOLUBLE = {"vitamin a", "vitamin d", "vitamin d3", "vitamin e"}
    overridden = []
    for vm in supplements.get("vitamins_minerals", []):
        substance_lower = vm.get("substance", "").lower()
        is_fat_soluble = any(fs in substance_lower for fs in FAT_SOLUBLE)
        old_delivery = vm.get("delivery", "?")
        if is_fat_soluble:
            vm["delivery"] = "softgel"
        else:
            vm["delivery"] = "sachet"
        if old_delivery != vm["delivery"]:
            overridden.append(f"{vm['substance']}: {old_delivery} → {vm['delivery']}")
    if overridden:
        print(f"  Delivery overrides: {len(overridden)} corrected")
        for o in overridden:
            print(f"    → {o}")
    else:
        print(f"  Delivery: All assignments correct")

    # ── STAGE D.0c: Capsule-Only Substance Enforcement ───────────────────
    # Deterministic override: substances listed in KB capsule_only_substances
    # must NEVER go in the sachet (bitter/pungent taste). Reroute to capsule.
    print("\n── CAPSULE-ONLY ENFORCEMENT ────────────────────────────────")
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
        print(f"  🔄 Rerouted {len(_capsule_only_rerouted)} capsule-only substance(s) out of sachet:")
        for r in _capsule_only_rerouted:
            print(f"    → {r}")
    else:
        print(f"  ✅ No capsule-only substances mis-assigned to sachet")

    # ── STAGE D.1b: Polyphenol Exclusion Guards (pregnancy, kidney, anticoagulant) ──
    print("\n── POLYPHENOL EXCLUSION GUARDS ─────────────────────────────")
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
        print("  ✅ No polyphenol exclusions triggered")
    for flag in polyphenol_exclusions.get("flagged_interactions", []):
        print(f"  ⚠️ FLAGGED: {flag['substance']} — {flag['warning']}")

    # ── STAGE D.1c: Piperine Auto-Addition for Curcumin ──────────────────
    print("\n── PIPERINE AUTO-ADDITION ──────────────────────────────────")
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
        print("  · No curcumin in supplement selection — piperine auto-addition skipped")

    # ── Tracking sets for presence check explanations (declared early — used in D.1d and D.2b) ──
    # These track WHY supplements were removed so the presence check can explain
    evening_overflow_dropped = set()  # Dropped due to evening capsule overflow
    conflict_removed_names = set()    # Dropped due to mineral absorption conflict
    polyphenol_cap_dropped = set()    # Dropped due to 1000mg polyphenol cap

    # ── STAGE D.1d: Polyphenol Diversity Rule (1000mg total cap + tier routing) ──
    print("\n── POLYPHENOL DIVERSITY RULE ───────────────────────────────")
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
        print(f"  Found {len(polyphenol_supps)} polyphenol supplement(s) + {len(polyphenol_csa)} condition-specific")

        total_polyphenol_mg = 0
        tier2_items = []
        tier1_items = []
        sachet_items = []

        # Classify and sum
        for sp in polyphenol_supps:
            tier = _polyphenol_tier(sp.get("substance", ""))
            dose = sp.get("dose_mg", 0)
            if tier == 2:
                tier2_items.append(sp)
                total_polyphenol_mg += dose
                print(f"    Tier 2: {sp['substance']} ({dose}mg) → dedicated capsule")
            elif tier == 1:
                tier1_items.append(sp)
                total_polyphenol_mg += dose
                print(f"    Tier 1: {sp['substance']} ({dose}mg) → evening capsule headroom")
            elif tier == "sachet":
                sachet_items.append(sp)
                total_polyphenol_mg += dose
                print(f"    Sachet: {sp['substance']} ({dose}mg) → sachet")
            else:
                # Unknown polyphenol — treat as sachet-safe
                sachet_items.append(sp)
                total_polyphenol_mg += dose
                print(f"    Unknown tier: {sp['substance']} ({dose}mg) → sachet (default)")

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
            if csa_tier == 2:
                tier2_items.append(csa_item)
                total_polyphenol_mg += dose
                print(f"    CSA Tier 2: {csa['substance']} ({dose}mg) → dedicated capsule")
            elif csa_tier == 1:
                tier1_items.append(csa_item)
                total_polyphenol_mg += dose
                print(f"    CSA Tier 1: {csa['substance']} ({dose}mg) → evening capsule")
            else:
                # Sachet-safe CSA polyphenol — stays in condition_specific_additions for downstream handling
                sachet_items.append(csa_item)
                total_polyphenol_mg += dose
                print(f"    CSA sachet-safe: {csa['substance']} ({dose}mg) → sachet")

        print(f"  Total polyphenol mass: {total_polyphenol_mg}mg / {POLYPHENOL_MASS_CAP_MG}mg cap")

        # Enforce 1000mg cap — LLM-informed dropping with deterministic fallback
        if total_polyphenol_mg > POLYPHENOL_MASS_CAP_MG:
            overage = total_polyphenol_mg - POLYPHENOL_MASS_CAP_MG
            print(f"  ⚠️ Over polyphenol cap by {overage}mg — trimming...")

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
            if use_llm and len(all_pp_items) >= 2:
                from llm_decisions import resolve_polyphenol_conflict
                client_goals = unified_input.get("questionnaire", {}).get("goals", {}).get("ranked", [])
                llm_drop_name = resolve_polyphenol_conflict(
                    polyphenol_items=all_pp_items,
                    client_goals=client_goals,
                    mix_name=mix.get("mix_name", ""),
                    mix_trigger=mix.get("primary_trigger", ""),
                    use_bedrock=use_llm,
                )

            if llm_drop_name:
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
                    print(f"  📋 LLM drop reduced overage but {overage}mg still over — continuing with deterministic trimming...")
                else:
                    print(f"  📋 Deterministic polyphenol trimming (overage: {overage}mg)...")

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
                    print(f"    → Reduced {si['substance']}: {dose}mg → {si['dose_mg']}mg")

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
                    print(f"    → Dropped {si['substance']} ({dose}mg) — below minimum dose {min_dose}mg")
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
                    print(f"    → Dropped Tier 1: {t1['substance']} ({dose}mg)")
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
            print(f"  → Routing {t2['substance']} → dedicated morning capsule (Tier 2)")

        # Tier 1 → evening capsule headroom
        for t1 in tier1_items:
            supplements["supplements"].append({
                **t1,
                "delivery": "evening_capsule",
                "_polyphenol_tier": 1,
            })
            print(f"  → Routing {t1['substance']} → evening capsule (Tier 1)")

        # Sachet-safe → sachet
        for ss in sachet_items:
            if ss.get("_from_csa"):
                # Keep in condition_specific_additions (already handled downstream)
                pass
            else:
                supplements["supplements"].append({
                    **ss,
                    "delivery": "sachet",
                    "_polyphenol_tier": "sachet",
                })
                print(f"  → Routing {ss['substance']} → sachet (sachet-safe)")

        # Update condition-specific additions (remove polyphenols that were handled)
        prebiotics["condition_specific_additions"] = non_polyphenol_csa

        print(f"  ✅ Polyphenol diversity rule applied: {total_polyphenol_mg}mg total")
    else:
        print("  · No polyphenols in supplement selection — diversity rule skipped")

    # ── STAGE D.2: Post-LLM Validation ───────────────────────────────────
    print("\n── POST-LLM VALIDATION ─────────────────────────────────────")

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
        print("  ✅ No medications reported — interaction check skipped")

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
        print("  ✅ No supplement-to-supplement or herb-drug conflicts detected")
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
        print(f"  ✅ All FODMAP classifications correct")

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

    print("── END VALIDATION ─────────────────────────────────────────\n")

    # ── STAGE E: Weight Calculation ──────────────────────────────────────
    print("\n─── E. WEIGHT CALCULATION ──────────────────────────────────")
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
        dose_str = str(csa.get("dose_g_or_mg", ""))
        substance = csa.get("substance", "Unknown")
        rationale = csa.get("rationale", "")
        # Robust dose parsing: extract numeric value with regex (handles LLM strings like "included in base 3.0g")
        _dose_num = re.search(r'([\d.]+)', dose_str)
        if not _dose_num:
            print(f"  ⚠️ CSA skipped (no numeric dose): {substance} — dose_str='{dose_str}'")
            continue
        _dose_val = float(_dose_num.group(1))
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

    # Add sachet vitamins/minerals
    for vm in supplements.get("vitamins_minerals", []):
        if vm.get("delivery") == "sachet":
            calc.add_sachet_vitamin(
                vm["substance"],
                vm.get("dose_value", 0),
                vm.get("dose_unit", "mg"),
                therapeutic=vm.get("therapeutic", False),
                standard_dose=vm.get("standard_dose", ""),
                rationale=vm.get("rationale", ""),
                clinical_note=vm.get("interaction_note", ""),
            )

    # Add sachet supplements (amino acids, botanicals)
    # Route to correct delivery unit based on LLM assignment + polyphenol tier routing
    for supp in supplements.get("supplements", []):
        delivery = supp.get("delivery", "sachet")
        if delivery == "sachet":
            calc.add_sachet_supplement(
                supp["substance"],
                supp.get("dose_mg", 0),
                rationale=supp.get("rationale", ""),
            )
        elif delivery == "evening_capsule":
            # LLM-selected supplements or Tier 1 polyphenols routed to evening capsule
            calc.add_evening_component(
                supp["substance"],
                supp.get("dose_mg", 0),
                rationale=supp.get("rationale", ""),
            )
            tier_tag = " [Tier 1 polyphenol]" if supp.get("_polyphenol_tier") == 1 else ""
            print(f"  → Supplement → evening capsule: {supp['substance']} {supp.get('dose_mg', 0)}mg{tier_tag}")
        elif delivery == "polyphenol_capsule":
            # Tier 2 polyphenols get dedicated morning capsule
            calc.add_polyphenol_capsule(
                supp["substance"],
                supp.get("dose_mg", 0),
                rationale=supp.get("rationale", ""),
                timing="morning",
            )
            print(f"  → Tier 2 polyphenol → dedicated capsule: {supp['substance']} {supp.get('dose_mg', 0)}mg")

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
        print(f"  ⚠️ LLM evening overflow: {llm_evening_total}mg > {EVENING_CAPSULE_CAPACITY_MG}mg — initiating smart resolution...")
        
        # Save original doses BEFORE any reduction (for restore if split needed)
        import copy
        _original_evening = copy.deepcopy(calc.evening_components)
        
        # Step 1: Try reducing evening doses to KB minimums (single capsule attempt)
        print(f"    Step 1: Trying to fit in 1 capsule by reducing doses to KB minimums...")
        for comp in calc.evening_components:
            kb = _find_evening_kb(comp["substance"])
            if kb and kb["min_dose_mg"] is not None and comp["dose_mg"] > kb["min_dose_mg"]:
                saved = comp["dose_mg"] - kb["min_dose_mg"]
                print(f"      → {comp['substance']}: {comp['dose_mg']}mg → {kb['min_dose_mg']}mg (saved {saved}mg)")
                comp["_original_dose_mg"] = comp["dose_mg"]  # Tag for rebalancing restoration
                comp["dose_mg"] = kb["min_dose_mg"]
                comp["weight_mg"] = kb["min_dose_mg"]
        
        llm_evening_total = sum(c.get("dose_mg", 0) for c in calc.evening_components)
        if llm_evening_total <= EVENING_CAPSULE_CAPACITY_MG:
            print(f"    ✅ Evening resolved at Step 1: {llm_evening_total}mg ≤ {EVENING_CAPSULE_CAPACITY_MG}mg (reduced doses)")
        else:
            # Step 2: Can't fit in 1 capsule — REVERT to original doses and split into 2
            print(f"    Step 2: Can't fit in 1 capsule even at min doses ({llm_evening_total}mg) — reverting to full doses and splitting...")
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
                print(f"      Evening capsule 1: {cap1_total}mg ({len(capsule1)} components)")
                for c in capsule1:
                    print(f"        · {c['substance']}: {c['dose_mg']}mg")
                print(f"      Evening capsule 2: {cap2_total}mg ({len(capsule2_overflow)} components)")
                for c in capsule2_overflow:
                    print(f"        · {c['substance']}: {c['dose_mg']}mg")
                print(f"    ✅ Evening resolved by splitting into 2 capsules")
            else:
                print(f"    ✅ Evening resolved at Step 2: all fit in capsule 1")

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
                # Document dose escalation for L-Theanine (400mg = high-stress + severe-sleep escalation)
                escalation_note = ""
                if "theanine" in ss["substance"].lower() and ss["dose_mg"] > 200:
                    escalation_note = " [ESCALATED: stress≥7 + sleep≤5 → 400mg per KB rule]"
                print(f"    → {ss['substance']}: {ss['dose_mg']}mg → EVENING capsule ({timing_info.get('reason', '')}){escalation_note}")
            else:
                calc.add_sachet_supplement(ss["substance"], ss["dose_mg"], rationale=ss.get("rationale", ""))
                print(f"    → {ss['substance']}: {ss['dose_mg']}mg → morning sachet ({ss.get('rationale', '')})")

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
    mg_needs = rule_outputs.get("magnesium", {})
    if mg_needs.get("capsules", 0) > 0:
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
            print(f"  🔄 Evening rebalancing ({len(_all_evening)} components, {_total_evening_mg}mg across 2 capsules)...")

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
                            print(f"    → Rebalance: {comp['substance']}: {old_dose}mg → {comp['dose_mg']}mg ({cap_name})")

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
            print(f"    ✅ Rebalanced: cap1={cap1_total}mg ({cap1_names})")
            if cap2:
                print(f"    ✅ Rebalanced: cap2={cap2_total}mg ({cap2_names})")
            else:
                print(f"    ✅ Rebalanced: all fit in 1 capsule")

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
    print("\n── DOSE OPTIMIZER (JSON-driven rules) ──────────────────────")
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
            print(f"  · No optimization rules matched — doses preserved as-is")
    
    # Evening capsules contain only active ingredients at their upstream-selected doses.
    # No filler is added — capsule headroom is acceptable.
    print("── END DOSE OPTIMIZER ─────────────────────────────────────")

    # ── UNIVERSAL SMALL-CAPSULE MERGE ────────────────────────────────────
    # If a secondary capsule (evening capsule 2, or any overflow capsule) has
    # very little total weight (≤ MERGE_THRESHOLD_MG), it's wasteful to use a
    # whole 650mg capsule shell for it. Instead, shave a tiny amount from the
    # primary capsule's largest components and absorb the small capsule's
    # contents into it. This is clinically negligible (e.g., 6mg from 450mg =
    # 1.3%) but eliminates absurd packaging waste.
    #
    # Applies to ALL capsule pairs: evening cap1+cap2, polyphenol, etc.
    print("\n── SMALL-CAPSULE MERGE ─────────────────────────────────────")
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
            print(f"  · Evening capsule 2 ({_ec2_merge_total}mg) above merge threshold ({MERGE_THRESHOLD_MG}mg) — keeping split")
    else:
        print(f"  · No secondary capsules to merge")

    # Apply to polyphenol capsule — check if it's tiny and could merge with morning probiotic
    # (Skip for now — polyphenol capsules are typically 500+mg, unlikely to hit threshold)

    print("── END SMALL-CAPSULE MERGE ─────────────────────────────────")

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
    print("  Deduplication check...")
    
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

    # Vitamins — deduplicate within vitamins and against prebiotics
    deduped_vitamins = []
    for v in calc.sachet_vitamins:
        key = v["substance"].lower()
        if key in seen_sachet:
            print(f"    → Removed duplicate vitamin: {v['substance']} ({v['dose']})")
        else:
            seen_sachet.add(key)
            deduped_vitamins.append(v)
    calc.sachet_vitamins = deduped_vitamins

    # Supplements — deduplicate within supplements and against prebiotics+vitamins
    # Note: Fiber supplements should already be filtered by EXCLUDED_FIBERS in Stage D.
    # This block is a safety net — if a fiber leaks through, drop it (prebiotic dose is authority).
    deduped_supplements = []
    for s in calc.sachet_supplements:
        key = s["substance"].lower()
        if key in seen_sachet:
            print(f"    → Removed duplicate supplement: {s['substance']} ({s['weight_mg']}mg) — already present in sachet")
        else:
            seen_sachet.add(key)
            deduped_supplements.append(s)
    calc.sachet_supplements = deduped_supplements

    # Evening capsule: deduplicate
    seen_evening = set()
    deduped_evening = []
    for e in calc.evening_components:
        key = e["substance"].lower()
        if key in seen_evening:
            print(f"    → Removed duplicate evening component: {e['substance']} ({e['dose_mg']}mg)")
        else:
            seen_evening.add(key)
            deduped_evening.append(e)
    calc.evening_components = deduped_evening

    # ── Sachet capacity guard: smart overflow resolution ─────────────────
    # 4-step algorithm: reduce doses → reroute to evening → drop redundant → alert
    capacity_trimmed_names = _resolve_sachet_overflow(calc, supplements)

    # ── Build Component Registry — single source of truth ────────────────
    # Built from the ACTUAL formulation calculator state (post-dedup, post-trim)
    # Every downstream consumer (rationale table, dashboard, source %) reads this
    print("  Building component registry...")
    component_registry = _build_component_registry(
        calc, mix, supplements, prebiotics, rule_outputs, unified_input
    )
    print(f"  ✅ Registry: {len(component_registry)} components")

    # Generate validated formulation
    formulation = calc.generate()
    validation = formulation["metadata"]["validation_status"]
    print(f"  {'✅' if validation == 'PASS' else '❌'} Validation: {validation}")
    print(f"  ✅ Total daily weight: {formulation['protocol_summary']['total_daily_weight_g']}g")
    print(f"  ✅ Total units: {formulation['protocol_summary']['total_daily_units']}")
    for w in formulation["metadata"]["warnings"]:
        print(f"  ⚠️ {w}")

    # ── Post-generation: Supplement presence validation ───────────────────
    # Every LLM-selected supplement MUST appear in at least one delivery format
    print("\n── SUPPLEMENT PRESENCE CHECK ────────────────────────────────")
    all_routed = set()
    for p in calc.probiotic_components:
        all_routed.add(p.get("substance", "").lower())
    for p in calc.sachet_prebiotics:
        all_routed.add(p.get("substance", "").lower())
    for v in calc.sachet_vitamins:
        all_routed.add(v.get("substance", "").lower())
    for s in calc.sachet_supplements:
        all_routed.add(s.get("substance", "").lower())
    for e in calc.evening_components:
        all_routed.add(e.get("substance", "").lower())
    for pc in calc.polyphenol_capsules:
        all_routed.add(pc.get("substance", "").lower())
    # Also check evening capsule 2 (overflow)
    for e2 in getattr(calc, 'evening_capsule_2', []):
        all_routed.add(e2.get("substance", "").lower())

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
    print("── GOAL-MINERAL AFFINITY CHECK ─────────────────────────────")
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

    print("── END PRESENCE CHECK ─────────────────────────────────────\n")

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
        "vitamin_production_disclaimer": VITAMIN_PRODUCTION_DISCLAIMER,
        "version": 1,
        "revision_history": [],
    }

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
    trace = build_decision_trace(master)
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

    # ── FORMULATION SUMMARY ──────────────────────────────────────────────
    print(f"\n{'═'*60}")
    print(f"  FORMULATION SUMMARY — {sample_id}")
    print(f"{'═'*60}")
    print(f"  Mix: {mix.get('mix_id')} — {mix.get('mix_name')} ({mix.get('total_cfu_billions', '?')}B CFU, {len(mix.get('strains', []))} strains)")
    print(f"")
    print(f"  📦 DELIVERY UNITS:")
    # Probiotic capsule
    _pc = formulation.get('delivery_format_1_probiotic_capsule', {})
    _pc_mg = _pc.get('totals', {}).get('total_weight_mg', 0)
    print(f"  ┌─ Morning ─────────────────────────────────────────────")
    print(f"  │ 1× Probiotic Hard Capsule ({_pc_mg}mg / 650mg)")
    # Softgels
    _sg = formulation.get('delivery_format_2_omega_softgels') or {}
    _sg_count = _sg.get('totals', {}).get('daily_count', 0)
    if _sg_count > 0:
        print(f"  │ {_sg_count}× Omega + Antioxidant Softgel (Omega-3 + D3 + E + Astaxanthin)")
    # Polyphenol capsule
    _pp = formulation.get('delivery_format_5_polyphenol_capsule')
    if _pp:
        _pp_mg = _pp.get('totals', {}).get('total_weight_mg', 0)
        print(f"  │ 1× Morning Wellness Capsule ({_pp_mg}mg / 650mg)")
        for _ppc in _pp.get('components', []):
            print(f"  │     {_ppc['substance']}: {_ppc['dose_mg']}mg")
    # Sachet
    _st = formulation.get('delivery_format_3_daily_sachet', {})
    _st_totals = _st.get('totals', {})
    _st_g = _st_totals.get('total_weight_g', 0)
    print(f"  │ 1× Daily Sachet ({_st_g}g / 19g):")
    _pb_total = _st_totals.get('prebiotic_total_g', 0)
    _pb_items = ', '.join(f"{p['substance']} {p['dose_g']}g" for p in calc.sachet_prebiotics)
    print(f"  │     Prebiotics ({_pb_total}g): {_pb_items}")
    if calc.sachet_vitamins:
        _vm_items = ', '.join(f"{v['substance']} {v['dose']}" for v in calc.sachet_vitamins)
        print(f"  │     Vitamins: {_vm_items}")
    if calc.sachet_supplements:
        _sp_items = ', '.join(f"{s['substance']} {s['weight_mg']}mg" for s in calc.sachet_supplements)
        print(f"  │     Supplements: {_sp_items}")
    # Mg capsules
    _mg_data = getattr(calc, 'mg_capsule_data', None)
    # Evening section
    print(f"  ├─ Evening ─────────────────────────────────────────────")
    if calc.evening_components:
        _ec_mg = sum(c['dose_mg'] for c in calc.evening_components)
        print(f"  │ 1× Evening Wellness Capsule ({_ec_mg}mg / 650mg):")
        for _ec in calc.evening_components:
            print(f"  │     {_ec['substance']}: {_ec['dose_mg']}mg")
    _ec2 = getattr(calc, 'evening_capsule_2', [])
    if _ec2:
        _ec2_mg = sum(c['dose_mg'] for c in _ec2)
        print(f"  │ 1× Evening Wellness Capsule (2) ({_ec2_mg}mg / 650mg):")
        for _e2 in _ec2:
            print(f"  │     {_e2['substance']}: {_e2['dose_mg']}mg")
    if _mg_data and _mg_data.get('timing') == 'evening':
        print(f"  │ {_mg_data['capsule_count']}× Magnesium Bisglycinate Capsule ({_mg_data['daily_total']['mg_bisglycinate_mg']}mg bisglycinate)")
    if not calc.evening_components and not _ec2 and not (_mg_data and _mg_data.get('timing') == 'evening'):
        print(f"  │ (none)")
    print(f"  └───────────────────────────────────────────────────────")
    # Build unit breakdown description
    _unit_parts = ["1 probiotic"]
    if _sg_count > 0:
        _unit_parts.append(f"{_sg_count} softgel{'s' if _sg_count > 1 else ''}")
    if _pp:
        _unit_parts.append("1 wellness cap")
    _unit_parts.append("1 sachet")
    if _mg_data:
        _unit_parts.append(f"{_mg_data['capsule_count']} Mg cap{'s' if _mg_data['capsule_count'] > 1 else ''}")
    if calc.evening_components:
        _unit_parts.append("1 evening cap")
    if _ec2:
        _unit_parts.append("1 evening cap 2")
    _total_units = formulation['protocol_summary']['total_daily_units']
    print(f"  Total: {_total_units} units ({', '.join(_unit_parts)}) | {formulation['protocol_summary']['total_daily_weight_g']}g daily | Validation: {validation}")
    print(f"{'═'*60}\n")

    # ── LLM FORMULATION SANITY CHECK ─────────────────────────────────────
    # Final safety net: send the completed manufacturing recipe to Claude for
    # a structural QA review. Checks consistency, capacity, timing — not clinical.
    # Runs AFTER formulation summary so you see the recipe first.
    print(f"── LLM FORMULATION SANITY CHECK ────────────────────────────")
    sanity_warnings = []
    if use_llm:
        try:
            from llm_decisions import formulation_sanity_check
            print("  🧠 QA reviewing manufacturing recipe...")
            _sanity_claims = rule_outputs.get("health_claims", {}).get("supplement_claims", []) + rule_outputs.get("health_claims", {}).get("vitamin_claims", [])
            _sanity_goals = unified_input.get("questionnaire", {}).get("goals", {}).get("ranked", [])
            sanity_result = formulation_sanity_check(
                recipe=recipe,
                health_claims=_sanity_claims,
                client_goals=_sanity_goals,
                use_bedrock=use_llm,
            )
            sanity_warnings = sanity_result.get("warnings", [])
            _sanity_assessment = sanity_result.get("overall_assessment", "")
            if sanity_result.get("skipped"):
                print(f"  ⚠️ Sanity check skipped: {_sanity_assessment}")
            elif sanity_warnings:
                for sw in sanity_warnings:
                    severity = sw.get("severity", "info")
                    icon = "🚨" if severity == "error" else "⚠️" if severity == "warning" else "ℹ️"
                    unit = sw.get("unit", "?")
                    issue = sw.get("issue", "?")
                    suggestion = sw.get("suggestion", "")
                    print(f"  {icon} {severity.upper()} [{unit}]: {issue}")
                    if suggestion:
                        print(f"      → Suggestion: {suggestion}")
                for sw in sanity_warnings:
                    if sw.get("severity") in ("error", "warning"):
                        formulation["metadata"]["warnings"].append(
                            f"LLM sanity check [{sw.get('severity', '?').upper()}]: {sw.get('unit', '?')} — {sw.get('issue', '?')}"
                        )
                print(f"  📋 {len(sanity_warnings)} issue(s) found — {_sanity_assessment}")
            else:
                print(f"  ✅ {_sanity_assessment}")
        except Exception as e:
            print(f"  ⚠️ Sanity check failed: {e}")
    else:
        print("  · Skipped (offline mode)")
    print("── END SANITY CHECK ───────────────────────────────────────\n")

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
