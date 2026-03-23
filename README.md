# science-engine

Bioinformatics and AI pipeline for the NB1 Health platform. Processes raw microbiome sequencing data into personalised health reports and supplement formulations.

## What it does

Takes a sample through three sequential stages:

1. **Bioinformatics** — QC, GMWI2 scoring, metabolic pathway analysis, metric calculation
2. **Report generation** — Scoring, guild analysis, root cause inference, narrative generation, platform JSON output
3. **Formulation** — Probiotic mix selection, prebiotic design, supplement selection, dose optimisation, manufacturing recipe

---

## Repository structure

```
science-engine/
├── bioinformatics/                     # Stage 1: sequencing data processing
│   ├── run_gmwi2.sh                    # GMWI2 scoring shell runner
│   ├── qc_precheck.py                  # QC checks before pipeline entry
│   ├── calculate_metrics.py            # Guild abundances, diversity, CLR
│   ├── core_pathway_analysis.py        # Metabolic pathway inference
│   ├── knowledge_base/
│   │   ├── GMWI2_taxa_coefficients.tsv
│   │   └── core_pathways_keywords.tsv
│   └── models/
│       └── GMWI2_model.joblib
│
├── report/                             # Stage 2: health report generation
│   ├── generate_report.py              # Main entry point (full report)
│   ├── generate_health_report.py       # Platform health report fields
│   ├── generate_narrative_report.py    # Narrative markdown report
│   ├── scoring.py                      # Guild scoring logic
│   ├── parse_metrics.py                # Parses bioinformatics output
│   ├── thresholds.py                   # Population threshold definitions
│   ├── update_population_thresholds.py # Threshold recalibration utility
│   ├── overview_fields.py              # Report overview section
│   ├── root_causes_fields.py           # Root cause inference fields
│   ├── action_plan_fields.py           # Action plan fields
│   ├── narratives.py                   # Narrative text generation
│   ├── assemble_interpretations.py     # Assembles interpretation JSON
│   ├── formulation_bridge.py           # Passes report outputs to formulation
│   ├── HEALTH_REPORT_SCHEMA.md         # Output schema documentation
│   ├── START_THRESHOLDS_WATCHER.sh     # Dev utility: watch threshold changes
│   ├── knowledge_base/
│   │   ├── population_thresholds.json
│   │   ├── interpretation_rules.json
│   │   ├── dysbiosis_rules.json
│   │   ├── guild_interpretation.json
│   │   ├── functional_pathways.json
│   │   ├── metabolic_functions.json
│   │   ├── dietary_inference.json
│   │   ├── root_cause_domain_rules.json
│   │   ├── questionnaire_microbiome_evidence.json
│   │   ├── vitamin_signals.json
│   │   ├── quality_and_accuracy.json
│   │   ├── static_content.json
│   │   └── concise_report_framework.md
│   ├── output/                         # Report output staging (gitignored)
│   └── archive/                        # Retired report scripts (read-only)
│       ├── generate_dashboard.py
│       ├── generate_report_analysis_only.py
│       ├── platform_mapping.py
│       └── platform_payload_schema.json
│
├── formulation/                        # Stage 3: supplement formulation engine
│   ├── generate_formulation.py         # Main entry point (morning pipeline)
│   ├── generate_formulation_evening.py # Evening capsule pipeline
│   ├── rules_engine.py                 # Deterministic rules (mix, sleep, etc.)
│   ├── dose_optimizer.py               # Dose capping and CFU optimisation
│   ├── weight_calculator.py            # Ingredient weight calculations
│   ├── parse_inputs.py                 # Input parsing and validation
│   ├── platform_mapping.py             # Formulation → platform field mapping
│   ├── recipe_renderer.py              # Manufacturing recipe output
│   ├── dashboard_renderer.py           # Client dashboard rendering
│   ├── formulation_validator.py        # Post-formulation validation checks
│   ├── apply_medication_timing_override.py  # Medication timing adjustments
│   ├── models.py                       # Pydantic data models
│   ├── __init__.py
│   │
│   ├── stages/                         # Modular pipeline (refactored 19 Mar 2026)
│   │   ├── s01_parse_inputs.py         # Input ingestion
│   │   ├── s02_clinical_analysis.py    # Clinical parameter analysis
│   │   ├── s03_medication_screening.py # Drug–supplement interaction check
│   │   ├── s04_deterministic_rules.py  # Mix selection + deterministic logic
│   │   ├── s05_formulation_decisions.py # LLM supplement + prebiotic decisions
│   │   ├── s06_post_processing.py      # Post-processing and overrides
│   │   ├── s07_weight_calculation.py   # Weight and dose finalisation
│   │   ├── s08_narratives.py           # Narrative generation
│   │   ├── s09_output.py               # Output assembly and export
│   │   └── __init__.py
│   │
│   ├── llm/                            # LLM integrations (AWS Bedrock / Claude)
│   │   ├── bedrock_client.py           # Bedrock API client
│   │   ├── clinical_analyzer.py        # LLM clinical context analysis
│   │   ├── evidence_retriever.py       # Evidence retrieval for decisions
│   │   ├── medication_screener.py      # LLM medication interaction check
│   │   ├── mix_selector.py             # Mix selection (calls offline deterministic)
│   │   ├── narrative_generator.py      # LLM narrative text generation
│   │   ├── prebiotic_designer.py       # LLM prebiotic blend design
│   │   ├── sanity_checker.py           # LLM output sanity validation
│   │   ├── supplement_selector.py      # LLM supplement selection
│   │   └── __init__.py
│   │
│   ├── builders/                       # Formulation object builders
│   │   └── __init__.py
│   │
│   ├── filters/                        # Ingredient and dose filters
│   │   └── __init__.py
│   │
│   ├── tests/                          # Formulation test suite
│   │   ├── conftest.py
│   │   ├── test_dose_optimizer.py
│   │   ├── test_end_to_end.py
│   │   ├── test_evening_pipeline.py
│   │   ├── test_llm_decisions.py
│   │   ├── test_models.py
│   │   ├── test_parse_inputs.py
│   │   ├── test_pipeline_stages.py
│   │   ├── test_platform_mapping.py
│   │   ├── test_post_processing.py
│   │   ├── test_regression.py
│   │   ├── test_rules_engine.py
│   │   ├── test_shared.py
│   │   ├── test_weight_calculator.py
│   │   └── __init__.py
│   │
│   ├── knowledge_base/
│   │   ├── synbiotic_mixes.json        # 8 probiotic mixes + strain definitions
│   │   ├── supplements_nonvitamins.json
│   │   ├── vitamins_minerals.json
│   │   ├── therapeutic_doses.json
│   │   ├── dose_optimization_rules.json
│   │   ├── prebiotic_rules.json
│   │   ├── delivery_format_rules.json
│   │   ├── timing_rules.json
│   │   ├── medication_interactions.json
│   │   ├── sensitivity_thresholds.json
│   │   ├── clr_decision_rules.json
│   │   ├── goal_to_health_claim.json
│   │   └── archive/                    # Retired KB versions (read-only)
│   │       ├── sachet_architecture_2026_03_17/
│   │       └── v1_2026_03_17/
│   │
│   └── archive/                        # Retired formulation engines (read-only)
│       ├── monolith_20260319/          # Pre-refactor monolith (frozen 19 Mar 2026)
│       └── sachet_architecture_2026_03_17/  # Sachet-format prototype
│
├── pipeline/                           # Orchestration scripts
│   ├── run_sample_analysis.sh          # Full pipeline entry point
│   └── upload_reports.sh               # Upload outputs to S3
│
├── shared/                             # Shared utilities (report + formulation)
│   ├── guild_priority.py               # Guild priority ranking logic
│   ├── formatting.py                   # Shared output formatting helpers
│   └── __init__.py
│
├── documentation/                      # Architecture and specs
│   ├── PROJECT_WIKI.md                 # Full architecture and module reference
│   ├── PIPELINE_DOCUMENTATION.md       # Step-by-step pipeline guide
│   ├── SCIENTIFIC_RATIONALE.md         # Scientific basis for scoring and logic
│   ├── PLATFORM_UI_MAPPING.md          # Pipeline output → platform UI mapping
│   ├── PLATFORM_UX_SPEC.md             # UX specification for the client report
│   ├── IMPLEMENTATION_BACKLOG.md       # Known gaps and planned work
│   ├── platform_payload_schema.json    # Platform API payload schema
│   └── frontend_handoff/              # Outputs shared with frontend team
│       ├── FRONTEND_API_CONTRACT.md
│       └── HEALTH_REPORT_SCHEMA.md
│
├── requirements.txt
└── .gitignore
```

---

## Running the pipeline

**Full batch:**
```bash
bash pipeline/run_sample_analysis.sh --batch nb1_2026_009
```

**Single sample:**
```bash
bash pipeline/run_sample_analysis.sh --batch nb1_2026_009 --sample 1421504848853
```

**Report only (existing GMWI2 data):**
```bash
python report/generate_report.py --sample-dir /path/to/analysis/nb1_2026_009/1421504848853
```

**Formulation only:**
```bash
python formulation/generate_formulation.py --sample-dir /path/to/analysis/nb1_2026_009/1421504848853
```

**Run formulation tests:**
```bash
pytest formulation/tests/
```

---

## Optimal Execution Sequence

For cost-optimized execution with minimal LLM calls, follow this phased approach:

### Step-by-step breakdown

```
Step 0:  distribute_questionnaires.py --token "..."              → 0 LLM
         (fetches from API → places questionnaire_*.json into each sample dir)

Step 1:  calculate_metrics.py                                → 0 LLM calls
         (produces only_metrics.txt + functional_guild.txt)

Step 2:  generate_report.py --no-llm                         → 0 LLM calls
         (produces microbiome_analysis_master.json — ALL structural data, 
          placeholder narratives. Internal health report call also runs no-llm → bare HTML)

Step 3:  generate_narrative_report.py                        → ~10 LLM calls
         (independent — reads analysis master + metrics → 
          produces narrative_report.md + PDF)

Step 4:  generate_formulation.py                             → 4-5 LLM calls
         (reads microbiome_analysis_master.json ✅ + questionnaire → 
          produces formulation_master.json)

Step 5:  generate_report.py (full LLM)                       → 1 LLM call
         (rewrites microbiome_analysis_master.json with real narratives.
          Uses --reuse-narratives? No — first run was --no-llm so narratives are placeholders.
          BUT: the internal health report call is the expensive part — see Step 6)

Step 6:  (triggered internally by Step 5)
         generate_health_report.py                           → ~6 LLM calls
         (NOW finds formulation_master.json ✅ → 
          full HTML with supplement sections, Section 3, lifestyle recs, etc.)
```

### Commands

```bash
# Step 1: Bioinformatics (0 LLM)
python science-engine/bioinformatics/calculate_metrics.py \
  --batch_id nb1_2026_XXX --sample_id SAMPLE_ID

# Step 2: Structural JSON only (0 LLM)
python science-engine/report/generate_report.py \
  --sample-dir analysis/nb1_2026_XXX/SAMPLE_ID/ --no-llm

# Step 3: Narrative report (~10 LLM)
python science-engine/report/generate_narrative_report.py \
  --sample-dir analysis/nb1_2026_XXX/SAMPLE_ID/

# Step 4: Formulation (4-5 LLM)
python science-engine/formulation/generate_formulation.py \
  --sample-dir analysis/nb1_2026_XXX/SAMPLE_ID/

# Step 5: Full report + health report HTML (1 + ~6 = ~7 LLM)
python science-engine/report/generate_report.py \
  --sample-dir analysis/nb1_2026_XXX/SAMPLE_ID/
```

---

## Setup

```bash
pip install -r requirements.txt
```

Requires AWS CLI configured with access to:
- `s3://nb1-prebiomics-sample-data` — sample sequencing data
- AWS Bedrock (Claude) — LLM supplement/prebiotic decisions

---

## Key design decisions

| Decision | Implementation |
|---|---|
| Probiotic mix selection | Fully **deterministic** — `rules_engine.py` → called via `formulation/llm/mix_selector.py` (no LLM) |
| Supplement selection | **LLM** — `formulation/llm/supplement_selector.py` (Bedrock Claude) |
| Prebiotic design | **LLM** with deterministic offline fallback — `formulation/llm/prebiotic_designer.py` |
| Strain lookup | Always from `formulation/knowledge_base/synbiotic_mixes.json` |
| Medication screening | LLM-assisted — `formulation/llm/medication_screener.py` |
| Standard probiotic dose | 50B CFU/day + optional 5B LP815 |
| Pipeline architecture | Modular stages `s01–s09` in `formulation/stages/` (refactored 19 March 2026) |
| Outputs | Written to `analysis/nb1_2026_XXX/{sample_id}/` — never stored in this repo |

---

## Probiotic mixes (8 total)

Defined in `formulation/knowledge_base/synbiotic_mixes.json`. Selection is deterministic based on guild dysbiosis pattern:

| # | Mix name | Trigger condition |
|---|---|---|
| 1 | Dysbiosis Recovery | Broad collapse ≥3 guilds |
| 2 | Bifidogenic Restore | Bifido depletion (most common, ~44% of samples) |
| 3 | Fiber & SCFA Restoration | Fiber/butyrate substrate-limited |
| 4 | Proteolytic Suppression | Pathobiont overgrowth |
| 5 | Mucus Barrier Restoration | Mucin degraders + diet-fed MDR |
| 6 | Maintenance Gold Standard | All guilds healthy |
| 7 | Psychobiotic | Clinician-directed only — no auto-trigger |
| 8 | Fiber Expansion & Competitive Displacement | Akk >10% + MDR >+0.5 + Fiber <30% |

---

## Archive policy

All `archive/` directories are **read-only historical references**.

- `formulation/archive/monolith_20260319/` — pre-refactor monolith, frozen 19 March 2026. Uses `sys.path` hacks incompatible with the current package structure. Do not copy from here.
- `formulation/archive/sachet_architecture_2026_03_17/` — abandoned sachet-format prototype.
- `formulation/knowledge_base/archive/` — superseded KB versions.
- `report/archive/` — retired report generation scripts.

When restoring or referencing archive code, all imports must be rewritten to use package-qualified paths (`from formulation.X` or `from shared.X`).

---

## Documentation

See `documentation/` for:
- `PROJECT_WIKI.md` — full architecture and module reference
- `PIPELINE_DOCUMENTATION.md` — step-by-step pipeline guide
- `SCIENTIFIC_RATIONALE.md` — scientific basis for scoring and formulation logic
- `PLATFORM_UI_MAPPING.md` — pipeline output → platform UI field mapping
- `PLATFORM_UX_SPEC.md` — UX specification for the client report
- `IMPLEMENTATION_BACKLOG.md` — known gaps and planned work
