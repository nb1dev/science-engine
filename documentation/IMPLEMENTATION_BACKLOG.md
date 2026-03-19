# Science Engine — Implementation Backlog

**Last updated:** 17 March 2026  
**Owner:** Polina Novikova  

Items captured here are confirmed as needed but deferred. Each entry includes context, current behaviour, and what the improved behaviour should be.

---

## OPEN ITEMS

---

### [FORMULATION-01] Revise prebiotic dosing tiers — decouple from CFU count

**Priority:** Medium  
**Area:** `formulation/rules_engine.py` + `formulation/knowledge_base/prebiotic_rules.json`  

**Current behaviour:**  
Prebiotic dosing range is determined by total CFU count (50B → 75B → 100B tier). LP815 add-on (+5B) can push a 50B mix into the 75B tier, silently widening the acceptable prebiotic range from [4–6g] to [5–7g]. This logic is technically functional but the coupling between probiotic CFU count and prebiotic gram dosing is a clinical approximation that may not hold as mix designs evolve.

**What should change:**  
Evaluate whether prebiotic tiers should be driven by **clinical severity / guild depletion depth** rather than CFU count. Options:
- Tier by guild depletion score (e.g., 1 guild depleted = 50B tier, 2+ = 75B, 3+ = 100B)  
- Tier by mix ID (each mix has its own fixed prebiotic range independent of CFU)  
- Keep CFU-based tiers but exclude LP815 from the tier calculation (LP815 is a gut-brain adjunct, not an ecological restoration strain — its 5B should not affect substrate dosing)

**Immediate low-risk fix available:**  
In `calculate_prebiotic_range()`, pass `lp815_added=False` as the baseline (use mix base CFU only, not total). This prevents LP815 from inflating the prebiotic tier.

---

### [FORMULATION-02] Revise standard probiotic dosages — per-mix CFU rationale

**Priority:** Medium  
**Area:** `formulation/knowledge_base/synbiotic_mixes.json`  

**Current behaviour:**  
All 8 mixes are set to 50B CFU total, distributed evenly across strains. This was a starting point but has no per-mix clinical justification. Mix 7 (Psychobiotic) uses a 40B Probiotical premix + 10B LP815 which already deviates from the pattern. Mix 3 uses 4 strains at 12.5B each. Mix 6 uses 6 strains at ~8.3B each. Per-strain CFU are KB-defined.

**What should change:**  
Review each mix's total CFU and per-strain CFU against current supplier data and clinical evidence. Specific questions to resolve:
- Should dysbiosis mixes (Mix 1, Mix 4, Mix 5) use higher total CFU (75B–100B) given severity of imbalance?  
- Should maintenance Mix 6 use lower total (25B–30B)?  
- Are the per-strain CFU values in v2 KB (`synbiotic_mixes.json`) correct for all suppliers (IFF, Novonesis, Probiotical, Verb Biotics)?  
- LP815 is fixed at 5B — confirm this is still the recommended dose from Verb Biotics

**Reference documents:**  
- `documents/supplement_module/final/probiotic_mixes_v2.xlsx` — supplier-confirmed strain roster  
- `documents/probiotic_table_guide.md`  

---

### [FORMULATION-03] Sample `1421996080050` (batch 009) missing microbiome analysis

**Priority:** Low (data gap, not pipeline bug)  
**Area:** `analysis/nb1_2026_009/1421996080050/`  

**Current behaviour:**  
`generate_formulation.py` and `sanity_check_vs_kb.py` both fail for this sample because `formulation_master_1421996080050.json` does not exist. The pipeline correctly guards against missing input.

**What needs to happen:**  
Run `generate_report.py` for this sample first (microbiome analysis step), then re-run formulation. This is a data pipeline sequencing issue, not a code issue. Sample should be tracked in `work_tracking/sample_tracking_master.csv`.

---

## COMPLETED ITEMS

---

### [FORMULATION-00] Update KB to v2 — new strains and prebiotic substrates

**Completed:** 17 March 2026  
**Changes made:**  
- `synbiotic_mixes.json` → all 8 mixes updated to v2 strains (IFF, Novonesis, Probiotical, Verb Biotics suppliers)  
- `prebiotic_rules.json` → Mix 3 GOS added to must_include; Mix 5 GOS + HMOs promoted; Mix 6/7 notes updated for v2 strain composition  
- Previous versions archived in `knowledge_base/archive/v1_2026_03_17/`  
- Tested against batch 009 (`--no-llm`): 15/15 sanity checks passed for sample `1421504848853`

**Source documents:**  
- `documents/supplement_module/final/probiotic_mixes_v2.xlsx`  
- `documents/supplement_module/final/prebiotics4mixes_v2.md`

---
