#!/usr/bin/env python3
"""
Weight Calculator — Formulation weight calculations with ZERO error tolerance.

Computes exact weights for all delivery formats, validates capacity limits,
and generates the formulation_decisions JSON that serves as single source
of truth for all downstream outputs.

Weight Formulas:
  - Probiotics: weight_mg = cfu_billions × 10
  - Omega oils: weight_mg = dose_mg (1:1)
  - Vitamins (mcg): NEGLIGIBLE — does NOT reduce capacity
  - Vitamins (mg): weight_mg = dose_mg (1:1)
  - Prebiotics: weight_g = dose_g (1:1)
  - Amino acids/botanicals: weight_mg = dose_mg (1:1)

Capacity Limits:
  - Hard capsule: 650mg
  - Softgel: 750mg per unit
  - Sachet: 19g
"""

import json
from datetime import datetime
from typing import Dict, List, Optional


# ─── CONSTANTS ────────────────────────────────────────────────────────────────

HARD_CAPSULE_CAPACITY_MG = 650
SOFTGEL_CAPACITY_MG = 750
SACHET_CAPACITY_G = 19.0
EVENING_CAPSULE_CAPACITY_MG = 650

# CFU to weight conversion
CFU_TO_MG_FACTOR = 10  # 1B CFU = 10mg powder


# ─── WEIGHT CALCULATION FUNCTIONS ─────────────────────────────────────────────

def probiotic_weight_mg(cfu_billions: float) -> float:
    """Calculate probiotic powder weight from CFU count.
    Formula: weight_mg = cfu_billions × 10
    Example: 50B CFU = 500mg
    """
    return round(cfu_billions * CFU_TO_MG_FACTOR, 2)


def omega_weight_mg(dose_mg: float) -> float:
    """Calculate omega oil weight. 1:1 relationship.
    Example: 750mg omega = 750mg weight
    """
    return round(dose_mg, 2)


def vitamin_weight_mg(dose_value: float, dose_unit: str) -> float:
    """Calculate vitamin weight.
    - mcg doses: NEGLIGIBLE (returns near-zero, stored as 0 for clean display)
    - mg doses: 1:1 relationship
    """
    if dose_unit.lower() in ["mcg", "μg", "ug"]:
        # Negligible weight — effectively 0mg for capacity purposes
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


def amino_acid_weight_mg(dose_mg: float) -> float:
    """Calculate amino acid/botanical weight. 1:1 relationship."""
    return round(dose_mg, 2)


def is_negligible_weight(dose_unit: str) -> bool:
    """Check if a dose unit produces negligible weight (mcg vitamins)."""
    return dose_unit.lower() in ["mcg", "μg", "ug"]


# ─── DELIVERY FORMAT BUILDERS ─────────────────────────────────────────────────

class FormulationCalculator:
    """Build and validate a complete formulation with weight calculations."""

    def __init__(self, sample_id: str):
        self.sample_id = sample_id
        self.timestamp = datetime.utcnow().isoformat() + "Z"

        # Component storage
        self.probiotic_components = []
        self.softgel_components = []
        self.sachet_prebiotics = []
        self.sachet_vitamins = []
        self.sachet_supplements = []
        self.evening_components = []
        self.polyphenol_capsules = []

        # Metadata
        self.mix_id = None
        self.mix_name = None
        self.prebiotic_strategy = None
        self.softgel_count = 2  # Default 2 softgels
        self.warnings = []

    # ─── FIXED DELIVERY UNITS ─────────────────────────────────────────────

    def add_fixed_softgels(self, daily_count: int = 2):
        """Add fixed-composition softgels (Omega + D3 + E + Astaxanthin).
        This is a pre-mixed unit — same for every client."""
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
        self.softgel_fixed = True

    def add_magnesium_capsules(self, capsule_count: int, needs: list = None, reasoning: list = None, timing: str = "evening"):
        """Add magnesium bisglycinate capsules.
        1 capsule = 750mg Mg bisglycinate = 105mg elemental Mg.
        Timing determined by timing engine (evening or morning)."""
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

    # ─── ADD COMPONENTS ───────────────────────────────────────────────────

    def add_probiotic(
        self, substance: str, cfu_billions: float,
        mix_id: int = None, mix_name: str = None,
        rationale: str = "", evidence_level: str = ""
    ):
        """Add probiotic strain to hard capsule."""
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

    def add_omega(self, substance: str, dose_mg_per_softgel: float, rationale: str = ""):
        """Add omega-3 to softgels."""
        weight = omega_weight_mg(dose_mg_per_softgel)
        self.softgel_components.append({
            "substance": substance,
            "type": "omega_fatty_acid",
            "dose_per_softgel": f"{dose_mg_per_softgel}mg",
            "dose_daily": f"{dose_mg_per_softgel * self.softgel_count}mg",
            "weight_mg_per_softgel": weight,
            "rationale": rationale,
        })

    def add_fat_soluble_vitamin(
        self, substance: str, dose_value: float, dose_unit: str,
        dose_iu: int = None, therapeutic: bool = False,
        standard_dose: str = "", rationale: str = "", clinical_note: str = ""
    ):
        """Add fat-soluble vitamin to softgels (with omega)."""
        weight = vitamin_weight_mg(dose_value, dose_unit)
        dose_str = f"{dose_value}{dose_unit}"
        if dose_iu:
            dose_str += f" ({dose_iu} IU)"

        component = {
            "substance": substance,
            "type": "fat_soluble_vitamin",
            "dose_per_softgel": f"{dose_value / self.softgel_count}{dose_unit}",
            "dose_daily": dose_str,
            "weight_mg_per_softgel": round(weight / self.softgel_count, 6),
            "weight_note": "NEGLIGIBLE" if is_negligible_weight(dose_unit) else None,
            "rationale": rationale,
        }
        if therapeutic:
            component["therapeutic_dose"] = True
            component["standard_dose"] = standard_dose
            component["clinical_note"] = clinical_note
        self.softgel_components.append(component)

    def add_vitamin_e_barrier(self, dose_mg: float = 180):
        """Add Vitamin E for barrier support (reduces omega capacity)."""
        per_softgel = dose_mg / self.softgel_count
        self.softgel_components.append({
            "substance": "Vitamin E",
            "type": "fat_soluble_vitamin",
            "dose_per_softgel": f"{per_softgel}mg",
            "dose_daily": f"{dose_mg}mg",
            "weight_mg_per_softgel": per_softgel,
            "weight_note": "Significant weight — reduces omega capacity",
            "rationale": "Barrier support for food-sensitive pattern",
        })
        self.barrier_support_active = True

    def add_prebiotic(self, substance: str, dose_g: float, fodmap: bool = False, rationale: str = ""):
        """Add prebiotic to sachet."""
        weight = prebiotic_weight_g(dose_g)
        self.sachet_prebiotics.append({
            "substance": substance,
            "type": "prebiotic",
            "dose_g": dose_g,
            "weight_g": weight,
            "fodmap": fodmap,
            "rationale": rationale,
        })

    def set_prebiotic_strategy(self, strategy: str):
        self.prebiotic_strategy = strategy

    def add_sachet_vitamin(
        self, substance: str, dose_value: float, dose_unit: str,
        therapeutic: bool = False, standard_dose: str = "",
        rationale: str = "", clinical_note: str = ""
    ):
        """Add water-soluble vitamin/mineral to sachet."""
        weight = vitamin_weight_mg(dose_value, dose_unit)
        component = {
            "substance": substance,
            "type": "vitamin_mineral",
            "dose": f"{dose_value}{dose_unit}",
            "weight_mg": weight,
            "rationale": rationale,
        }
        if therapeutic:
            component["therapeutic_dose"] = True
            component["standard_dose"] = standard_dose
            component["clinical_note"] = clinical_note
        self.sachet_vitamins.append(component)

    def add_sachet_supplement(self, substance: str, dose_mg: float, rationale: str = ""):
        """Add amino acid/botanical to sachet."""
        weight = amino_acid_weight_mg(dose_mg)
        self.sachet_supplements.append({
            "substance": substance,
            "type": "amino_acid_botanical",
            "dose_mg": dose_mg,
            "weight_mg": weight,
            "rationale": rationale,
        })

    def add_evening_component(self, substance: str, dose_mg: float, rationale: str = ""):
        """Add component to evening capsule."""
        self.evening_components.append({
            "substance": substance,
            "dose_mg": dose_mg,
            "weight_mg": round(dose_mg, 2),
            "rationale": rationale,
        })

    def add_polyphenol_capsule(self, substance: str, dose_mg: float, rationale: str = "", timing: str = "morning"):
        """Add a dedicated polyphenol capsule (Tier 2 — Curcumin+Piperine or Bergamot).
        These are too large to share with the evening capsule."""
        self.polyphenol_capsules.append({
            "substance": substance,
            "dose_mg": dose_mg,
            "weight_mg": round(dose_mg, 2),
            "timing": timing,
            "rationale": rationale,
        })

    # ─── CALCULATION & VALIDATION ─────────────────────────────────────────

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
        # Calculate per-softgel weight (only count actual weight, skip negligible)
        weight_per_softgel = 0
        for c in self.softgel_components:
            w = c.get("weight_mg_per_softgel", 0)
            if c.get("weight_note") == "NEGLIGIBLE":
                continue  # Don't count negligible weight against capacity
            weight_per_softgel += w

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

    def _calc_sachet_totals(self) -> Dict:
        prebiotic_g = sum(c["weight_g"] for c in self.sachet_prebiotics)
        vitamin_mg = sum(c["weight_mg"] for c in self.sachet_vitamins)
        vitamin_g = vitamin_mg / 1000
        supplement_mg = sum(c["weight_mg"] for c in self.sachet_supplements)
        supplement_g = supplement_mg / 1000

        total_g = prebiotic_g + vitamin_g + supplement_g
        utilization = round((total_g / SACHET_CAPACITY_G) * 100, 1)
        headroom = round(SACHET_CAPACITY_G - total_g, 2)
        validation = "PASS" if total_g <= SACHET_CAPACITY_G else "FAIL"

        if validation == "FAIL":
            self.warnings.append(f"CRITICAL: Sachet exceeds capacity ({total_g}g > {SACHET_CAPACITY_G}g)")

        # FODMAP warning
        total_fodmap = sum(c["dose_g"] for c in self.sachet_prebiotics if c.get("fodmap"))
        if total_fodmap >= 5:
            self.warnings.append(f"High FODMAP load: {total_fodmap}g — monitor bloating weeks 1-2")

        return {
            "prebiotic_total_g": round(prebiotic_g, 3),
            "vitamin_mineral_total_g": round(vitamin_g, 4),
            "supplement_total_g": round(supplement_g, 4),
            "total_weight_g": round(total_g, 3),
            "total_fodmap_g": round(total_fodmap, 2),
            "utilization_pct": utilization,
            "headroom_g": headroom,
            "validation": validation,
        }

    def _calc_evening_totals(self) -> Optional[Dict]:
        if not self.evening_components:
            return None

        total_mg = sum(c["weight_mg"] for c in self.evening_components)
        utilization = round((total_mg / EVENING_CAPSULE_CAPACITY_MG) * 100, 1)
        headroom = round(EVENING_CAPSULE_CAPACITY_MG - total_mg, 2)
        validation = "PASS" if total_mg <= EVENING_CAPSULE_CAPACITY_MG else "FAIL"

        if validation == "FAIL":
            self.warnings.append(f"CRITICAL: Evening capsule exceeds capacity ({total_mg}mg > {EVENING_CAPSULE_CAPACITY_MG}mg)")

        return {
            "total_weight_mg": round(total_mg, 2),
            "utilization_pct": utilization,
            "headroom_mg": headroom,
            "validation": validation,
        }

    def _calc_evening_2_totals(self) -> Optional[Dict]:
        """Calculate totals for evening capsule 2 (overflow capsule)."""
        ec2 = getattr(self, 'evening_capsule_2', [])
        if not ec2:
            return None

        total_mg = sum(c.get("weight_mg", c.get("dose_mg", 0)) for c in ec2)
        utilization = round((total_mg / EVENING_CAPSULE_CAPACITY_MG) * 100, 1)
        headroom = round(EVENING_CAPSULE_CAPACITY_MG - total_mg, 2)
        validation = "PASS" if total_mg <= EVENING_CAPSULE_CAPACITY_MG else "FAIL"

        if validation == "FAIL":
            self.warnings.append(f"CRITICAL: Evening capsule 2 exceeds capacity ({total_mg}mg > {EVENING_CAPSULE_CAPACITY_MG}mg)")

        return {
            "total_weight_mg": round(total_mg, 2),
            "utilization_pct": utilization,
            "headroom_mg": headroom,
            "validation": validation,
        }

    def _calc_polyphenol_capsule_totals(self) -> Optional[Dict]:
        """Calculate totals for dedicated polyphenol capsule(s) (Tier 2)."""
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

    # ─── GENERATE OUTPUT ──────────────────────────────────────────────────

    def generate(self) -> Dict:
        """Generate complete formulation decisions JSON with validated weights."""

        probiotic_totals = self._calc_probiotic_totals()
        softgel_totals = self._calc_softgel_totals()
        sachet_totals = self._calc_sachet_totals()
        evening_totals = self._calc_evening_totals()
        evening_2_totals = self._calc_evening_2_totals()
        polyphenol_totals = self._calc_polyphenol_capsule_totals()

        # Overall validation
        all_pass = (
            probiotic_totals["validation"] == "PASS" and
            softgel_totals["validation"] == "PASS" and
            sachet_totals["validation"] == "PASS" and
            (evening_totals is None or evening_totals["validation"] == "PASS") and
            (evening_2_totals is None or evening_2_totals["validation"] == "PASS") and
            (polyphenol_totals is None or polyphenol_totals["validation"] == "PASS")
        )

        # Mg capsule data
        mg_data = getattr(self, 'mg_capsule_data', None)
        mg_count = mg_data["capsule_count"] if mg_data else 0

        # Evening capsule 2 (overflow)
        ec2_list = getattr(self, 'evening_capsule_2', [])
        has_ec2 = bool(ec2_list)

        # Count units — Mg is always evening, softgels only if they have components
        has_softgels = bool(self.softgel_components)
        has_polyphenol_capsule = bool(self.polyphenol_capsules)
        morning_solid = 1 + (self.softgel_count if has_softgels else 0) + (1 if has_polyphenol_capsule else 0)
        morning_drinks = 1  # sachet
        evening_solid = mg_count + (1 if self.evening_components else 0) + (1 if has_ec2 else 0)

        # Total daily weight
        total_g = (
            probiotic_totals["total_weight_mg"] / 1000 +
            softgel_totals["daily_total_mg"] / 1000 +
            sachet_totals["total_weight_g"] +
            (mg_count * 750 / 1000) +  # Mg capsules
            (evening_totals["total_weight_mg"] / 1000 if evening_totals else 0) +
            (evening_2_totals["total_weight_mg"] / 1000 if evening_2_totals else 0) +
            (polyphenol_totals["total_weight_mg"] / 1000 if polyphenol_totals else 0)
        )

        # Therapeutic dose list
        therapeutic_list = []
        for c in self.softgel_components:
            if c.get("therapeutic_dose"):
                therapeutic_list.append(f"{c['substance']} {c['dose_daily']}")
        for c in self.sachet_vitamins:
            if c.get("therapeutic_dose"):
                therapeutic_list.append(f"{c['substance']} {c['dose']}")

        return {
            "metadata": {
                "sample_id": self.sample_id,
                "generated_at": self.timestamp,
                "pipeline_version": "1.0.0",
                "validation_status": "PASS" if all_pass else "FAIL",
                "warnings": self.warnings,
            },
            "delivery_format_1_probiotic_capsule": {
                "format": {
                    "type": "hard_capsule", "size": "00",
                    "capacity_mg": HARD_CAPSULE_CAPACITY_MG,
                    "daily_count": 1, "timing": "morning"
                },
                "components": self.probiotic_components,
                "totals": probiotic_totals,
            },
            "delivery_format_2_omega_softgels": {
                "format": {
                    "type": "softgel", "size": "0",
                    "capacity_mg": SOFTGEL_CAPACITY_MG,
                    "daily_count": self.softgel_count, "timing": "morning"
                },
                "components_per_softgel": self.softgel_components,
                "totals": softgel_totals,
            } if self.softgel_components else None,
            "delivery_format_3_daily_sachet": {
                "format": {
                    "type": "sachet",
                    "capacity_g": SACHET_CAPACITY_G,
                    "daily_count": 1, "timing": "morning",
                },
                "prebiotics": {
                    "strategy": self.prebiotic_strategy or "",
                    "components": self.sachet_prebiotics,
                },
                "vitamins_minerals": {
                    "components": self.sachet_vitamins,
                },
                "supplements": {
                    "components": self.sachet_supplements,
                },
                "totals": sachet_totals,
            },
            "delivery_format_4_evening_capsule": {
                "format": {
                    "type": "hard_capsule",
                    "capacity_mg": EVENING_CAPSULE_CAPACITY_MG,
                    "daily_count": 1 if self.evening_components else 0,
                    "timing": "evening",
                },
                "components": self.evening_components,
                "totals": evening_totals,
            } if self.evening_components else None,
            "delivery_format_4b_evening_capsule_2": {
                "format": {
                    "type": "hard_capsule",
                    "capacity_mg": EVENING_CAPSULE_CAPACITY_MG,
                    "daily_count": 1 if has_ec2 else 0,
                    "timing": "evening",
                },
                "components": ec2_list,
                "totals": evening_2_totals,
            } if has_ec2 else None,
            "delivery_format_5_polyphenol_capsule": {
                "format": {
                    "type": "hard_capsule",
                    "size": "00",
                    "capacity_mg": HARD_CAPSULE_CAPACITY_MG,
                    "daily_count": 1 if self.polyphenol_capsules else 0,
                    "timing": "morning",
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
                "morning_drinks": morning_drinks,
                "evening_solid_units": evening_solid,
                "total_daily_units": morning_solid + morning_drinks + evening_solid,
                "total_daily_weight_g": round(total_g, 2),
                "therapeutic_doses": therapeutic_list,
                "barrier_support_active": False,  # Legacy field — softgel is always fixed composition
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
    """
    Distribute CFU evenly across strains.
    Rule: Round to nearest standard CFU unit (5B, 8B, 10B).
    """
    per_strain = total_cfu_billions / num_strains
    # Round to nearest 5B or nearest whole
    if per_strain >= 10:
        return round(per_strain / 5) * 5  # Round to nearest 5
    elif per_strain >= 5:
        return round(per_strain)  # Round to nearest 1
    else:
        return round(per_strain, 1)  # Keep decimal for small values


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Quick demo/test
    calc = FormulationCalculator(sample_id="test_sample")

    # Add 5-strain mix at 50B total
    cfu_per = distribute_cfu_evenly(50, 5)
    for i in range(5):
        calc.add_probiotic(f"Strain_{i+1}", cfu_per, mix_id=2, mix_name="Bifidogenic Restore")

    # Omega + Vitamin D
    calc.add_omega("Omega-3 (DHA & EPA)", 750, "Mood + brain + skin")
    calc.add_fat_soluble_vitamin("Vitamin D3", 25, "mcg", dose_iu=1000, rationale="Immune support")

    # Prebiotics
    calc.set_prebiotic_strategy("PHGG-moderate")
    calc.add_prebiotic("PHGG", 3.25, fodmap=False)
    calc.add_prebiotic("Beta-glucans", 1.5, fodmap=False)
    calc.add_prebiotic("GOS", 1.25, fodmap=True)

    # Sachet vitamins
    calc.add_sachet_vitamin("Vitamin C", 250, "mg")
    calc.add_sachet_vitamin("Vitamin B12", 1000, "mcg", therapeutic=True, standard_dose="25mcg")

    # Sachet supplements
    calc.add_sachet_supplement("L-Theanine", 200, "Calm focus")

    result = calc.generate()
    print(json.dumps(result, indent=2))
    print(f"\nValidation: {result['metadata']['validation_status']}")
    print(f"Total daily weight: {result['protocol_summary']['total_daily_weight_g']}g")
    print(f"Total units: {result['protocol_summary']['total_daily_units']}")
