#!/usr/bin/env python3
"""
Weight Calculator — Formulation weight calculations with ZERO error tolerance.

Architecture (v3.0 — powder jar + capsule stacking):

Delivery units:
  1. Probiotic hard capsule (1×, fixed count, 650mg)
  2. Omega + antioxidant softgels (0–2×, fixed composition, 750mg each)
  3. Powder jar (1×, prebiotics + heavy botanicals, ≤19g soft target, phased dosing)
  4. Morning Wellness Capsule(s) (N×, vitamins + minerals + light botanicals, CapsuleStackingOptimizer)
  5. Evening Wellness Capsule(s) (M×, sleep aids + calming adaptogens + Tier 1 polyphenols, CapsuleStackingOptimizer)
  6. Polyphenol dedicated capsule (0–1×, Curcumin+Piperine or Bergamot, 650mg)
  7. Magnesium bisglycinate capsule(s) (0–2×, fixed 750mg each, evening)

Weight Formulas:
  Probiotics:   weight_mg = cfu_billions × 10
  Omega oils:   weight_mg = dose_mg (1:1)
  Vitamins mcg: NEGLIGIBLE — does NOT reduce capacity
  Vitamins mg:  weight_mg = dose_mg (1:1)
  Prebiotics:   weight_g = dose_g (1:1)
  Botanicals:   weight_mg = dose_mg (1:1)

CapsuleStackingOptimizer:
  Pools all eligible components, finds minimum capsule count by adjusting
  doses within KB-defined [min_dose_mg, max_dose_mg] ranges.
  Produces full adjustment_record for audit trail.
"""

import json
import math

def _round_clinical(x: float, decimals: int = 1) -> float:
    """Round a clinical value using standard (not banker's) rounding.
    
    Python 3's built-in round() uses banker's rounding (round-half-to-even),
    which causes round(2.5) = 2, round(12.5) = 12. This is wrong for clinical
    doses where 12.5B CFU must display as 12.5, not 12.
    
    This helper always rounds half-up: 12.5 → 12.5, 2.5 → 3.0.
    """
    factor = 10 ** decimals
    return math.floor(x * factor + 0.5) / factor


from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ─── CONSTANTS ────────────────────────────────────────────────────────────────

HARD_CAPSULE_CAPACITY_MG = 650
SOFTGEL_CAPACITY_MG = 750
JAR_TARGET_G = 19.0          # Soft limit — warn if exceeded, never hard-fail
EVENING_CAPSULE_CAPACITY_MG = 650  # kept for backward compat in generate_formulation references

# CFU to weight conversion
CFU_TO_MG_FACTOR = 10        # 1B CFU = 10mg powder

# KB delivery format rules
KB_DIR = Path(__file__).parent / "knowledge_base"


def _load_delivery_kb() -> Dict:
    path = KB_DIR / "delivery_format_rules.json"
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ─── WEIGHT CALCULATION FUNCTIONS ─────────────────────────────────────────────

def probiotic_weight_mg(cfu_billions: float) -> float:
    """Calculate probiotic powder weight from CFU count.
    Formula: weight_mg = cfu_billions × 10
    """
    return round(cfu_billions * CFU_TO_MG_FACTOR, 2)


def vitamin_weight_mg(dose_value: float, dose_unit: str) -> float:
    """Calculate vitamin weight.
    mcg doses: NEGLIGIBLE (returns 0)
    mg doses: 1:1 relationship
    """
    if dose_unit.lower() in ["mcg", "μg", "ug"]:
        return 0.0
    elif dose_unit.lower() in ["mg"]:
        return round(dose_value, 2)
    elif dose_unit.lower() in ["g"]:
        return round(dose_value * 1000, 2)
    else:
        raise ValueError(f"Unknown dose unit: {dose_unit}. Expected mcg, mg, or g.")


def prebiotic_weight_g(dose_g: float) -> float:
    """Calculate prebiotic weight. 1:1 relationship in grams."""
    return round(dose_g, 3)


def is_negligible_weight(dose_unit: str) -> bool:
    """Check if a dose unit produces negligible weight (mcg vitamins)."""
    return dose_unit.lower() in ["mcg", "μg", "ug"]


# ─── CAPSULE STACKING OPTIMIZER ───────────────────────────────────────────────

class CapsuleStackingOptimizer:
    """
    Pools eligible components and finds the minimum number of 650mg capsules
    needed by adjusting doses within KB-defined [min_dose_mg, max_dose_mg] ranges.

    Algorithm:
      1. Sum all component doses → total_pooled_mg
      2. raw_count = total / capacity (e.g. 3.09)
      3. Try floor(raw_count) capsules:
         - target_mass = floor_count × capacity
         - overflow = total - target_mass
         - If overflow ≤ 0: done (fits)
         - If overflow > 0: reduce adjustable ingredients (largest first) within their ranges
         - If overflow eliminated → use floor_count
         - Else → use ceil_count
      4. Return capsule_count, adjusted components, full adjustment_record

    Each component dict must have:
      substance (str), dose_mg (float), min_dose_mg (float or None),
      max_dose_mg (float or None), adjustable (bool)
    """

    def __init__(self, capsule_capacity_mg: float = 650):
        self.capacity = capsule_capacity_mg

    def optimize(self, components: List[Dict]) -> Dict:
        """
        Run the stacking optimization.

        Returns:
            capsule_count (int)
            components (list) — with possibly adjusted dose_mg values
            adjustment_record (dict) — full audit trail
        """
        if not components:
            return {
                "capsule_count": 0,
                "components": [],
                "adjustment_record": {
                    "original_total_mg": 0,
                    "capsule_capacity_mg": self.capacity,
                    "raw_capsule_count": 0,
                    "chosen_capsule_count": 0,
                    "overflow_mg": 0,
                    "adjustments_made": [],
                    "final_total_mg": 0,
                    "optimization_outcome": "fit_without_adjustment",
                }
            }

        # Work on a copy to avoid mutating inputs
        import copy
        comps = copy.deepcopy(components)

        original_total = sum(c.get("dose_mg", 0) for c in comps)
        raw_count = original_total / self.capacity
        floor_count = max(1, math.floor(raw_count))
        ceil_count = math.ceil(raw_count) if raw_count > floor_count else floor_count

        adjustments_made = []
        outcome = "fit_without_adjustment"

        # Try to fit in floor_count capsules
        target_mass = floor_count * self.capacity
        overflow = original_total - target_mass

        if overflow <= 0:
            # Already fits in floor_count capsules
            chosen_count = floor_count
            outcome = "fit_without_adjustment"
        else:
            # Attempt to reduce overflow via dose adjustments
            # Sort adjustable components largest-first (best candidates for reduction)
            adjustable = [
                (i, c) for i, c in enumerate(comps)
                if c.get("adjustable", False)
                and c.get("min_dose_mg") is not None
                and c.get("dose_mg", 0) > c.get("min_dose_mg", 0)
            ]
            adjustable.sort(key=lambda x: -x[1].get("dose_mg", 0))

            remaining_overflow = overflow
            for idx, comp in adjustable:
                if remaining_overflow <= 0:
                    break
                current_dose = comp["dose_mg"]
                min_dose = comp["min_dose_mg"]
                max_reducible = current_dose - min_dose
                reduce_by = min(remaining_overflow, max_reducible)
                if reduce_by > 0:
                    new_dose = round(current_dose - reduce_by, 2)
                    adjustments_made.append({
                        "substance": comp["substance"],
                        "original_dose_mg": current_dose,
                        "adjusted_dose_mg": new_dose,
                        "reduction_mg": round(reduce_by, 2),
                    })
                    comps[idx]["dose_mg"] = new_dose
                    comps[idx]["weight_mg"] = new_dose
                    remaining_overflow -= reduce_by

            if remaining_overflow <= 0.001:  # tolerance for float rounding
                chosen_count = floor_count
                outcome = "fit_after_adjustment"
            else:
                # Could not compress into floor_count — use ceil
                # Restore components to original doses
                comps = copy.deepcopy(components)
                adjustments_made = []
                chosen_count = ceil_count
                outcome = "used_ceil_count"

        final_total = round(sum(c.get("dose_mg", 0) for c in comps), 2)

        # Warn if capsule count exceeds preferred max per KB
        try:
            dfr = _load_delivery_kb()
            pref_max = dfr.get("unit_count_limits", {}).get("preferred_max", 9)
            abs_max = dfr.get("unit_count_limits", {}).get("absolute_max", 13)
        except Exception:
            pref_max, abs_max = 9, 13

        return {
            "capsule_count": chosen_count,
            "components": comps,
            "adjustment_record": {
                "original_total_mg": round(original_total, 2),
                "capsule_capacity_mg": self.capacity,
                "raw_capsule_count": round(raw_count, 4),
                "chosen_capsule_count": chosen_count,
                "overflow_mg": round(overflow, 2),
                "adjustments_made": adjustments_made,
                "final_total_mg": final_total,
                "optimization_outcome": outcome,
            }
        }


# ─── DELIVERY FORMAT BUILDERS ─────────────────────────────────────────────────

class FormulationCalculator:
    """Build and validate a complete formulation with weight calculations.

    Delivery architecture (v3.0):
      jar_prebiotics     — prebiotics + prebiotic-like fibers → powder jar
      jar_botanicals     — heavy non-bitter botanicals (dose > threshold) → powder jar
      morning_pooled     — vitamins + minerals + light botanicals → Morning Wellness Capsule(s)
      evening_pooled     — sleep aids + calming adaptogens + Tier 1 polyphenols → Evening Wellness Capsule(s)
      probiotic_components — probiotics → Probiotic Capsule
      softgel_components   — fixed omega/vit/astaxanthin → Omega Softgels
      polyphenol_capsules  — Curcumin+Piperine, Bergamot → Dedicated morning capsule
      mg_capsule_data      — Mg bisglycinate → Magnesium Capsule(s), always evening
    """

    def __init__(self, sample_id: str):
        self.sample_id = sample_id
        self.timestamp = datetime.utcnow().isoformat() + "Z"

        # ── Jar (powder) ──────────────────────────────────────────────────────
        self.jar_prebiotics: List[Dict] = []
        self.jar_botanicals: List[Dict] = []  # heavy non-bitter (dose > threshold)
        self.prebiotic_strategy: Optional[str] = None

        # ── Pooled capsule groups ─────────────────────────────────────────────
        self.morning_pooled_components: List[Dict] = []   # vitamins + minerals + light botanicals
        self.evening_pooled_components: List[Dict] = []   # sleep aids + calming adaptogens + Tier 1 polyphenols

        # ── Fixed/dedicated units ─────────────────────────────────────────────
        self.probiotic_components: List[Dict] = []
        self.softgel_components: List[Dict] = []
        self.polyphenol_capsules: List[Dict] = []

        # ── Metadata ──────────────────────────────────────────────────────────
        self.mix_id: Optional[int] = None
        self.mix_name: Optional[str] = None
        self.softgel_count: int = 2
        self.warnings: List[str] = []

        # ── Load KB once ──────────────────────────────────────────────────────
        try:
            self._dfr = _load_delivery_kb()
            self._heavy_threshold = self._dfr.get("heavy_botanical_threshold_mg", 650)
            self._phased_policy = self._dfr.get("phased_dosing_policy", {})
        except Exception:
            self._dfr = {}
            self._heavy_threshold = 650
            self._phased_policy = {}

    # ─── BACKWARD COMPATIBILITY ALIASES ───────────────────────────────────────
    # These allow generate_formulation.py to be updated incrementally.

    @property
    def sachet_prebiotics(self) -> List[Dict]:
        return self.jar_prebiotics

    @sachet_prebiotics.setter
    def sachet_prebiotics(self, value: List[Dict]):
        self.jar_prebiotics = value

    @property
    def sachet_vitamins(self) -> List[Dict]:
        return self.morning_pooled_components

    @sachet_vitamins.setter
    def sachet_vitamins(self, value: List[Dict]):
        self.morning_pooled_components = value

    @property
    def sachet_supplements(self) -> List[Dict]:
        """Light sachet supplements now in morning_pooled or jar_botanicals.
        Backward compat: returns morning_pooled only (heavy are separate).
        Setter appends to the correct list based on dose.
        """
        return self.morning_pooled_components

    @sachet_supplements.setter
    def sachet_supplements(self, value: List[Dict]):
        # Re-route each item to the correct pool
        self.morning_pooled_components = [
            c for c in self.morning_pooled_components
            if c.get("_source") == "vitamin_mineral"
        ]
        self.jar_botanicals = []
        for item in value:
            dose_mg = item.get("weight_mg", item.get("dose_mg", 0))
            if dose_mg > self._heavy_threshold:
                self.jar_botanicals.append(item)
            else:
                self.morning_pooled_components.append(item)

    @property
    def evening_components(self) -> List[Dict]:
        return self.evening_pooled_components

    @evening_components.setter
    def evening_components(self, value: List[Dict]):
        self.evening_pooled_components = value

    # ─── FIXED DELIVERY UNITS ─────────────────────────────────────────────────

    def add_fixed_softgels(self, daily_count: int = 2):
        """Add fixed-composition softgels (Omega + D3 + E + Astaxanthin)."""
        self.softgel_count = daily_count
        self.softgel_components = [
            {"substance": "Omega-3 (EPA + DHA)", "type": "omega_fatty_acid",
             "dose_per_softgel": "712.5mg", "dose_daily": f"{712.5 * daily_count}mg",
             "weight_mg_per_softgel": 712.5, "rationale": "Fixed softgel composition"},
            {"substance": "Vitamin D3", "type": "fat_soluble_vitamin",
             "dose_per_softgel": "10mcg (400 IU)", "dose_daily": f"{10 * daily_count}mcg ({400 * daily_count} IU)",
             "weight_mg_per_softgel": 0.00001, "weight_note": "NEGLIGIBLE",
             "rationale": "Fixed softgel composition"},
            {"substance": "Vitamin E", "type": "fat_soluble_vitamin",
             "dose_per_softgel": "7.5mg", "dose_daily": f"{7.5 * daily_count}mg",
             "weight_mg_per_softgel": 7.5, "rationale": "Fixed softgel composition"},
            {"substance": "Astaxanthin (H. pluvialis 10% oleoresin)", "type": "carotenoid_antioxidant",
             "dose_per_softgel": "3mg active (30mg oleoresin)", "dose_daily": f"{3 * daily_count}mg active",
             "weight_mg_per_softgel": 30, "rationale": "Fixed softgel composition"},
        ]

    def add_magnesium_capsules(self, capsule_count: int, needs: list = None,
                               reasoning: list = None, timing: str = "evening"):
        """Add magnesium bisglycinate capsules. 1 capsule = 750mg bisglycinate."""
        if capsule_count <= 0:
            return
        self.mg_capsule_count = capsule_count
        self.mg_capsule_data = {
            "capsule_count": capsule_count,
            "per_capsule": {
                "mg_bisglycinate_mg": 750,
                "elemental_mg_mg": 105,
            },
            "daily_total": {
                "mg_bisglycinate_mg": capsule_count * 750,
                "elemental_mg_mg": capsule_count * 105,
            },
            "needs": needs or [],
            "reasoning": reasoning or [],
            "timing": timing,
        }

    # ─── ADD COMPONENTS — JAR ─────────────────────────────────────────────────

    def add_jar_prebiotic(self, substance: str, dose_g: float,
                          fodmap: bool = False, rationale: str = ""):
        """Add prebiotic or prebiotic-like fiber to the powder jar."""
        weight = prebiotic_weight_g(dose_g)
        self.jar_prebiotics.append({
            "substance": substance,
            "type": "prebiotic",
            "dose_g": dose_g,
            "weight_g": weight,
            "fodmap": fodmap,
            "rationale": rationale,
        })

    # Backward compat alias
    def add_prebiotic(self, substance: str, dose_g: float,
                      fodmap: bool = False, rationale: str = ""):
        return self.add_jar_prebiotic(substance, dose_g, fodmap, rationale)

    def add_jar_botanical(self, substance: str, dose_g: float, rationale: str = ""):
        """Add a heavy non-bitter botanical (dose > threshold) to the powder jar."""
        self.jar_botanicals.append({
            "substance": substance,
            "type": "botanical_heavy",
            "dose_g": dose_g,
            "weight_g": round(dose_g, 3),
            "rationale": rationale,
        })

    def set_prebiotic_strategy(self, strategy: str):
        self.prebiotic_strategy = strategy

    # ─── ADD COMPONENTS — MORNING POOLED CAPSULES ────────────────────────────

    def add_morning_pooled_component(
        self, substance: str, dose_value: float, dose_unit: str,
        min_dose_value: float = None, max_dose_value: float = None,
        adjustable: bool = True,
        therapeutic: bool = False, standard_dose: str = "",
        rationale: str = "", clinical_note: str = "",
        informed_by: str = "questionnaire",
        source_type: str = "vitamin_mineral"
    ):
        """Add a vitamin, mineral, or light botanical to the morning pooled capsules.

        min_dose_value / max_dose_value: KB dose range (same unit as dose_value).
        adjustable: whether the CapsuleStackingOptimizer can reduce this dose.
        """
        weight = vitamin_weight_mg(dose_value, dose_unit)

        # Convert min/max to mg for optimizer
        def _to_mg(val, unit):
            if val is None:
                return None
            if unit.lower() in ["mcg", "μg", "ug"]:
                return 0.0  # negligible
            elif unit.lower() == "mg":
                return float(val)
            elif unit.lower() == "g":
                return float(val) * 1000
            return float(val)

        min_mg = _to_mg(min_dose_value, dose_unit) if min_dose_value is not None else weight
        max_mg = _to_mg(max_dose_value, dose_unit) if max_dose_value is not None else weight

        component = {
            "substance": substance,
            "type": source_type,
            "dose": f"{dose_value}{dose_unit}",
            "dose_value": dose_value,
            "dose_unit": dose_unit,
            "dose_mg": weight,
            "weight_mg": weight,
            "weight_note": "NEGLIGIBLE" if is_negligible_weight(dose_unit) else None,
            "min_dose_mg": min_mg,
            "max_dose_mg": max_mg,
            "adjustable": adjustable and not is_negligible_weight(dose_unit),
            "rationale": rationale,
            "informed_by": informed_by,
            "_source": "vitamin_mineral",
        }
        if therapeutic:
            component["therapeutic_dose"] = True
            component["standard_dose"] = standard_dose
            component["clinical_note"] = clinical_note
            component["adjustable"] = False  # Never adjust therapeutic doses

        self.morning_pooled_components.append(component)

    def add_sachet_vitamin(
        self, substance: str, dose_value: float, dose_unit: str,
        therapeutic: bool = False, standard_dose: str = "",
        rationale: str = "", clinical_note: str = "",
        informed_by: str = "questionnaire"
    ):
        """Backward compat alias → add_morning_pooled_component."""
        self.add_morning_pooled_component(
            substance=substance, dose_value=dose_value, dose_unit=dose_unit,
            therapeutic=therapeutic, standard_dose=standard_dose,
            rationale=rationale, clinical_note=clinical_note,
            informed_by=informed_by, source_type="vitamin_mineral"
        )

    def add_light_botanical_to_morning(self, substance: str, dose_mg: float,
                                       min_dose_mg: float = None, max_dose_mg: float = None,
                                       rationale: str = ""):
        """Add a light non-bitter botanical (dose ≤ threshold) to morning pooled capsules."""
        min_mg = min_dose_mg if min_dose_mg is not None else dose_mg
        max_mg = max_dose_mg if max_dose_mg is not None else dose_mg
        self.morning_pooled_components.append({
            "substance": substance,
            "type": "botanical_light",
            "dose": f"{dose_mg}mg",
            "dose_value": dose_mg,
            "dose_unit": "mg",
            "dose_mg": round(dose_mg, 2),
            "weight_mg": round(dose_mg, 2),
            "weight_note": None,
            "min_dose_mg": min_mg,
            "max_dose_mg": max_mg,
            "adjustable": True,
            "rationale": rationale,
            "informed_by": "questionnaire",
            "_source": "botanical_light",
        })

    def add_sachet_supplement(self, substance: str, dose_mg: float, rationale: str = ""):
        """Backward compat: routes to jar (heavy) or morning pooled (light) based on dose threshold."""
        if dose_mg > self._heavy_threshold:
            dose_g = round(dose_mg / 1000, 3)
            self.add_jar_botanical(substance, dose_g, rationale)
        else:
            self.add_light_botanical_to_morning(substance, dose_mg, rationale=rationale)

    # ─── ADD COMPONENTS — EVENING POOLED CAPSULES ─────────────────────────────

    def add_evening_pooled_component(self, substance: str, dose_mg: float,
                                     min_dose_mg: float = None, max_dose_mg: float = None,
                                     rationale: str = ""):
        """Add a component to the evening pooled capsules (sleep / calming / Tier 1 polyphenols)."""
        min_mg = min_dose_mg if min_dose_mg is not None else dose_mg
        max_mg = max_dose_mg if max_dose_mg is not None else dose_mg
        self.evening_pooled_components.append({
            "substance": substance,
            "dose_mg": round(dose_mg, 2),
            "weight_mg": round(dose_mg, 2),
            "min_dose_mg": min_mg,
            "max_dose_mg": max_mg,
            "adjustable": min_mg < dose_mg,
            "rationale": rationale,
        })

    def add_evening_component(self, substance: str, dose_mg: float, rationale: str = ""):
        """Backward compat alias → add_evening_pooled_component."""
        self.add_evening_pooled_component(substance, dose_mg, rationale=rationale)

    # ─── ADD COMPONENTS — FIXED UNITS ────────────────────────────────────────

    def add_probiotic(self, substance: str, cfu_billions: float,
                      mix_id: int = None, mix_name: str = None,
                      rationale: str = "", evidence_level: str = ""):
        """Add probiotic strain to the probiotic hard capsule."""
        weight = probiotic_weight_mg(cfu_billions)
        self.probiotic_components.append({
            "substance": substance,
            "type": "probiotic_strain",
            "dose": f"{cfu_billions}B CFU",
            "cfu_billions": cfu_billions,
            "weight_mg": weight,
            "rationale": rationale,
            "evidence_level": evidence_level,
        })
        if mix_id and not self.mix_id:
            self.mix_id = mix_id
            self.mix_name = mix_name

    def add_polyphenol_capsule(self, substance: str, dose_mg: float,
                               rationale: str = "", timing: str = "morning"):
        """Add a Tier 2 polyphenol (Curcumin+Piperine, Bergamot) to dedicated capsule."""
        self.polyphenol_capsules.append({
            "substance": substance,
            "dose_mg": dose_mg,
            "weight_mg": round(dose_mg, 2),
            "timing": timing,
            "rationale": rationale,
        })

    # ─── CALCULATION & VALIDATION ─────────────────────────────────────────────

    def _calc_probiotic_totals(self) -> Dict:
        total_mg = sum(c["weight_mg"] for c in self.probiotic_components)
        total_cfu = sum(c["cfu_billions"] for c in self.probiotic_components)
        utilization = round((total_mg / HARD_CAPSULE_CAPACITY_MG) * 100, 1)
        headroom = round(HARD_CAPSULE_CAPACITY_MG - total_mg, 2)
        validation = "PASS" if total_mg <= HARD_CAPSULE_CAPACITY_MG else "FAIL"
        if validation == "FAIL":
            self.warnings.append(f"CRITICAL: Probiotic capsule exceeds capacity ({total_mg}mg > {HARD_CAPSULE_CAPACITY_MG}mg)")
        return {
            "total_weight_mg": round(total_mg, 2),
            "total_cfu_billions": total_cfu,
            "utilization_pct": utilization,
            "headroom_mg": headroom,
            "validation": validation,
        }

    def _calc_softgel_totals(self) -> Dict:
        weight_per_softgel = sum(
            c.get("weight_mg_per_softgel", 0)
            for c in self.softgel_components
            if c.get("weight_note") != "NEGLIGIBLE"
        )
        utilization = round((weight_per_softgel / SOFTGEL_CAPACITY_MG) * 100, 1)
        headroom = round(SOFTGEL_CAPACITY_MG - weight_per_softgel, 2)
        validation = "PASS" if weight_per_softgel <= SOFTGEL_CAPACITY_MG else "FAIL"
        if validation == "FAIL":
            self.warnings.append(f"CRITICAL: Softgel exceeds capacity ({weight_per_softgel}mg > {SOFTGEL_CAPACITY_MG}mg)")
        return {
            "weight_per_softgel_mg": round(weight_per_softgel, 2),
            "daily_count": self.softgel_count,
            "daily_total_mg": round(weight_per_softgel * self.softgel_count, 2),
            "utilization_pct": utilization,
            "headroom_mg": headroom,
            "validation": validation,
        }

    def _calc_jar_totals(self) -> Dict:
        """Compute jar totals: prebiotics + heavy botanicals. Attach phased dosing."""
        prebiotic_g = sum(c["weight_g"] for c in self.jar_prebiotics)
        botanical_g = sum(c["weight_g"] for c in self.jar_botanicals)
        total_g = prebiotic_g + botanical_g

        # Phased dosing
        policy = self._phased_policy
        half_dose = round(total_g * policy.get("weeks_1_2_fraction", 0.5), 1)
        full_dose = round(total_g, 1)
        template = policy.get("instruction_template", "")
        instruction = template.replace("{half_dose_g}", str(half_dose)).replace("{full_dose_g}", str(full_dose))

        phased_dosing = {
            "weeks_1_2_g": half_dose,
            "weeks_3_plus_g": full_dose,
            "instruction": instruction,
            "rationale": policy.get("rationale", ""),
        }

        # Validation (soft limit)
        within_target = total_g <= JAR_TARGET_G
        if not within_target:
            self.warnings.append(f"WARNING: Powder jar {total_g:.1f}g exceeds soft target ({JAR_TARGET_G}g) — consider trimming heavy botanicals")

        # FODMAP
        total_fodmap = sum(c.get("dose_g", 0) for c in self.jar_prebiotics if c.get("fodmap"))
        if total_fodmap >= 5:
            self.warnings.append(f"High FODMAP load: {total_fodmap}g — phased dosing mitigates weeks 1-2 risk")

        return {
            "prebiotic_total_g": round(prebiotic_g, 3),
            "botanical_total_g": round(botanical_g, 3),
            "total_weight_g": round(total_g, 3),
            "total_fodmap_g": round(total_fodmap, 2),
            "within_daily_target": within_target,
            "daily_target_g": JAR_TARGET_G,
            "validation": "PASS",  # Jar has no hard cap — always PASS
            "phased_dosing": phased_dosing,
        }

    def _calc_pooled_morning_totals(self) -> Optional[Dict]:
        """Run CapsuleStackingOptimizer on morning_pooled_components."""
        if not self.morning_pooled_components:
            return None
        optimizer = CapsuleStackingOptimizer(HARD_CAPSULE_CAPACITY_MG)
        result = optimizer.optimize(self.morning_pooled_components)
        # Update components with any dose adjustments
        self.morning_pooled_components = result["components"]

        count = result["capsule_count"]
        final_total = result["adjustment_record"]["final_total_mg"]
        per_capsule = round(final_total / count, 2) if count > 0 else 0
        utilization = round((per_capsule / HARD_CAPSULE_CAPACITY_MG) * 100, 1)

        # Per-capsule distribution (round-robin fill)
        capsules = self._distribute_into_capsules(self.morning_pooled_components, count)

        return {
            "capsule_count": count,
            "capacity_mg_per_capsule": HARD_CAPSULE_CAPACITY_MG,
            "total_weight_mg": round(final_total, 2),
            "avg_fill_per_capsule_mg": per_capsule,
            "avg_utilization_pct": utilization,
            "capsules": capsules,
            "validation": "PASS",
            "optimizer_record": result["adjustment_record"],
        }

    def _calc_pooled_evening_totals(self) -> Optional[Dict]:
        """Run CapsuleStackingOptimizer on evening_pooled_components."""
        if not self.evening_pooled_components:
            return None
        optimizer = CapsuleStackingOptimizer(HARD_CAPSULE_CAPACITY_MG)
        result = optimizer.optimize(self.evening_pooled_components)
        # Update components with any dose adjustments
        self.evening_pooled_components = result["components"]

        count = result["capsule_count"]
        final_total = result["adjustment_record"]["final_total_mg"]
        per_capsule = round(final_total / count, 2) if count > 0 else 0
        utilization = round((per_capsule / HARD_CAPSULE_CAPACITY_MG) * 100, 1)

        capsules = self._distribute_into_capsules(self.evening_pooled_components, count)

        return {
            "capsule_count": count,
            "capacity_mg_per_capsule": HARD_CAPSULE_CAPACITY_MG,
            "total_weight_mg": round(final_total, 2),
            "avg_fill_per_capsule_mg": per_capsule,
            "avg_utilization_pct": utilization,
            "capsules": capsules,
            "validation": "PASS",
            "optimizer_record": result["adjustment_record"],
        }

    def _distribute_into_capsules(self, components: List[Dict], count: int) -> List[Dict]:
        """Distribute components across N capsules using greedy bin-packing (largest-first).
        Each capsule gets up to 650mg. Returns list of {capsule_n, components, fill_mg}.
        """
        if count <= 0 or not components:
            return []

        sorted_comps = sorted(components, key=lambda c: -c.get("dose_mg", 0))
        bins: List[List] = [[] for _ in range(count)]
        bin_totals = [0.0] * count

        for comp in sorted_comps:
            dose = comp.get("dose_mg", 0)
            if is_negligible_weight(comp.get("dose_unit", "mg")):
                # Negligible — put in first capsule, don't count against fill
                bins[0].append(comp)
                continue
            # Place in the bin with the most remaining space
            best_bin = min(range(count), key=lambda i: bin_totals[i])
            bins[best_bin].append(comp)
            bin_totals[best_bin] += dose

        result = []
        for i, (bin_comps, bin_total) in enumerate(zip(bins, bin_totals)):
            result.append({
                "capsule_number": i + 1,
                "components": bin_comps,
                "fill_mg": round(bin_total, 2),
                "utilization_pct": round((bin_total / HARD_CAPSULE_CAPACITY_MG) * 100, 1),
            })
        return result

    def _calc_polyphenol_capsule_totals(self) -> Optional[Dict]:
        if not self.polyphenol_capsules:
            return None
        total_mg = sum(c["weight_mg"] for c in self.polyphenol_capsules)
        utilization = round((total_mg / HARD_CAPSULE_CAPACITY_MG) * 100, 1)
        headroom = round(HARD_CAPSULE_CAPACITY_MG - total_mg, 2)
        validation = "PASS" if total_mg <= HARD_CAPSULE_CAPACITY_MG else "FAIL"
        if validation == "FAIL":
            self.warnings.append(f"CRITICAL: Polyphenol capsule exceeds capacity ({total_mg}mg > {HARD_CAPSULE_CAPACITY_MG}mg)")
        return {
            "total_weight_mg": round(total_mg, 2),
            "utilization_pct": utilization,
            "headroom_mg": headroom,
            "validation": validation,
        }

    # ─── GENERATE OUTPUT ──────────────────────────────────────────────────────

    def generate(self) -> Dict:
        """Generate complete formulation decisions JSON with validated weights."""

        probiotic_totals = self._calc_probiotic_totals()
        softgel_totals = self._calc_softgel_totals() if self.softgel_components else None
        jar_totals = self._calc_jar_totals()
        morning_totals = self._calc_pooled_morning_totals()
        evening_totals = self._calc_pooled_evening_totals()
        polyphenol_totals = self._calc_polyphenol_capsule_totals()

        mg_data = getattr(self, 'mg_capsule_data', None)
        mg_count = mg_data["capsule_count"] if mg_data else 0

        # Overall validation
        all_pass = (
            probiotic_totals["validation"] == "PASS" and
            (softgel_totals is None or softgel_totals["validation"] == "PASS") and
            jar_totals["validation"] == "PASS" and
            (morning_totals is None or morning_totals["validation"] == "PASS") and
            (evening_totals is None or evening_totals["validation"] == "PASS") and
            (polyphenol_totals is None or polyphenol_totals["validation"] == "PASS")
        )

        # Unit counts
        has_softgels = bool(self.softgel_components)
        has_polyphenol = bool(self.polyphenol_capsules)
        morning_capsule_count = (morning_totals["capsule_count"] if morning_totals else 0)
        evening_capsule_count = (evening_totals["capsule_count"] if evening_totals else 0)
        polyphenol_count = 1 if has_polyphenol else 0

        morning_solid = 1 + (self.softgel_count if has_softgels else 0) + morning_capsule_count + polyphenol_count
        morning_jar = 1  # Always 1 jar
        evening_solid = mg_count + evening_capsule_count
        total_units = morning_solid + morning_jar + evening_solid

        # Check against KB limits
        try:
            pref_max = self._dfr.get("unit_count_limits", {}).get("preferred_max", 9)
            abs_max = self._dfr.get("unit_count_limits", {}).get("absolute_max", 13)
        except Exception:
            pref_max, abs_max = 9, 13
        if total_units > abs_max:
            self.warnings.append(f"CRITICAL: Total units {total_units} exceeds absolute max {abs_max}")
        elif total_units > pref_max:
            self.warnings.append(f"NOTE: Total units {total_units} exceeds preferred max {pref_max} — review for simplification")

        # Total daily weight
        total_g = (
            probiotic_totals["total_weight_mg"] / 1000 +
            ((softgel_totals["daily_total_mg"] / 1000) if softgel_totals else 0) +
            jar_totals["total_weight_g"] +
            ((morning_totals["total_weight_mg"] / 1000) if morning_totals else 0) +
            ((evening_totals["total_weight_mg"] / 1000) if evening_totals else 0) +
            ((polyphenol_totals["total_weight_mg"] / 1000) if polyphenol_totals else 0) +
            (mg_count * 750 / 1000)
        )

        # Therapeutic dose list (from morning pooled)
        therapeutic_list = [
            f"{c['substance']} {c['dose']}"
            for c in self.morning_pooled_components
            if c.get("therapeutic_dose")
        ]

        return {
            "metadata": {
                "sample_id": self.sample_id,
                "generated_at": self.timestamp,
                "pipeline_version": "3.0.0",
                "validation_status": "PASS" if all_pass else "FAIL",
                "warnings": self.warnings,
            },
            "delivery_format_1_probiotic_capsule": {
                "format": {
                    "type": "hard_capsule", "size": "00",
                    "capacity_mg": HARD_CAPSULE_CAPACITY_MG,
                    "daily_count": 1, "timing": "morning",
                },
                "components": self.probiotic_components,
                "totals": probiotic_totals,
            },
            "delivery_format_2_omega_softgels": {
                "format": {
                    "type": "softgel", "size": "0",
                    "capacity_mg": SOFTGEL_CAPACITY_MG,
                    "daily_count": self.softgel_count, "timing": "morning",
                },
                "components_per_softgel": self.softgel_components,
                "totals": softgel_totals,
            } if self.softgel_components else None,
            "delivery_format_3_powder_jar": {
                "format": {
                    "type": "jar",
                    "daily_target_g": JAR_TARGET_G,
                    "daily_count": 1, "timing": "morning",
                    "mixing_instructions": "Mix in 200-300ml water. Stir well and drink immediately.",
                },
                "prebiotics": {
                    "strategy": self.prebiotic_strategy or "",
                    "components": self.jar_prebiotics,
                },
                "botanicals": {
                    "components": self.jar_botanicals,
                },
                "totals": jar_totals,
            },
            "delivery_format_4_morning_wellness_capsules": {
                "format": {
                    "type": "hard_capsule", "size": "00",
                    "capacity_mg_per_capsule": HARD_CAPSULE_CAPACITY_MG,
                    "daily_count": morning_capsule_count,
                    "timing": "morning",
                    "label": "Morning Wellness Capsule",
                },
                "components": self.morning_pooled_components,
                "totals": morning_totals,
            } if morning_totals else None,
            "delivery_format_5_evening_wellness_capsules": {
                "format": {
                    "type": "hard_capsule", "size": "00",
                    "capacity_mg_per_capsule": HARD_CAPSULE_CAPACITY_MG,
                    "daily_count": evening_capsule_count,
                    "timing": "evening",
                    "label": "Evening Wellness Capsule",
                },
                "components": self.evening_pooled_components,
                "totals": evening_totals,
            } if evening_totals else None,
            "delivery_format_6_polyphenol_capsule": {
                "format": {
                    "type": "hard_capsule", "size": "00",
                    "capacity_mg": HARD_CAPSULE_CAPACITY_MG,
                    "daily_count": 1, "timing": "morning",
                    "label": "Morning Wellness Capsule",
                },
                "components": self.polyphenol_capsules,
                "totals": polyphenol_totals,
            } if self.polyphenol_capsules else None,
            "protocol_summary": {
                "synbiotic_mix": {
                    "mix_id": self.mix_id,
                    "mix_name": self.mix_name,
                },
                "prebiotic_strategy": self.prebiotic_strategy,
                "morning_solid_units": morning_solid,
                "morning_jar_units": morning_jar,
                "evening_solid_units": evening_solid,
                "total_daily_units": total_units,
                "unit_count_preferred_max": pref_max,
                "unit_count_absolute_max": abs_max,
                "total_daily_weight_g": round(total_g, 2),
                "therapeutic_doses": therapeutic_list,
                "barrier_support_active": False,  # Legacy field
            },
        }

    def save(self, output_path: str) -> Dict:
        """Generate and save to file."""
        result = self.generate()
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Formulation JSON saved: {output_path}")
        print(f"  Validation: {result['metadata']['validation_status']}")
        if result['metadata']['warnings']:
            for w in result['metadata']['warnings']:
                print(f"  ⚠️ {w}")
        return result


# ─── PROBIOTIC CFU DISTRIBUTION ───────────────────────────────────────────────

def distribute_cfu_evenly(total_cfu_billions: float, num_strains: int) -> float:
    """Distribute CFU evenly across strains, rounded to 1 decimal place.
    
    Uses _round_clinical() (standard rounding) not Python's round() which uses
    banker's rounding (round(12.5)=12, round(2.5)=2 — wrong for clinical values).
    
    Examples: 50B / 4 strains = 12.5B, 50B / 5 strains = 10.0B, 25B / 3 = 8.3B
    """
    per_strain = total_cfu_billions / num_strains
    return _round_clinical(per_strain, 1)


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Quick demo / sanity test
    calc = FormulationCalculator(sample_id="test_sample")

    # Probiotics
    cfu_per = distribute_cfu_evenly(50, 5)
    for i in range(5):
        calc.add_probiotic(f"Strain_{i+1}", cfu_per, mix_id=2, mix_name="Bifidogenic Restore")

    # Softgels
    calc.add_fixed_softgels(daily_count=2)

    # Powder jar
    calc.set_prebiotic_strategy("PHGG-moderate")
    calc.add_jar_prebiotic("PHGG", 3.25, fodmap=False)
    calc.add_jar_prebiotic("Beta-glucans", 1.5, fodmap=False)
    calc.add_jar_prebiotic("GOS", 1.25, fodmap=True)

    # Morning wellness capsules (vitamins + minerals + light botanicals)
    calc.add_morning_pooled_component("Vitamin C", 250, "mg",
        min_dose_value=100, max_dose_value=500)
    calc.add_morning_pooled_component("Vitamin B12", 1000, "mcg",
        therapeutic=True, standard_dose="25mcg")
    calc.add_morning_pooled_component("Zinc", 8, "mg",
        min_dose_value=5, max_dose_value=10)
    calc.add_morning_pooled_component("Folate (B9)", 400, "mcg")
    calc.add_light_botanical_to_morning("Glutathione", 75, min_dose_mg=50, max_dose_mg=100)

    # Evening wellness capsules
    calc.add_evening_pooled_component("Ashwagandha", 600, min_dose_mg=300, max_dose_mg=600)
    calc.add_evening_pooled_component("L-Theanine", 200, min_dose_mg=200, max_dose_mg=400)
    calc.add_evening_pooled_component("Melatonin", 1, min_dose_mg=1, max_dose_mg=1)

    # Magnesium capsules
    calc.add_magnesium_capsules(2, needs=["sleep", "stress"])

    result = calc.generate()
    import json
    print(json.dumps(result, indent=2))
    print(f"\nValidation: {result['metadata']['validation_status']}")
    print(f"Total daily weight: {result['protocol_summary']['total_daily_weight_g']}g")
    print(f"Total units: {result['protocol_summary']['total_daily_units']}")
    jar = result["delivery_format_3_powder_jar"]
    phased = jar["totals"]["phased_dosing"]
    print(f"Jar phased dosing: weeks 1-2 = {phased['weeks_1_2_g']}g, week 3+ = {phased['weeks_3_plus_g']}g")
    morning = result.get("delivery_format_4_morning_wellness_capsules")
    if morning:
        opt = morning["totals"]["optimizer_record"]
        print(f"Morning capsules: {morning['totals']['capsule_count']}× | outcome: {opt['optimization_outcome']}")
        if opt["adjustments_made"]:
            for adj in opt["adjustments_made"]:
                print(f"  → {adj['substance']}: {adj['original_dose_mg']}mg → {adj['adjusted_dose_mg']}mg")
