"""
Tests for weight_calculator.py — Weight calculations, stacking optimizer, FormulationCalculator.
"""

import pytest
import math
from weight_calculator import (
    probiotic_weight_mg, vitamin_weight_mg, prebiotic_weight_g,
    is_negligible_weight, distribute_cfu_evenly,
    CapsuleStackingOptimizer, FormulationCalculator,
    _round_clinical,
)


# ── Weight functions ──────────────────────────────────────────────────────────

class TestWeightFunctions:
    def test_probiotic_weight(self):
        assert probiotic_weight_mg(10) == 100
        assert probiotic_weight_mg(12.5) == 125
        assert probiotic_weight_mg(0) == 0

    def test_vitamin_weight_mg(self):
        assert vitamin_weight_mg(250, "mg") == 250
        assert vitamin_weight_mg(1.5, "g") == 1500

    def test_vitamin_weight_mcg_negligible(self):
        assert vitamin_weight_mg(400, "mcg") == 0.0
        assert vitamin_weight_mg(200, "μg") == 0.0

    def test_vitamin_weight_unknown_unit(self):
        with pytest.raises(ValueError):
            vitamin_weight_mg(100, "oz")

    def test_prebiotic_weight(self):
        assert prebiotic_weight_g(3.25) == 3.25
        assert prebiotic_weight_g(0) == 0

    def test_is_negligible(self):
        assert is_negligible_weight("mcg")
        assert is_negligible_weight("μg")
        assert not is_negligible_weight("mg")
        assert not is_negligible_weight("g")


# ── _round_clinical ───────────────────────────────────────────────────────────

class TestRoundClinical:
    def test_standard_rounding(self):
        """_round_clinical should use standard rounding, not banker's."""
        assert _round_clinical(12.5, 0) == 13.0  # Python round(12.5) = 12 (banker's)
        assert _round_clinical(2.5, 0) == 3.0    # Python round(2.5) = 2 (banker's)

    def test_one_decimal(self):
        assert _round_clinical(12.5, 1) == 12.5
        assert _round_clinical(12.55, 1) == 12.6
        assert _round_clinical(12.549, 1) == 12.5


# ── distribute_cfu_evenly ────────────────────────────────────────────────────

class TestDistributeCFU:
    def test_even_split(self):
        assert distribute_cfu_evenly(50, 5) == 10.0

    def test_uneven_split(self):
        assert distribute_cfu_evenly(50, 4) == 12.5

    def test_three_strains(self):
        result = distribute_cfu_evenly(50, 3)
        assert result == pytest.approx(16.7, abs=0.1)

    def test_single_strain(self):
        assert distribute_cfu_evenly(50, 1) == 50.0

    def test_cap_distributes_within_active_capacity(self):
        """MAX_CAPSULE_CFU (48B) distributed across N strains must never exceed PROBIOTIC_ACTIVE_CAPACITY_MG."""
        from formulation.weight_calculator import PROBIOTIC_ACTIVE_CAPACITY_MG, CFU_TO_MG_FACTOR
        MAX_CAPSULE_CFU = int(PROBIOTIC_ACTIVE_CAPACITY_MG / CFU_TO_MG_FACTOR)  # = 48
        for n_strains in [1, 2, 3, 4, 5, 6, 8]:
            per_strain = distribute_cfu_evenly(MAX_CAPSULE_CFU, n_strains)
            total_active_mg = round(per_strain * n_strains * CFU_TO_MG_FACTOR, 2)
            assert total_active_mg <= PROBIOTIC_ACTIVE_CAPACITY_MG, (
                f"Cap overflow for {n_strains} strains: {total_active_mg}mg > {PROBIOTIC_ACTIVE_CAPACITY_MG}mg"
            )


# ── CapsuleStackingOptimizer ─────────────────────────────────────────────────

class TestCapsuleStackingOptimizer:
    def test_empty_components(self):
        opt = CapsuleStackingOptimizer(650)
        result = opt.optimize([])
        assert result["capsule_count"] == 0
        assert result["adjustment_record"]["optimization_outcome"] == "fit_without_adjustment"

    def test_fits_in_one_capsule(self):
        opt = CapsuleStackingOptimizer(650)
        components = [
            {"substance": "Vitamin C", "dose_mg": 250, "weight_mg": 250,
             "min_dose_mg": 100, "max_dose_mg": 500, "adjustable": True},
            {"substance": "Zinc", "dose_mg": 8, "weight_mg": 8,
             "min_dose_mg": 5, "max_dose_mg": 15, "adjustable": True},
        ]
        result = opt.optimize(components)
        assert result["capsule_count"] == 1
        assert result["adjustment_record"]["optimization_outcome"] == "fit_without_adjustment"

    def test_needs_two_capsules(self):
        opt = CapsuleStackingOptimizer(650)
        components = [
            {"substance": "A", "dose_mg": 400, "weight_mg": 400,
             "min_dose_mg": 400, "max_dose_mg": 400, "adjustable": False},
            {"substance": "B", "dose_mg": 350, "weight_mg": 350,
             "min_dose_mg": 350, "max_dose_mg": 350, "adjustable": False},
        ]
        result = opt.optimize(components)
        assert result["capsule_count"] == 2

    def test_adjustment_to_fit(self):
        """If adjustable components can be reduced, use fewer capsules."""
        opt = CapsuleStackingOptimizer(650)
        components = [
            {"substance": "A", "dose_mg": 400, "weight_mg": 400,
             "min_dose_mg": 400, "max_dose_mg": 400, "adjustable": False},
            {"substance": "B", "dose_mg": 300, "weight_mg": 300,
             "min_dose_mg": 200, "max_dose_mg": 400, "adjustable": True},
        ]
        result = opt.optimize(components)
        assert result["capsule_count"] == 1
        assert result["adjustment_record"]["optimization_outcome"] == "fit_after_adjustment"
        # B should have been reduced from 300 to 250 (overflow = 700-650 = 50)
        adjusted_b = next(c for c in result["components"] if c["substance"] == "B")
        assert adjusted_b["dose_mg"] == 250


# ── FormulationCalculator ────────────────────────────────────────────────────

class TestFormulationCalculator:
    def test_basic_generate(self):
        calc = FormulationCalculator(sample_id="test_001")

        # Add probiotics — 48B total (480mg) fits within size-0 capsule active capacity (495mg)
        cfu_per = distribute_cfu_evenly(48, 4)
        for i in range(4):
            calc.add_probiotic(f"Strain_{i+1}", cfu_per, mix_id=2, mix_name="Bifidogenic Restore")

        # Add prebiotics
        calc.set_prebiotic_strategy("PHGG-moderate")
        calc.add_prebiotic("PHGG", 3.0, fodmap=False)
        calc.add_prebiotic("GOS", 1.5, fodmap=True)

        result = calc.generate()

        # Metadata
        assert result["metadata"]["sample_id"] == "test_001"
        assert result["metadata"]["validation_status"] == "PASS"

        # Probiotic capsule
        probiotic = result["delivery_format_1_probiotic_capsule"]
        assert len(probiotic["components"]) == 4
        assert probiotic["totals"]["total_cfu_billions"] == pytest.approx(48, abs=0.5)
        assert probiotic["totals"]["validation"] == "PASS"

        # Jar
        jar = result["delivery_format_3_powder_jar"]
        assert jar["totals"]["prebiotic_total_g"] == pytest.approx(4.5, abs=0.01)
        assert jar["totals"]["total_fodmap_g"] == pytest.approx(1.5, abs=0.01)

        # Protocol summary
        assert result["protocol_summary"]["total_daily_units"] >= 2

    def test_softgels(self):
        calc = FormulationCalculator(sample_id="test_002")
        calc.add_probiotic("Strain1", 50, mix_id=6, mix_name="Maintenance")
        calc.add_fixed_softgels(daily_count=2)
        calc.set_prebiotic_strategy("test")
        result = calc.generate()

        softgels = result["delivery_format_2_omega_softgels"]
        assert softgels is not None
        assert softgels["totals"]["daily_count"] == 2
        assert softgels["totals"]["validation"] == "PASS"

    def test_morning_wellness_capsules(self):
        calc = FormulationCalculator(sample_id="test_003")
        calc.add_probiotic("Strain1", 50, mix_id=6, mix_name="Maintenance")
        calc.set_prebiotic_strategy("test")

        calc.add_morning_pooled_component("Vitamin C", 250, "mg")
        calc.add_morning_pooled_component("Zinc", 8, "mg")
        calc.add_morning_pooled_component("Folate", 400, "mcg")
        calc.add_light_botanical_to_morning("Glutathione", 75)

        result = calc.generate()
        mwc = result["delivery_format_4_morning_wellness_capsules"]
        assert mwc is not None
        assert mwc["totals"]["capsule_count"] >= 1
        assert mwc["totals"]["validation"] == "PASS"

    def test_evening_wellness_capsules(self):
        calc = FormulationCalculator(sample_id="test_004")
        calc.add_probiotic("Strain1", 50, mix_id=6, mix_name="Maintenance")
        calc.set_prebiotic_strategy("test")

        calc.add_evening_component("Ashwagandha", 600)
        calc.add_evening_component("L-Theanine", 200)

        result = calc.generate()
        ewc = result["delivery_format_5_evening_wellness_capsules"]
        assert ewc is not None
        assert ewc["totals"]["capsule_count"] >= 1

    def test_magnesium_capsules(self):
        calc = FormulationCalculator(sample_id="test_005")
        calc.add_probiotic("Strain1", 50, mix_id=6, mix_name="Maintenance")
        calc.set_prebiotic_strategy("test")
        calc.add_magnesium_capsules(2, needs=["sleep", "stress"])

        result = calc.generate()
        proto = result["protocol_summary"]
        assert proto["evening_solid_units"] >= 2

    def test_polyphenol_capsule(self):
        calc = FormulationCalculator(sample_id="test_006")
        calc.add_probiotic("Strain1", 50, mix_id=6, mix_name="Maintenance")
        calc.set_prebiotic_strategy("test")
        calc.add_polyphenol_capsule("Curcumin + Piperine", 505)

        result = calc.generate()
        poly = result["delivery_format_6_polyphenol_capsule"]
        assert poly is not None
        assert poly["totals"]["total_weight_mg"] == 505

    def test_probiotic_capacity_overflow(self):
        """Probiotic active content exceeding 495mg (size-0 capsule) should produce FAIL + warning."""
        calc = FormulationCalculator(sample_id="test_overflow")
        # 50B CFU = 500mg > 495mg active capacity
        calc.add_probiotic("BigStrain", 50, mix_id=1, mix_name="Dysbiosis")
        calc.set_prebiotic_strategy("test")
        result = calc.generate()
        assert result["delivery_format_1_probiotic_capsule"]["totals"]["validation"] == "FAIL"
        assert any("CRITICAL" in w for w in result["metadata"]["warnings"])

    def test_jar_botanical(self):
        calc = FormulationCalculator(sample_id="test_jar")
        calc.add_probiotic("Strain1", 50, mix_id=6, mix_name="Maintenance")
        calc.set_prebiotic_strategy("test")
        calc.add_jar_botanical("L-Glutamine", 5.0, rationale="Gut barrier support")

        result = calc.generate()
        jar = result["delivery_format_3_powder_jar"]
        assert jar["totals"]["botanical_total_g"] == 5.0

    def test_phased_dosing(self):
        calc = FormulationCalculator(sample_id="test_phased")
        calc.add_probiotic("Strain1", 50, mix_id=6, mix_name="Maintenance")
        calc.set_prebiotic_strategy("test")
        calc.add_prebiotic("PHGG", 4.0)
        calc.add_prebiotic("GOS", 2.0)

        result = calc.generate()
        phased = result["delivery_format_3_powder_jar"]["totals"]["phased_dosing"]
        assert phased["weeks_1_2_g"] == pytest.approx(3.0, abs=0.1)
        assert phased["weeks_3_plus_g"] == pytest.approx(6.0, abs=0.1)
