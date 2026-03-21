# Frontend API Contract — Prebiomics Health Report

> **Date**: 20 March 2026  
> **Schema version**: `3.0`  
> **Author**: Polina Novikova

---

## Overview

The health report frontend receives **one JSON file per client** and renders it as a
self-contained HTML page. No secondary API calls are needed. The backend computes
everything (including all LLM text) and persists it to this single JSON.

**File name pattern**: `health_report_interpretations_{sample_id}.json`  
**Schema version field**: `schema_version: "3.0"`

Two reference implementations are included in this folder:
- `health_report_interpretations_1421029282376.json` — sample 2376 (score 85.7, 1 deviation, E. coli absent)
- `health_report_interpretations_1421266404096.json` — sample 4096 (score 68.3, 5 deviations, E.Shigella detected)

---

## Architecture Principle

```
Backend (science-engine)
  → generate_health_report.py
  → health_report_interpretations_{id}.json   ← THE CONTRACT
  → Frontend renders this JSON only
```

**The frontend never computes. It only displays.** Every visible text element,
every score, every chart value is pre-computed and injected into the JSON.

---

## Top-Level JSON Structure

```json
{
  "sample_id": "1421029282376",
  "generated_at": "2026-03-19T17:04:52",
  "schema_version": "3.0",
  "report_date": "2026-03-10",

  // ── Cover page ──────────────────────────────────────────
  "overall_score": { ... },
  "score_summary": "<strong>...</strong> out of 100...",
  "profile": { ... },

  // ── Section 1 — The Big Picture ─────────────────────────
  "circle_scores": { ... },
  "bacterial_groups": { ... },
  "metabolic_dials": { ... },
  "ecological_metrics": { ... },
  "safety_profile": { ... },
  "guild_timepoints": [ ... ],

  // ── Section 2 — Strengths & Challenges ──────────────────
  "strengths_challenges": { ... },
  "good_news": "...",

  // ── Section 3 — Root Causes ─────────────────────────────
  "root_cause_data": { ... },

  // ── Section 4 — Timeline & Lifestyle ────────────────────
  "timeline_phases": [ ... ],
  "lifestyle_recommendations": [ ... ],

  // ── Section 5 — Formula ─────────────────────────────────
  "supplement_cards": [ ... ],
  "protocol_summary": { ... },

  // ── Section 6 — Goals ────────────────────────────────────
  "goal_cards": [ ... ],

  // ── References ───────────────────────────────────────────
  "cited_papers": [ ... ]
}
```

---

## Field-by-Field Reference

### `overall_score` → Cover page score dial + pillar chips

```json
{
  "total": 85.7,              // number 0-100 — dial fill
  "band": "Excellent",        // string — displayed near dial
  "pillars": {
    "health_association":  { "score": 14.4, "max": 20, "description": "..." },
    "diversity_resilience":{ "score": 18.0, "max": 20, "description": "..." },
    "metabolic_function":  { "score": 20.0, "max": 20, "description": "..." },
    "guild_balance":       { "score": 23.3, "max": 30, "description": "..." },
    "safety_profile":      { "score": 10.0, "max": 10, "description": "..." }
  },
  "score_drivers": {
    "strongest": { "pillar": "metabolic_function", "label": "Metabolic Function", "pct_of_max": 100 },
    "weakest":   { "pillar": "health_association", "label": "Health Association",  "pct_of_max": 72 },
    "key_note":  "Your strongest area is metabolic function (100% of its potential)"
  }
}
```

### `score_summary` → Pre-rendered HTML sentence for cover

```json
"Your overall score is <strong>85.7</strong> out of 100. The main area to focus on is <strong>fiber processing</strong>."
```
Render as `innerHTML`. Contains `<strong>` tags for bold emphasis.

### `profile` → Cover page client stats bar

```json
{
  "first_name": "Polina",
  "age": 30,
  "sex": "Female",
  "diet": "Omnivore",
  "stress": 7,
  "sleep": 9,
  "goals": ["Immune resilience", "Mood & anxiety", "Optimise metabolism"],
  "sensitivity": "Moderate"
}
```

### `circle_scores` → Section 1 four health dials

```json
{
  "gut_lining":      89,   // 0-100 — renders as circular gauge
  "inflammation":    83,
  "fiber_processing": 67,
  "bifidobacteria":  88
}
```
Color mapping: ≥75 green, ≥50 amber, <50 red.

### `bacterial_groups` → Section 1 guild bars + SVG pathway

```json
{
  "Fiber Degraders": {
    "abundance": 15.23,             // % — current level
    "healthy_range": "30-50%",      // display string
    "status": "Below range",        // "Below range" | "Within range" | "Above range" | "Absent — CRITICAL"
    "clr": 0.32,                    // competitive ratio — used by JS pathway
    "evenness": 0.54,               // guild redundancy 0-1
    "evenness_status": "Moderate redundancy",
    "client_interpretation": "Your fiber-processing bacteria are running below..."
  },
  // ... 5 more guilds
}
```

Guild names: `"Fiber Degraders"`, `"Cross-Feeders"`, `"Butyrate Producers"`, `"Bifidobacteria"`, `"Proteolytic Guild"`, `"Mucin Degraders"`

### `guild_timepoints` → Evolution slider data (JavaScript `HR_TPS`)

```json
[
  {
    "label": "Baseline — Mar 2026",
    "score": 85.7,            // overall score at this timepoint (optional — JS can approximate)
    "guilds": {
      "fd": 0.1523,           // fraction (not %) — Fiber Degraders
      "bb": 0.1028,           // Bifidobacteria
      "cf": 0.1702,           // Cross-Feeders
      "bp": 0.1417,           // Butyrate Producers
      "pg": 0.0500,           // Proteolytic Guild
      "md": 0.0304            // Mucin Degraders
    }
  }
  // Future timepoints appended here on retest
]
```

### `safety_profile` → Safety markers

```json
{
  "dysbiosis_markers": {
    "F_nucleatum":    0.0,      // abundance %
    "S_gallolyticus": 0.0,
    "P_anaerobius":   0.0,
    "E_Shigella":     0.9615   // non-zero = detected
  },
  "any_detected": true         // boolean — show/hide warning banner
}
```

### `metabolic_dials` → 4 metabolic state indicators

```json
{
  "main_fuel":             { "state": "carb_driven",     "label": "Carbohydrate fermentation dominant", "value": 0.918 },
  "fermentation_efficiency":{ "state": "efficient",      "label": "Efficient assembly line",            "value": 0.412 },
  "mucus_dependency":      { "state": "diet_fed",        "label": "Diet-substrate driven",              "value": -1.613 },
  "putrefaction_pressure": { "state": "scfa_dominant",   "label": "SCFA-dominant — gentle byproducts", "value": -1.042 }
}
```

State enums:
- `main_fuel`: `carb_driven` | `balanced` | `protein_driven`
- `fermentation_efficiency`: `efficient` | `ok` | `sluggish`
- `mucus_dependency`: `diet_fed` | `backup` | `heavy_mucus`
- `putrefaction_pressure`: `scfa_dominant` | `balanced` | `protein_pressure`

### `strengths_challenges` → Section 2

```json
{
  "strengths": [
    { "icon": "🛡️", "title": "Low inflammatory pressure", "text": "Your beneficial bacteria are..." }
  ],
  "challenges": [
    { "icon": "🌾", "title": "Fiber-processing bacteria below range",
      "area_key": "fiber_processing", "area_label": "fiber processing",
      "severity": "moderate",       // "critical" | "high" | "moderate"
      "text": "Your fiber-processing bacteria are at 15.2%..." }
  ],
  "all_strengths": [ ... ],         // untruncated — show in expanded view
  "all_challenges": [ ... ],
  "distinct_areas": ["fiber processing"],  // for score_summary text
  "bottom_line": "The main issue is not total collapse..."
}
```

### `root_cause_data` → Section 3

```json
{
  "deviation_cards": [
    {
      "deviation": {
        "client_label": "Fiber-Processing Bacteria",
        "icon": "🌾",
        "value_str": "15.2%",
        "range_str": "30–50%",
        "description": "Your Fiber-Processing Bacteria are at 15.2% — below the healthy range..."
      },
      "narrative": "Your fiber-processing bacteria are the workhorses...",  // LLM paragraphs
      "summary_line": "Your fiber-processing bacteria took a hit...",       // takeaway
      "kb_drivers": [
        {
          "icon": "💊",
          "label": "Antibiotic Use",
          "text": "Following antibiotic use...",           // LLM-personalised body text
          "kb_text": "Following antibiotic use...",       // static science text for "What does science say"
          "evidence_label": "Research supported",        // "Well established" | "Research supported" | "Emerging research"
          "directionality": "driver",
          "directionality_arrow": "exposure → microbiome"
        }
      ]
    }
  ],
  "awareness_chips": [
    {
      "domain_key": "alcohol_consumption",
      "domain_label": "Regular Alcohol Consumption",
      "icon": "🍷",
      "directionality_arrow": "exposure → microbiome",
      "summary_text": "Regular alcohol consumption is a documented contributor..."
    }
  ],
  "section_summary": "Your gut is telling the story of a recent perfect storm..."
}
```

### `timeline_phases` → Section 4 timeline

```json
[
  {
    "weeks": "1–4",
    "title": "🌱 Settling in",
    "body": "The probiotic strains in your capsule are adapting...",
    "color": "#3A6EA8"
  },
  { "weeks": "5–8",  "title": "🔋 Early momentum", ... },
  { "weeks": "9–12", "title": "🛡️ Deepening balance", ... },
  { "weeks": "13–16","title": "⚖️ Consolidation", ... }
]
```

### `lifestyle_recommendations` → Section 4 lifestyle panel

```json
[
  { "emoji": "🌾", "title": "Prioritize diverse fiber sources daily", "text": "With your fiber-processing bacteria at only 15.2%..." }
]
```
Max 5 items. Render in dark-background panel alongside timeline.

### `supplement_cards` → Section 5

```json
[
  {
    "num": 1,
    "key": "delivery_format_1_probiotic_capsule",
    "name": "Probiotic Hard Capsule",
    "timing": "🌅 1× morning",
    "what_it_is": "Live beneficial bacteria, selected specifically for your microbiome pattern.",
    "why": "Mix 3 (Fiber & SCFA Restoration) targets your fiber-processing bacteria...",
    "pills": [
      { "name": "Lactobacillus plantarum UALp-05", "dose": "125mg · 12.5B CFU" }
    ],
    "capsules": null,           // null for single-component cards
    "supports": ["Fiber & SCFA Restoration", "Gut-Brain"],
    "color": "#1E1E2A",
    "emoji": "💊"
  },
  {
    "num": 4,
    "name": "Morning Wellness Capsules",
    "capsules": [               // multi-capsule cards have capsules array instead of pills
      {
        "label": "Capsule 1",
        "weight": "258mg",
        "components": [
          { "name": "Vitamin C", "dose": "250mg" }
        ]
      }
    ]
  }
]
```

### `goal_cards` → Section 6

```json
[
  {
    "emoji": "🛡️",
    "title": "Stronger immune resilience",
    "mechanism": "Around 70% of your immune system is shaped by your gut bacteria...",
    "formula_link": "Your formula targets this through Zinc and Vitamin C..."
  }
]
```
Max 4 cards.

### `cited_papers` → References section

```json
[
  {
    "citation": "Chen et al., 2024",
    "title": "When smoke meets gut: deciphering the interactions...",
    "abstract": "...",
    "venue": "Science China Life Sciences",
    "year": 2024,
    "doi": "10.1007/s11427-023-2446-y",
    "authors": ["Bo Chen", "Guangyi Zeng", "Lulu Sun", "Changtao Jiang"]
  }
]
```
Render as APA format. DOI should be a clickable link.

---

## Rendering Rules

| Field | Render rule |
|---|---|
| `score_summary` | `innerHTML` — contains `<strong>` HTML |
| `overall_score.total` | Number. Animate SVG stroke-dashoffset on load |
| `circle_scores.*` | 0-100. Green ≥75, Amber ≥50, Red <50 |
| `guild_timepoints` | Array — if length > 1, show time-slider control |
| `safety_profile.any_detected` | If `true`, show red warning banner |
| `supplement_cards[].capsules` | If non-null, render per-capsule sub-sections |
| `root_cause_data.deviation_cards[].narrative` | Multi-paragraph text split on `\n\n` |
| `profile.goals` | Array of strings — render as badges |

---

## Regenerating the JSON

```bash
cd science-engine

# Full run with LLM (production)
python report/generate_health_report.py \
  --sample-dir /path/to/analysis/batch/sample/

# Re-render HTML only (zero LLM cost — uses cached JSON)
python report/generate_health_report.py \
  --sample-dir /path/to/analysis/batch/sample/ \
  --use-cached
```

---

## What is NOT in this JSON (and where it lives)

| Artifact | Location | Purpose |
|---|---|---|
| `microbiome_analysis_master_{id}.json` | `reports/reports_json/` | Full scientific data, scoring details — internal use only |
| `formulation_master_{id}.json` | `reports/reports_json/` | Full formulation pipeline output — internal |
| `manufacturing_recipe_{id}.json` | `reports/reports_json/` | Manufacturing instructions — ops only |
| `health_report_{id}.html` | `reports/reports_html/` | Pre-rendered HTML reference — visual QA |
