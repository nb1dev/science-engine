# Optimal Pipeline Execution Sequence
**Minimizing LLM Costs by Breaking Circular Dependencies**

Date: 23 March 2026  
Author: Pipeline optimization analysis

---

## The Problem

The pipeline has a circular dependency that causes wasted LLM API calls:

```
calculate_metrics → generate_report.py → [internally calls] generate_health_report.py
                                                              ↑ needs formulation_master.json
                                          generate_formulation.py
                                                ↑ needs microbiome_analysis_master.json
```

Running naively causes:
- `generate_health_report.py` to run **before** formulation exists → supplement sections empty
- Needing to re-run the health report → **~6 LLM calls wasted**

---

## LLM Call Inventory (per sample)

| Script | LLM Calls | What They Generate |
|--------|-----------|-------------------|
| `calculate_metrics.py` | **0** | Pure bioinformatics computation |
| `generate_report.py` | **1** | Executive summary, guild interpretations, metabolic/vitamin narratives |
| `generate_formulation.py` | **4-5** | Clinical analysis, medication screening, supplement selection, ecological rationale |
| `generate_health_report.py` | **~6** | Section 3 root causes, factor explanations, lifestyle recommendations, supplement why-texts, consistency check |
| `generate_narrative_report.py` | **~10** | Complete 5,000-7,000 word clinical narrative (optional) |

---

## Optimal Execution Sequence

### Core Pipeline (minimum LLM calls)

```bash
# ──────────────────────────────────────────────────────────────
# Step 0: PREREQUISITE — Fetch questionnaires (0 LLM)
# ──────────────────────────────────────────────────────────────
# Run once per batch, not per sample
python science-engine/pipeline/distribute_questionnaires.py --token "..."

# OR use a previously downloaded response file:
python science-engine/pipeline/distribute_questionnaires.py response_1770214891248.json

# This places questionnaire_{sample}.json into analysis/{batch}/{sample}/questionnaire/


# ──────────────────────────────────────────────────────────────
# Step 1: Bioinformatics — Calculate Metrics (0 LLM)
# ──────────────────────────────────────────────────────────────
python science-engine/bioinformatics/calculate_metrics.py \
  --batch_id nb1_2026_XXX --sample_id SAMPLE_ID

# Produces:
#   - {sample}/only_metrics/{sample}_only_metrics.txt
#   - {sample}/only_metrics/{sample}_functional_guild.txt
#   - {sample}/plots/{sample}_integrated_analysis.pdf


# ──────────────────────────────────────────────────────────────
# Step 2: Microbiome Analysis JSON — Structural Only (0 LLM)
# ──────────────────────────────────────────────────────────────
python science-engine/report/generate_report.py \
  --sample-dir analysis/nb1_2026_XXX/SAMPLE_ID/ \
  --no-llm

# Produces:
#   - {sample}/reports/reports_json/microbiome_analysis_master_{sample}.json
#     ↳ All deterministic scores, guilds, dials, vitamin signals
#     ↳ Placeholder text for narratives
# Note: Health report also runs internally with --no-llm → bare HTML (overwritten in Step 4)


# ──────────────────────────────────────────────────────────────
# Step 3: Generate Formulation (4-5 LLM calls)
# ──────────────────────────────────────────────────────────────
python science-engine/formulation/generate_formulation.py \
  --sample-dir analysis/nb1_2026_XXX/SAMPLE_ID/

# Reads:
#   - microbiome_analysis_master_{sample}.json ✅ (from Step 2)
#   - questionnaire_{sample}.json ✅ (from Step 0)
# Produces:
#   - {sample}/reports/reports_json/formulation_master_{sample}.json
# LLM calls:
#   1. Clinical analysis (questionnaire review)
#   2. Medication screening
#   3-4. Supplement selection (sleep, goal-triggered)
#   5. Ecological rationale


# ──────────────────────────────────────────────────────────────
# Step 4: Complete Report + Health Report HTML (~7 LLM calls)
# ──────────────────────────────────────────────────────────────
python science-engine/report/generate_report.py \
  --sample-dir analysis/nb1_2026_XXX/SAMPLE_ID/

# Reads:
#   - only_metrics.txt (from Step 1)
#   - questionnaire_{sample}.json (from Step 0)
# Produces:
#   - microbiome_analysis_master_{sample}.json (UPDATED with real narratives)
#   - health_report_{sample}.html (COMPLETE with formulation data)
#   - health_report_interpretations_{sample}.json (cache for HTML)
# LLM calls:
#   1. Microbiome narratives (executive summary, guild interpretations)
#   + health_report.py internally:
#     2. Elicit query generation
#     3. Section 3 root causes
#     4. Factor-first explanations
#     5. Lifestyle recommendations
#     6. Supplement why-texts
#     7. Consistency check
```

### Optional: Narrative Clinical Report (~10 LLM calls)

```bash
# ──────────────────────────────────────────────────────────────
# Step 5: Narrative Report (OPTIONAL — independent)
# ──────────────────────────────────────────────────────────────
python science-engine/report/generate_narrative_report.py \
  --sample-dir analysis/nb1_2026_XXX/SAMPLE_ID/

# Reads:
#   - microbiome_analysis_master_{sample}.json (from Step 4)
#   - only_metrics.txt (from Step 1)
# Produces:
#   - narrative_report_{sample}.md (5,000-7,000 words)
#   - narrative_report_{sample}.pdf
# LLM calls: ~10 (5 Opus + 5 Sonnet sections)
```

---

## Batch Processing

For processing entire batches:

```bash
# Step 0: Fetch all questionnaires (once per batch cycle)
python science-engine/pipeline/distribute_questionnaires.py --token "..."

# Steps 1-2: Metrics + structural analysis (0 LLM)
python science-engine/bioinformatics/calculate_metrics.py \
  --batch_id nb1_2026_XXX --all-samples

# Then for each sample with questionnaire:
for sample in analysis/nb1_2026_XXX/*/; do
  # Skip if no questionnaire
  if [ ! -d "$sample/questionnaire" ]; then
    continue
  fi
  
  # Step 2: Structural analysis (0 LLM)
  python science-engine/report/generate_report.py \
    --sample-dir "$sample" --no-llm
done

# Step 3: Formulation batch (4-5 LLM per sample)
python science-engine/formulation/generate_formulation.py \
  --batch-dir analysis/nb1_2026_XXX/

# Step 4: Complete reports batch (~7 LLM per sample)
python science-engine/report/generate_report.py \
  --batch-dir analysis/nb1_2026_XXX/

# Step 5: Narrative reports (optional, ~10 LLM per sample)
python science-engine/report/generate_narrative_report.py \
  --batch-dir analysis/nb1_2026_XXX/ \
  --parallel 3
```

---

## Cost Savings

| Approach | LLM Calls/Sample | For 9 Samples | Cost/Sample* | Cost/9 Samples* |
|----------|------------------|---------------|--------------|-----------------|
| Naive (current) | ~17-18 + re-run = ~24 | ~216 | ~$1.80 | ~$16.20 |
| **Optimal (this plan)** | **~12** | **~108** | **~$0.90** | **~$8.10** |
| **+ Narrative** | **~22** | **~198** | **~$1.65** | **~$14.85** |
| **Savings (core)** | **~12** | **~108** | **~$0.90** | **~$8.10** |

*Approximate costs based on Claude Sonnet 4 pricing (~$0.075/call average)

---

## What Each Step Produces

### Step 0: `distribute_questionnaires.py`
- **Input:** API token or local `response_*.json`
- **Output:** `questionnaire_{sample}.json` in each sample's dir
- **LLM Calls:** 0
- **Purpose:** Fetch client health data from platform

### Step 1: `calculate_metrics.py`
- **Input:** Raw sequencing data (GMWI2, MetaPhlAn, HUMAnN outputs)
- **Output:** `only_metrics.txt`, `functional_guild.txt`
- **LLM Calls:** 0
- **Purpose:** Pure computational analysis

### Step 2: `generate_report.py --no-llm`
- **Input:** `only_metrics.txt`
- **Output:** `microbiome_analysis_master_{sample}.json` (structural data only)
- **LLM Calls:** 0
- **Purpose:** Create the data structure formulation needs
- **Key:** Placeholder narratives don't matter — formulation reads scores/guilds/dials

### Step 3: `generate_formulation.py`
- **Input:** `microbiome_analysis_master.json` + `questionnaire.json`
- **Output:** `formulation_master_{sample}.json`
- **LLM Calls:** 4-5
- **Purpose:** Generate personalized supplement formulation

### Step 4: `generate_report.py` (full)
- **Input:** `only_metrics.txt`, `questionnaire.json`, `formulation_master.json`
- **Output:** Complete `microbiome_analysis_master.json` + `health_report.html`
- **LLM Calls:** ~7
- **Purpose:** Final reports with all data integrated

### Step 5: `generate_narrative_report.py` (optional)
- **Input:** `microbiome_analysis_master.json` + `only_metrics.txt`
- **Output:** `narrative_report.md` + PDF
- **LLM Calls:** ~10
- **Purpose:** Comprehensive clinical narrative document

---

## Critical Rules

1. **Never skip Step 0** — Questionnaire is required for Steps 3-4
2. **Steps 1-2 are free** (0 LLM) — run them before any LLM steps
3. **Step 2 --no-llm is essential** — creates the structural JSON formulation needs
4. **Step 3 must come before Step 4** — formulation data is needed for complete health report
5. **Step 5 is independent** — can run anytime after Step 4

---

## Common Patterns

### Fresh Sample (nothing exists yet)
Run Steps 0 → 1 → 2 → 3 → 4 in order.

### Metrics exist, need to regenerate reports
Skip Step 1, start from Step 2.

### Questionnaire updated, need to regenerate formulation
Re-run Step 3 → Step 4.

### Just regenerate health report HTML layout
```bash
python science-engine/report/generate_health_report.py \
  --sample-dir analysis/{batch}/{sample}/ \
  --use-cached
```
This reuses `health_report_interpretations_{sample}.json` cache → 0 LLM calls.

---

## Files & Dependencies

### Files Each Script Needs

| Script | Reads From | Writes To |
|--------|-----------|-----------|
| `distribute_questionnaires.py` | API / `data/questionnaire/response_*.json` | `{sample}/questionnaire/questionnaire_{sample}.json` |
| `calculate_metrics.py` | `{sample}/GMWI2/*`, `data/{batch}/functional_profiling/{sample}/*` | `{sample}/only_metrics/{sample}_only_metrics.txt` |
| `generate_report.py` | `{sample}/only_metrics/{sample}_only_metrics.txt` | `{sample}/reports/reports_json/microbiome_analysis_master_{sample}.json` |
| `generate_formulation.py` | `microbiome_analysis_master_{sample}.json`, `questionnaire_{sample}.json` | `formulation_master_{sample}.json` |
| `generate_health_report.py` | `microbiome_analysis_master_{sample}.json`, `formulation_master_{sample}.json`, `questionnaire_{sample}.json` | `health_report_{sample}.html` |
| `generate_narrative_report.py` | `microbiome_analysis_master_{sample}.json`, `only_metrics.txt` | `narrative_report_{sample}.md` |

### Directory Structure

```
analysis/nb1_2026_XXX/SAMPLE_ID/
├── GMWI2/
│   ├── {sample}_run_GMWI2.txt
│   ├── {sample}_run_GMWI2_taxa.txt
│   └── {sample}_run_metaphlan.txt
├── only_metrics/
│   ├── {sample}_only_metrics.txt          ← Step 1 output
│   └── {sample}_functional_guild.txt
├── questionnaire/
│   └── questionnaire_{sample}.json        ← Step 0 output
├── reports/
│   ├── reports_json/
│   │   ├── microbiome_analysis_master_{sample}.json  ← Step 2 → Step 4
│   │   ├── formulation_master_{sample}.json          ← Step 3 output
│   │   └── health_report_interpretations_{sample}.json
│   ├── reports_html/
│   │   └── health_report_{sample}.html    ← Step 4 output
│   └── reports_md/
│       └── narrative_report_{sample}.md   ← Step 5 output
└── logs/
    └── {sample}_*.log
```

---

## Troubleshooting

### Error: "No _only_metrics.txt found"
→ Run Step 1 first. Or the path argument is wrong (should be sample dir, not batch dir).

### Error: "Microbiome analysis JSON not found"
→ Run Step 2 before Step 3.

### Error: "No questionnaire found — proceeding with microbiome data only"
→ Run Step 0 to fetch questionnaires from API.

### Formulation works but health report supplement sections are empty
→ You ran Step 4 before Step 3. Re-run Step 4 after formulation exists.

### Health report supplement sections still empty after re-running
→ The cached `health_report_interpretations.json` has stale data. Delete it and re-run Step 4.

---

## Skip Unnecessary Re-runs

If you're **only changing thresholds or scoring logic** (no LLM changes):

```bash
# Regenerate analysis master with new thresholds
python science-engine/report/generate_report.py \
  --sample-dir ... \
  --reuse-narratives

# This extracts existing LLM narratives and only recalculates scores → 0 LLM calls
```

If you're **only changing HTML layout** (no data changes):

```bash
python science-engine/report/generate_health_report.py \
  --sample-dir ... \
  --use-cached

# Reuses health_report_interpretations.json cache → 0 LLM calls
```

---

## Integration with run_sample_analysis.sh

The current `run_sample_analysis.sh` follows the naive approach. To optimize it:

**Option 1:** Modify it to follow this sequence (Steps 1→2 no-llm→3→4)

**Option 2:** Create a wrapper script `run_optimized_pipeline.sh` that chains:
```bash
bash science-engine/pipeline/run_optimized_pipeline.sh \
  --batch nb1_2026_XXX \
  --sample SAMPLE_ID
```

---

## Key Insight

The formulation pipeline (`generate_formulation.py`) **only reads structural/computational fields** from `microbiome_analysis_master.json`:
- `bacterial_groups` (abundance, CLR, status, evenness)
- `_debug.raw_metrics` (CUR, FCR, MDR, PPR)
- `metabolic_function.dials`
- `vitamin_synthesis` (risk levels, signals)
- `overall_score` (total, band)
- `root_causes` (diagnostic flags)

It **never reads narrative text** (executive_summary, interpretations, etc.).

Therefore: `--no-llm` produces a **complete, valid input** for the formulation pipeline at **zero cost**.

---

**END OF DOCUMENT**
