# Microbiome Analysis Pipeline — Technical Documentation

## Overview

End-to-end automated pipeline that processes raw microbiome sequencing data into structured reports for the client health platform. Three independent stages, each producing distinct outputs.

---

## Pipeline Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        STAGE 1: BIOINFORMATICS                          │
│                     run_sample_analysis.sh                               │
│                                                                          │
│  S3 Bucket ──→ Download ──→ GMWI2 Analysis ──→ Metrics Calculation      │
│  (raw data)    (3 dirs)     (run_gmwi2.sh)     (calculate_metrics.py)   │
│                                                                          │
│  Input:  s3://nb1-prebiomics-sample-data/incoming/{batch}/              │
│  Output: analysis/{batch}/{sample}/only_metrics/                         │
│          ├── {sample}_only_metrics.txt                                   │
│          └── {sample}_functional_guild.csv                               │
└─────────────────────────┬───────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     STAGE 2: JSON REPORT GENERATION                     │
│                       generate_report.py                                 │
│                                                                          │
│  _only_metrics.txt ──→ parse ──→ score ──→ fields ──→ LLM ──→ assemble │
│  _functional_guild.csv                                                   │
│  questionnaire.json                                                      │
│                                                                          │
│  Output: output/{sample}_microbiome_analysis.json  (master)             │
│          output/{sample}_platform.json             (API-ready)          │
│          + copies in analysis/{batch}/{sample}/report_json/              │
└─────────────────────────┬───────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    STAGE 3: NARRATIVE REPORT                            │
│                  generate_narrative_report.py                             │
│                                                                          │
│  _microbiome_analysis.json ──→ 10 LLM calls ──→ assemble ──→ PDF       │
│  _only_metrics.txt                                                       │
│                                                                          │
│  Output: output/{sample}_narrative_report.md                            │
│          output/{sample}_narrative_report.pdf                            │
│          + copies in analysis/{batch}/{sample}/report/                    │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Stage 1: Bioinformatics

**Script:** `platform_automation/pipeline_scripts/run_sample_analysis.sh`
**Dependencies:** AWS CLI, GMWI2 tool, Python 3

### What It Does
1. Discovers samples from S3 bucket
2. Downloads 3 data types: `functional_profiling/`, `raw_sequences/`, `taxonomic_profiling/`
3. Runs GMWI2 analysis (health association scoring on MetaPhlAn output)
4. Runs integrated metrics report (calculates all compositional, diversity, guild, vitamin, and CLR metrics)
5. Deletes raw sequences (saves disk space)
6. Marks sample as complete

### Commands
```bash
# Full pipeline — single sample
bash platform_automation/pipeline_scripts/run_sample_analysis.sh \
  --batch nb1_2026_001 --sample 1421263814738

# Full pipeline — entire batch
bash platform_automation/pipeline_scripts/run_sample_analysis.sh \
  --batch nb1_2026_001

# Recalculate metrics only (skip S3 + GMWI2)
bash platform_automation/pipeline_scripts/run_sample_analysis.sh \
  --batch nb1_2026_001 --metrics-only --force

# Preview without executing
bash platform_automation/pipeline_scripts/run_sample_analysis.sh \
  --batch nb1_2026_001 --dry-run
```

### Flags
| Flag | Purpose |
|------|---------|
| `--batch BATCH_ID` | Process specific batch |
| `--sample SAMPLE_ID` | Process single sample (requires --batch) |
| `--metrics-only` | Skip S3/GMWI2, recalculate metrics from existing data |
| `--force` | Force reprocessing even if already complete |
| `--dry-run` | Preview without executing |

### Output Structure
```
analysis/{batch}/{sample}/
├── GMWI2/
│   ├── {sample}_run_GMWI2.txt
│   ├── {sample}_run_GMWI2_taxa.txt
│   └── {sample}_run_metaphlan.txt
├── only_metrics/
│   ├── {sample}_only_metrics.txt        ← INPUT for Stage 2
│   ├── {sample}_functional_guild.txt
│   └── {sample}_functional_guild.csv    ← INPUT for Stage 2
├── questionnaire/
│   └── questionnaire_{sample}.json
└── logs/
    └── {sample}_pipeline.status
```

---

## Stage 2: JSON Report Generation

**Script:** `platform_automation/report_automation/generate_report.py`
**Dependencies:** Python 3, boto3 (for LLM), knowledge base JSONs

### What It Does
```
_only_metrics.txt ──→ parse_metrics.py ──→ Unified data dict
                           │
                           ▼
                      scoring.py ──→ Overall score (0-100, 5 pillars)
                           │         + score_drivers
                           │         + pillar interpretations {scientific, non_expert}
                           │
                           ▼
                    overview_fields.py ──→ Deterministic fields:
                           │               • Overall balance {scientific, non_expert}
                           │               • Diversity & resilience {scientific, non_expert}
                           │               • 4 metabolic dials + context
                           │               • Key strengths {scientific, non_expert} + CLR
                           │               • Key opportunities {scientific, non_expert} + CLR
                           │               • Vitamin risks (B12, folate, biotin, B-complex)
                           │               • Guild status + priority_level (CRITICAL/1A/1B/Monitor)
                           │
                           ▼
                     narratives.py ──→ 12 LLM calls to AWS Bedrock:
                           │           All dual format {scientific, non_expert}
                           │           • Summary, what's happening, good news
                           │           • Possible impacts, is_something_wrong, can_this_be_fixed
                           │           • Guild interpretations (6 guilds × 2 versions)
                           │           • Metabolic + vitamin interpretations
                           │           • Root causes: diagnosis, insights, conclusion
                           │
                           ▼
                 root_causes_fields.py ──→ Diagnostic flags, trophic cascades, reversibility
                           │
                           ▼
                 action_plan_fields.py ──→ Prioritized intervention steps
                           │               • Priority levels matching guild status
                           │               • 100-player capacity scale
                           │               • Timeline estimates, forecasts
                           │
                           ▼
                 platform_mapping.py ──→ Extracts non_expert for platform
                           │
                           ▼
                    ┌──────┴──────┐
                    ▼             ▼
    _microbiome_analysis.json   _platform.json
         (master file)          (API payload)
```

### Commands
```bash
cd platform_automation/report_automation

# With LLM narratives
python3 generate_report.py --sample-dir /path/to/analysis/{batch}/{sample}/

# Without LLM (fast, deterministic only)
python3 generate_report.py --sample-dir /path/to/sample/ --no-llm

# Batch mode
python3 generate_report.py --batch-dir /path/to/analysis/{batch}/
```

### Output Files

**`{sample}_microbiome_analysis.json`** — Master file containing:
- `report_metadata` — sample ID, date, algorithm version
- `executive_summary` — pattern, key finding, priorities
- `overall_score` — total, band, 5 pillars with {scientific, non_expert}, score_drivers
- `ecological_metrics` — diversity, resilience, balance with {scientific, non_expert}
- `safety_profile` — dysbiosis markers, M. smithii, BCFA
- `metabolic_function` — 4 dials with description + context
- `vitamin_synthesis` — B12, folate, biotin, B-complex risks
- `bacterial_groups` — 6 guilds with status, CLR, evenness, **priority_level**
- `key_messages` — strengths/opportunities {scientific, non_expert}, good news, impacts
- `root_causes` — diagnostic flags, trophic impact, reversibility
- `action_plan` — prioritized steps with **priority_level**, capacity, timelines
- `_debug` — raw metrics, guild summary

**`{sample}_platform.json`** — 5-tab API payload:
- `overview_tab` — score, balance, diversity, dials, meanings (non_expert only)
- `bacterial_groups_tab` — 6 guilds flat list with capacity
- `root_causes_tab` — flags, insights, reversibility (non_expert only)
- `vitamins_tab` — 4 vitamins with status
- `action_plan_tab` — prioritized steps with **priority_level** matching guilds

---

## Stage 3: Narrative Report Generation

**Script:** `platform_automation/report_automation/generate_narrative_report.py`
**Dependencies:** Python 3, boto3, pandoc + xelatex (for PDF)

### What It Does
1. Loads `_microbiome_analysis.json` + raw `_only_metrics.txt`
2. Builds system prompt from `knowledge_base/concise_report_framework.md`
3. Makes 10 section-by-section Bedrock API calls
4. Assembles into single Markdown document
5. Converts to PDF via pandoc + xelatex

### Commands
```bash
cd platform_automation/report_automation

# Single sample
python3 generate_narrative_report.py \
  --sample-dir /path/to/analysis/{batch}/{sample}/

# Batch
python3 generate_narrative_report.py \
  --batch-dir /path/to/analysis/{batch}/

# From specific JSON
python3 generate_narrative_report.py \
  --sample-dir /path/to/sample/ --from-json /path/to/analysis.json
```

### 10-Section Structure
| # | Section | Words | LLM Call |
|---|---------|-------|----------|
| 1 | Executive Summary | 400-500 | Call 1 |
| 2 | Compositional Metrics | 800-1000 | Call 2 |
| 3 | Diversity Signatures | 400-600 | Call 3 |
| 4a | Guild Framework + CLR Dashboard | 1200-1500 | Call 4 |
| 4b | Detailed Guild Assessments (6 guilds) | 2000-2500 | Call 5 |
| 4c | Metabolic Flow Diagram | 300-500 | Call 6 |
| 5 | Pathways & Vitamins | 600-800 | Call 7 |
| 6 | Integrated Assessment | 800-1000 | Call 8 |
| 7+8 | Restoration + Monitoring | 800-1100 | Call 9 |
| 9+10 | Limitations + Disclaimer + Summary | 1000-1300 | Call 10 |

---

## Module Reference

### `platform_automation/report_automation/`

| Module | Role | Input | Output |
|--------|------|-------|--------|
| `parse_metrics.py` | Read pipeline outputs | `_only_metrics.txt`, `_functional_guild.csv` | Unified data dict |
| `scoring.py` | 5-pillar scoring (0-100) | Data dict | Score + pillars + drivers |
| `overview_fields.py` | Deterministic fields | Data dict | Balance, diversity, dials, guilds, strengths, vitamins |
| `root_causes_fields.py` | Root causes | Data dict + score | Flags, cascades, reversibility |
| `action_plan_fields.py` | Action plan | Data dict + score + vitamins | Steps, capacity, forecasts |
| `narratives.py` | LLM narratives | Data + score + fields | 12 dual {scientific, non_expert} texts |
| `platform_mapping.py` | Platform transformer | Analysis JSON + data | Platform JSON (non_expert only) |
| `generate_report.py` | **Main orchestrator** | Sample dir | `_microbiome_analysis.json` + `_platform.json` |
| `generate_narrative_report.py` | Narrative generator | Analysis JSON + metrics | `_narrative_report.md` + `.pdf` |

### Knowledge Base (`knowledge_base/`)

| File | Content |
|------|---------|
| `guild_interpretation.json` | Guild definitions, CLR ratios, trophic cascades, 9-scenario matrix |
| `interpretation_rules.json` | Compositional metric thresholds, pattern classification |
| `static_content.json` | Universal text for all 5 platform tabs |
| `concise_report_framework.md` | Optimized LLM prompt for narrative report generation |
| `dietary_inference.json` | Dietary pattern inference from CLR ratios |
| `vitamin_signals.json` | Vitamin synthesis signal definitions |

---

## Priority Classification System

Deterministic classification applied to every guild in `bacterial_groups` and every step in `action_plan`:

| Level | Beneficial Guild Criteria | Contextual Guild Criteria |
|-------|--------------------------|--------------------------|
| **CRITICAL** | Absent (0%) or < 50% of minimum | > 3× above maximum |
| **1A** | Below range + CLR < -1.0 | > 2× above maximum |
| **1B** | Below range (other), or CLR < -0.5 within range | > maximum |
| **Monitor** | Within range, stable CLR | Within range |

Priorities flow: `overview_fields.py` → `bacterial_groups.priority_level` → `action_plan_fields.py` → `intervention_steps.priority_level` → `platform_mapping.py` → platform action plan steps.

---

## AWS Configuration

- **S3 Bucket:** `s3://nb1-prebiomics-sample-data/incoming`
- **Bedrock Model:** `eu.anthropic.claude-sonnet-4-20250514-v1:0`
- **Bedrock Region:** `eu-west-1`
- **Credentials:** `~/.aws/credentials`

---

## Quick Reference

### Process a New Sample (End-to-End)
```bash
# 1. Download + GMWI2 + Metrics
bash platform_automation/pipeline_scripts/run_sample_analysis.sh \
  --batch nb1_2026_001 --sample 1421263814738

# 2. Generate JSONs (with LLM)
cd platform_automation/report_automation
python3 generate_report.py \
  --sample-dir /Users/pnovikova/Documents/work/analysis/nb1_2026_001/1421263814738/

# 3. Generate Narrative Report
python3 generate_narrative_report.py \
  --sample-dir /Users/pnovikova/Documents/work/analysis/nb1_2026_001/1421263814738/
```

### Process Entire Batch
```bash
# 1. Metrics for all samples
bash platform_automation/pipeline_scripts/run_sample_analysis.sh \
  --batch nb1_2026_001 --force

# 2. JSONs for all samples
cd platform_automation/report_automation
python3 generate_report.py \
  --batch-dir /Users/pnovikova/Documents/work/analysis/nb1_2026_001/

# 3. Narrative reports for all samples
python3 generate_narrative_report.py \
  --batch-dir /Users/pnovikova/Documents/work/analysis/nb1_2026_001/
```

### Regenerate After Algorithm Update
```bash
# Recalculate metrics only (no S3/GMWI2)
bash platform_automation/pipeline_scripts/run_sample_analysis.sh \
  --batch nb1_2026_001 --metrics-only --force

# Regenerate JSONs
cd platform_automation/report_automation
python3 generate_report.py \
  --batch-dir /Users/pnovikova/Documents/work/analysis/nb1_2026_001/ --no-llm
```
