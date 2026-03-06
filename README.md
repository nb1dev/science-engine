# science-engine

Bioinformatics and AI pipeline for the nb1 microbiome analysis platform (Prebiomics). Processes raw microbiome sequencing data into personalised health reports and supplement formulations.

## What it does

Takes a sample through three sequential stages:

1. **Bioinformatics** — QC, GMWI2 scoring, metabolic pathway analysis, metric calculation
2. **Report generation** — Scoring, guild analysis, root cause inference, platform JSON output
3. **Formulation** — Probiotic mix selection, prebiotic design, supplement selection, manufacturing recipe

## Repository structure

```
science-engine/
├── bioinformatics/        # GMWI2, QC precheck, metrics calculation
│   ├── run_gmwi2.sh
│   ├── qc_precheck.py
│   ├── calculate_metrics.py
│   ├── core_pathway_analysis.py
│   └── knowledge_base/    # GMWI2 model, taxa coefficients
├── report/                # Report generation pipeline
│   ├── generate_report.py         # Main entry point
│   ├── generate_narrative_report.py
│   ├── scoring.py
│   ├── overview_fields.py
│   ├── root_causes_fields.py
│   ├── action_plan_fields.py
│   ├── narratives.py
│   ├── platform_mapping.py
│   └── knowledge_base/    # Thresholds, interpretation rules, guild data
├── formulation/           # Supplement formulation engine
│   ├── generate_formulation.py    # Main entry point
│   ├── rules_engine.py
│   ├── llm_decisions.py           # Bedrock Claude — supplements + prebiotics
│   ├── dose_optimizer.py
│   ├── weight_calculator.py
│   ├── parse_inputs.py
│   ├── platform_mapping.py
│   └── knowledge_base/    # Synbiotic mixes, supplements, vitamins, dose rules
├── pipeline/              # Orchestration
│   └── run_sample_analysis.sh     # Full pipeline entry point
├── shared/                # Shared utilities (imported by report + formulation)
│   ├── guild_priority.py
│   └── formatting.py
└── documentation/         # Architecture, scientific rationale, UI/UX specs
```

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

## Setup

```bash
pip install -r requirements.txt
```

Requires AWS CLI configured with access to:
- `s3://nb1-prebiomics-sample-data` — sample data
- AWS Bedrock (Claude) — LLM supplement/prebiotic decisions

## Key design decisions

- **Probiotic mix selection** is fully deterministic (`rules_engine.py`) — no LLM
- **Supplement selection** uses Bedrock Claude (`llm_decisions.py`)
- **Strain lookup** always from `formulation/knowledge_base/synbiotic_mixes.json`
- **Outputs** go to `analysis/nb1_2026_XXX/{sample_id}/reports/` — never stored in this repo
- Standard probiotic dose: 50B CFU/day + optional 5B LP815

## Documentation

See `documentation/` for:
- `PROJECT_WIKI.md` — full architecture and module reference
- `PIPELINE_DOCUMENTATION.md` — step-by-step pipeline guide
- `SCIENTIFIC_RATIONALE.md` — scientific basis for scoring and formulation logic
- `PLATFORM_UI_MAPPING.md` — pipeline output → platform UI field mapping
- `PLATFORM_UX_SPEC.md` — UX specification for the client report
- `PRIORITY_SYSTEM_CHANGELOG.md` — supplement priority logic change history
