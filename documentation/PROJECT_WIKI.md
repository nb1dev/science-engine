# NB1 Microbiome Platform — Project Wiki

**Last Updated:** March 4, 2026  
**Audience:** New team members, collaborators, and technical reviewers  
**Maintainer:** Polina Novikova

---

# Table of Contents

- [PART I — THE BIG PICTURE](#part-i--the-big-picture)
  - [1. What We Do](#1-what-we-do)
  - [2. Why It Matters](#2-why-it-matters)
  - [3. What We Produce — The Personalized Supplement](#3-what-we-produce--the-personalized-supplement)
  - [4. The Client Journey](#4-the-client-journey)
  - [5. Key Concepts Glossary](#5-key-concepts-glossary)
- [PART II — THE SCIENCE](#part-ii--the-science)
  - [6. The 6 Functional Guilds](#6-the-6-functional-guilds)
  - [7. CLR Ratios & Metabolic Dials](#7-clr-ratios--metabolic-dials)
  - [8. The 5-Pillar Scoring System](#8-the-5-pillar-scoring-system)
  - [9. Priority Classification System](#9-priority-classification-system)
  - [10. Vitamin Signal Logic](#10-vitamin-signal-logic)
  - [11. The 8 Probiotic Mixes](#11-the-8-probiotic-mixes)
- [PART III — THE PIPELINE (Technical)](#part-iii--the-pipeline-technical)
  - [12. Pipeline Architecture Overview](#12-pipeline-architecture-overview)
  - [13. Stage 1: Bioinformatics](#13-stage-1-bioinformatics)
  - [14. Stage 2: JSON Report Generation](#14-stage-2-json-report-generation)
  - [15. Stage 3: Narrative Report Generation](#15-stage-3-narrative-report-generation)
  - [16. Stage 4: Formulation Pipeline](#16-stage-4-formulation-pipeline)
- [PART IV — THE FORMULATION ENGINE (Deep Dive)](#part-iv--the-formulation-engine-deep-dive)
  - [17. Input Parsing](#17-input-parsing)
  - [18. The Rules Engine](#18-the-rules-engine)
  - [19. LLM Decisions](#19-llm-decisions)
  - [20. Post-Processing & Safety Gates](#20-post-processing--safety-gates)
  - [21. Weight Calculation & Delivery Formats](#21-weight-calculation--delivery-formats)
  - [22. Output Artifacts](#22-output-artifacts)
- [PART V — OPERATIONS & REFERENCE](#part-v--operations--reference)
  - [23. Running the Pipeline — Command Reference](#23-running-the-pipeline--command-reference)
  - [24. Directory Structure & File Map](#24-directory-structure--file-map)
  - [25. Knowledge Base Reference](#25-knowledge-base-reference)
  - [26. AWS Configuration](#26-aws-configuration)
  - [27. Quality Assurance](#27-quality-assurance)
- [PART VI — ROADMAP & FUTURE DEVELOPMENT](#part-vi--roadmap--future-development)
  - [28. The 16-Week Follow-Up](#28-the-16-week-follow-up-rd--not-yet-implemented)
  - [29. Scientific Improvements](#29-scientific-improvements)
  - [30. Technical / Engineering Improvements](#30-technical--engineering-improvements)
  - [31. Bioinformatics Improvements](#31-bioinformatics-improvements)
  - [32. Long-Term Data Science Vision](#32-long-term-data-science-vision)
  - [33. Current Status — A Strong MVP](#33-current-status--a-strong-mvp)

---

# PART I — THE BIG PICTURE

This part explains what our project does in plain language. No prior knowledge of microbiome science or bioinformatics is required.

---

## 1. What We Do

We build **personalized gut health supplements** based on each individual client's unique microbiome composition.

Here's the core idea in one sentence: *A client sends us a stool sample, we sequence the DNA of all the bacteria living in their gut, analyze what's there (and what's missing), combine that with their health questionnaire, and produce a custom supplement formula tailored specifically to their microbiome.*

This is fundamentally different from buying a generic probiotic off the shelf. A generic probiotic contains the same strains and doses for everyone. Our product is different for every single client — the probiotic strains, the prebiotic fibers, the vitamins, the minerals, the botanicals, the doses, even the timing (morning vs evening) are all determined by that individual person's gut bacteria and health profile.

### The Pipeline at a Glance

```
Stool Sample → DNA Sequencing → Bioinformatics Analysis → Microbiome Health Report → Personalized Formulation → Manufacturing → Client Receives Custom Product
```

The stool samples are collected using home collection kits. The DNA is extracted and sequenced using **whole-genome shotgun (WGS) metagenomic sequencing** — this means we read ALL the DNA from ALL organisms in the sample, not just target one gene (like 16S rRNA). This gives us both **taxonomic** information (which bacteria are present and how abundant they are) and **functional** information (what metabolic pathways those bacteria are capable of running).

The sequencing is performed by an external partner laboratory. The raw sequencing data (FASTQ files — pairs of DNA reads) is uploaded to our **AWS S3 bucket**. From there, our automated pipeline takes over: it downloads the data, runs the bioinformatics analysis, generates health reports, and creates the personalized supplement formulation — all largely automated with targeted AI assistance at key clinical decision points.

---

## 2. Why It Matters

### The Gut Microbiome Is an Ecosystem

The human gut contains roughly **38 trillion** bacteria — roughly one bacterial cell for every human cell in the body. These bacteria aren't random passengers; they form a complex **ecosystem** with defined roles and relationships, much like a rainforest or a coral reef.

Some bacteria break down dietary fiber into smaller molecules. Others take those smaller molecules and produce **short-chain fatty acids (SCFAs)** like butyrate, which is the primary fuel source for the cells lining the colon. Others produce vitamins, modulate the immune system, or maintain the protective mucus layer.

When this ecosystem is **balanced**, everything works smoothly — efficient digestion, strong gut barrier, healthy immune signaling, even positive effects on mood and brain function (the gut-brain axis). When it becomes **imbalanced** (called **dysbiosis**), things break down: poor digestion, inflammation, increased gut permeability ("leaky gut"), vitamin deficiencies, and a range of symptoms from bloating to fatigue to skin problems.

### Why Generic Probiotics Fall Short

A generic probiotic might contain 2-3 common strains (like *Lactobacillus acidophilus* and *Bifidobacterium lactis*) at a fixed dose. But what if your specific problem isn't that those bacteria are missing? What if the issue is that your **fiber-degrading bacteria** are depleted, or your **butyrate producers** are being outcompeted by protein-fermenting bacteria? A generic probiotic won't address those specific ecological imbalances.

Our approach is different: we first **diagnose** the specific ecological imbalances in each person's gut, then **design** a supplement formula that addresses those exact problems — the right strains to fill the gaps, the right prebiotic fibers to feed them, and the right supporting nutrients based on what the microbiome tells us.

---

## 3. What We Produce — The Personalized Supplement

### The Physical Product

Each client receives a **daily supplement kit** consisting of multiple delivery units, designed to be taken over a **16-week protocol**. A typical daily kit might include:

| # | Unit | Timing | What It Contains | Why This Format |
|---|------|--------|------------------|-----------------|
| 1 | **Probiotic Hard Capsule** | Morning | 5-8 specific probiotic strains, 50B CFU total | Acid-resistant capsule protects live bacteria through stomach acid |
| 2 | **Omega + Antioxidant Softgel** | Morning (×2) | Omega-3 (DHA+EPA), Vitamin D3, Vitamin E, Astaxanthin | Fat-soluble compounds need oil matrix for absorption |
| 3 | **Daily Sachet** (powder drink) | Morning | Prebiotic fibers + water-soluble vitamins + supplements | Volume — prebiotic fibers are bulky (3-10g), can't fit in a capsule |
| 4 | **Morning Wellness Capsule** | Morning | Polyphenols (e.g., Curcumin + Piperine) | Bitter/pungent substances taste terrible in a drink |
| 5 | **Stress & Relaxation Capsule** | Evening | Calming supplements (e.g., Ashwagandha, L-Theanine) | Evening timing for sleep support and relaxation |
| 6 | **Magnesium Capsule** | Evening (×1-2) | Magnesium bisglycinate | Evening for sleep, recovery, and relaxation |

Not every client gets all units. For example, a client with no stress or sleep issues may not receive units 5-6. A client with no skin/brain/immune goals may not receive unit 2. The composition and inclusion of each unit is determined by the analysis.

### Here Is a Real Example

From client `1421794165663` (batch nb1_2026_006):

- **Unit 1**: Probiotic capsule — Mix 4 (Proteolytic Suppression) — 5 strains at 50B CFU total. This client had proteolytic overgrowth, so we selected strains that competitively suppress protein-fermenting bacteria.
- **Unit 2**: 2× Omega softgels — Omega-3, D3, E, Astaxanthin
- **Unit 3**: Daily sachet — PHGG 2.5g + Beta-glucans 1.0g + B1 + B2 + B12 + Vitamin C + Zinc (3.76g total)
- **Unit 4**: Morning capsule — Curcumin 500mg + Piperine 5mg (anti-inflammatory)
- **Unit 5**: Evening capsule — Ashwagandha 600mg (stress 7/10 → calming support)
- **Unit 6**: Evening capsule 2 — Quercetin 450mg + L-Theanine 200mg
- **Unit 7**: Magnesium capsule — 750mg bisglycinate (105mg elemental)
- **Total**: 8 daily units, 8.27g total weight, 16-week protocol

### What's Personalized vs What's Fixed

Every component in the formula is tagged with its **source** — explaining WHY it's there:

**Microbiome-driven** (unique per person, based on gut analysis):
- Which of 8 probiotic mixes is selected (determined by guild analysis — which bacterial teams are depleted or overgrown)
- Which prebiotic fibers and at what doses (determined by gut sensitivity + which guilds need feeding)
- Which vitamins are triggered by microbiome signals (B12, folate, biotin, B-complex — based on presence/absence of vitamin-producing bacteria)

**Questionnaire-driven** (lifestyle personalization):
- Sleep supplements: L-Theanine, Melatonin, Valerian Root — selected based on sleep quality score and sleep problem type (onset vs maintenance)
- Stress adaptogens: Ashwagandha — routed to evening capsule when calming goals present. LP815 psychobiotic strain added to probiotic mix when stress ≥6/10
- Magnesium dosing: 1 or 2 capsules — based on a 3-criteria scoring: sleep quality, sport/exercise level, stress level
- Softgel inclusion: Based on goals like skin health, brain health, immune support
- Polyphenols: Curcumin, Quercetin, Bergamot — selected based on health goals and inflammation markers
- Timing: Whether supplements go morning (sachet) vs evening (capsule) — based on whether the client has calming goals

**Fixed components** (same standard for everyone who gets them):
- Softgel composition: Omega-3 + D3 + E + Astaxanthin is a fixed blend (if included)
- Capsule format: Size 00 vegetarian hard capsules
- Sachet capacity: Maximum 19g
- Capsule capacity: Maximum 650mg per capsule
- Protocol duration: 16 weeks

### Source Attribution

Every single component in the final formula is tagged with one of three source labels:
- **`microbiome_primary`** — Directly driven by gut bacteria analysis (e.g., the probiotic mix, prebiotics, microbiome-flagged vitamins)
- **`microbiome_linked`** — Driven by microbiome analysis combined with questionnaire data (e.g., omega-3 when both gut-brain pattern AND mood goal exist)
- **`questionnaire_only`** — Driven purely by the client's reported goals, symptoms, and lifestyle (e.g., ashwagandha for stress, melatonin for sleep onset problems)

This transparency means we can tell the client: "70% of your formula is based on your gut bacteria, 30% is based on your health goals" — and trace every ingredient back to its scientific justification.

---

## 4. The Client Journey

1. **Client receives a home collection kit** — stool sample collected at home
2. **Sample shipped to partner lab** — DNA extracted, whole-genome shotgun sequencing performed
3. **Raw data uploaded to AWS S3** — paired-end FASTQ files organized by batch
4. **Automated bioinformatics pipeline runs** — species identification (MetaPhlAn), pathway analysis (HUMAnN), health association scoring (GMWI2), guild analysis, CLR ratios, vitamin signals
5. **Client fills out health questionnaire** — goals, symptoms, diet, lifestyle, medical history, sleep, stress, exercise
6. **Automated report generation** — microbiome health report (JSON + narrative PDF + interactive HTML dashboard)
7. **Automated formulation generation** — personalized supplement formula based on microbiome analysis + questionnaire
8. **Nutritionist review** — formulation reviewed before production
9. **Manufacturing** — custom supplement kit produced per the manufacturing recipe
10. **Client receives personalized product** — 16-week supply with explanation of what's inside and why
11. **Follow-up** *(future — not yet implemented)* — Second sample after 16 weeks to assess changes

---

## 5. Key Concepts Glossary

| Term | Simple Explanation |
|------|-------------------|
| **Microbiome** | The community of all microorganisms living in the gut (mostly bacteria) |
| **Metagenomics** | Sequencing ALL DNA from a sample (not just one organism) to identify every species and their capabilities |
| **FASTQ files** | The raw output from DNA sequencing — text files containing millions of short DNA reads |
| **MetaPhlAn** | Software that identifies which bacterial species are present and their relative abundance |
| **HUMAnN** | Software that identifies which metabolic pathways the bacteria can perform |
| **Guild** | A group of bacteria that perform the same ecological function (like a "team" in the gut ecosystem) |
| **GMWI2** | Gut Microbiome Wellness Index — a validated health score based on 155 signature species |
| **CLR** | Centered Log-Ratio — a mathematical transformation for comparing relative abundances of bacterial groups |
| **SCFA** | Short-Chain Fatty Acids — the main beneficial products of gut fermentation (butyrate, acetate, propionate) |
| **Butyrate** | The primary fuel for colon cells — produced by specific gut bacteria from fiber fermentation |
| **Prebiotics** | Non-digestible fibers and compounds that selectively feed beneficial gut bacteria |
| **Probiotics** | Live beneficial bacteria provided as a supplement to restore depleted populations |
| **Synbiotic** | A combination of probiotics + prebiotics designed to work together |
| **CFU** | Colony-Forming Units — the standard measure of live bacteria (e.g., 50 billion CFU = 50B CFU) |
| **Dysbiosis** | An imbalanced gut microbiome where harmful patterns dominate over healthy ones |
| **FFA Weighting** | Fractional Functional Attribution — our method of assigning multi-guild bacteria proportionally |
| **AWS S3** | Amazon's cloud storage where sequencing data is stored |
| **AWS Bedrock** | Amazon's AI service where we run Claude language models for report narratives and clinical decisions |
| **FODMAP** | Fermentable carbohydrates that can cause gas/bloating in sensitive individuals |

---

# PART II — THE SCIENCE

This part explains the scientific framework behind our analysis. It's written for someone with a basic science background who wants to understand the biological reasoning.

---

## 6. The 6 Functional Guilds

### Why Guilds Instead of Individual Species?

A typical gut sample contains 200-400 bacterial species. Analyzing each one individually would be overwhelming and scientifically imprecise — many species have overlapping functions. Instead, we group bacteria into **6 functional guilds** based on their metabolic role in the fermentation cascade. This is grounded in ecological guild theory (Faust & Raes, 2012): organisms that use the same resources in the same way form functional units regardless of taxonomy.

Think of it like a factory with 6 departments. We don't need to know every individual worker's name — we need to know whether each department is properly staffed, whether they have the raw materials they need, and whether the assembly line between departments is flowing smoothly.

### The 6 Guilds

| # | Guild (Scientific) | Guild (Client-Facing) | Ecological Role | Healthy Range |
|---|--------------------|-----------------------|-----------------|---------------|
| 1 | **Fiber Degraders** | Fiber-Processing Bacteria | Primary polysaccharide processors — the **ecosystem gatekeepers**. They break down complex dietary fibers into simpler sugars and molecules that downstream guilds can use. | 30-50% |
| 2 | **Bifidobacteria** | Bifidobacteria | Early fermentation **amplifiers**. Convert oligosaccharides to lactate and acetate via the phosphoketolase pathway. Rapid acidification of the gut environment (which inhibits pathogens). | 2-10% |
| 3 | **Cross-Feeders** | Intermediate Processors | Secondary fermentation **stabilizers**. The CRITICAL CONNECTORS between degradation and terminal SCFA production. They consume the lactate, acetate, succinate, and hydrogen produced by guilds 1 and 2, converting them into substrates for guild 4. | 6-12% |
| 4 | **Butyrate Producers** | Gut-Lining Energy Producers | Terminal SCFA **producers**. They are downstream-dependent (they need acetate from upstream guilds). They produce butyrate — the primary fuel for colonocytes (the cells lining your colon). | 10-25% |
| 5 | **Mucin Degraders** | Mucus-Layer Bacteria | Host-substrate **users**. They consume the protective mucus layer as a carbon source. This is normal and adaptive in small amounts (a "backup" when fiber is scarce), but pathological when chronic or excessive — it means the ecosystem is "eating the walls" instead of dietary fiber. | 5-10% |
| 6 | **Proteolytic Guild** | Protein-Fermenting Bacteria | Putrefactive **strategy** bacteria. They ferment protein instead of carbohydrates, producing ammonia, hydrogen sulfide (H₂S), phenols, indoles, and branched-chain fatty acids (BCFAs). These are inflammatory metabolites. Expansion indicates the ecosystem has shifted from carb-driven (healthy) to protein-driven (unhealthy). | 1-5% |

### The Trophic Cascade — How Guilds Connect

The guilds don't work in isolation. They form a metabolic **assembly line** (with parallel tracks):

```
                    DIETARY FIBER
                         │
                         ▼
              ┌─── GUILD 1: Fiber Degraders ───┐
              │    (Complex → Simple sugars)    │
              │                                 │
              ▼                                 ▼
    GUILD 2: Bifidobacteria          GUILD 3: Cross-Feeders
    (Lactate + Acetate)              (Propionate + H₂ metabolism)
              │                                 │
              └──────────┬──────────────────────┘
                         │    (Acetate + other intermediates)
                         ▼
              GUILD 4: Butyrate Producers
              (BUTYRATE → feeds colon cells)
                         │
                         ▼
                    SCFA POOL → Health Benefits

    ═══════════════════════════════════════════════
    BACKUP PATHWAY:              COMPETING PATHWAY:
    GUILD 5: Mucin Degraders     GUILD 6: Proteolytic Guild
    (Host mucus → carbon)        (Protein → toxic metabolites)
    ↑ Increases when fiber       ↑ Increases when carb guilds
      is scarce                    are depleted
```

**Key principle:** A healthy gut = carbohydrate-driven ecosystem. Dysbiosis = protein-driven and mucin-driven ecosystem.

### The Factory Analogy (Used in Client Reports)

We explain guilds to clients using a factory metaphor:

> "Think of your gut as a factory with 6 departments. Department 1 (Fiber Processors) receives raw materials (fiber from your food) and breaks them into parts. Departments 2 and 3 (Bifidobacteria and Intermediate Processors) work in parallel to refine these parts into semi-finished goods. Department 4 (Gut-Lining Energy Producers) takes those semi-finished goods and produces the final product — butyrate, the fuel your gut lining needs.
>
> Department 5 (Mucus-Layer Bacteria) is a backup team — when Department 1 doesn't get enough raw materials, Department 5 starts consuming the factory's own walls (mucus) for fuel. Department 6 (Protein-Fermenting Bacteria) is a competitor — they produce toxic waste products instead of useful ones. In a healthy factory, Departments 1-4 dominate. In a struggling factory, Departments 5 and 6 start taking over."

### How We Assign Species to Guilds — FFA Weighting

Many bacterial species contribute to multiple guilds. For example, *Roseburia intestinalis* degrades some fibers (Guild 1) AND produces butyrate (Guild 4). Rather than arbitrarily assigning each species to one guild, we use **Fractional Functional Attribution (FFA)** — each species's abundance is split proportionally across the guilds it contributes to, based on literature-documented metabolic capabilities.

This means a species that is 80% butyrate producer and 20% fiber degrader will have its abundance assigned accordingly. The guild assignments are stored in `{sample_id}_functional_guild.csv` — a tab-delimited file listing every detected species, its abundance, and its FFA-weighted guild allocations.

---

## 7. CLR Ratios & Metabolic Dials

### Why CLR?

Microbiome data is **compositional** — the abundances are relative percentages that must sum to 100%. This means that if one group increases, others must decrease, even if their absolute numbers didn't change. Standard statistics don't work properly on compositional data (Gloor et al., 2017).

**Centered Log-Ratio (CLR)** transformation is the standard solution. For each guild, CLR tells us: "Is this guild doing better or worse than the geometric average of all guilds in this sample?" A positive CLR means the guild is enriched relative to the community; a negative CLR means it's suppressed.

### The 4 Diagnostic Ratios

We compute 4 ratios from guild-level CLR values. Each captures a fundamental metabolic trade-off:

| Ratio | Name | Formula | What It Measures |
|-------|------|---------|-----------------|
| **CUR** | Carbohydrate Utilization Ratio | [(Fiber_CLR + Bifido_CLR)/2] − Proteo_CLR | **Substrate competition**: are carbohydrate guilds winning against protein fermenters? Positive = healthy, carb-dominant. Negative = protein-driven dysbiosis. |
| **FCR** | Fermentation Completion Ratio | [(Butyrate_CLR + Cross_CLR)/2] − Bifido_CLR | **Terminal processing efficiency**: are intermediates being successfully converted to SCFAs? Positive = efficient. Negative = metabolite accumulation, broken cross-feeding. |
| **MDR** | Mucus Dependency Ratio | Mucin_CLR − Fiber_CLR | **Host vs dietary substrate**: are bacteria eating your food or your gut lining? Positive = mucus-dependent (concerning). Negative = diet-fed (healthy). |
| **PPR** | Putrefaction Pressure Ratio | Proteo_CLR − Butyrate_CLR | **Harsh vs gentle metabolites**: which metabolic strategy dominates? Positive = inflammatory (protein fermentation). Negative = anti-inflammatory (SCFA production). |

### Threshold Interpretation

| Ratio | Favorable | Neutral | Unfavorable |
|-------|-----------|---------|-------------|
| CUR | > +0.3 | ±0.3 | < −0.3 |
| FCR | > +0.3 | ±0.3 | < −0.3 |
| MDR | < −0.2 | ±0.2 | > +0.2 |
| PPR | < −0.2 | ±0.2 | > +0.2 |

These thresholds are derived from observed distributions in healthy reference cohorts. The ±0.3 and ±0.2 boundaries separate clinically meaningful metabolic states from noise.

### Special Case: When a Guild Is Absent

When a guild has <1% abundance, CLR is mathematically unstable (log of near-zero). In these cases, CLR is treated as 0 for the ratio calculations. The guild's depletion is captured by other metrics (the priority classification system).

---

## 8. The 5-Pillar Scoring System

We compute an **overall gut health score from 0 to 100** for each client, built from 5 pillars:

| Pillar | Max Points | Weight | What It Measures |
|--------|-----------|--------|-----------------|
| **P1: Health Association** | 20 | 20% | GMWI2 model — a validated score based on 155 signature taxa, trained on 8,069 metagenomes from 26 countries. The broadest validated health predictor available. |
| **P2: Diversity & Resilience** | 20 | 20% | Shannon diversity (species richness) + guild evenness (are teams balanced internally or dominated by one species?). Predicts ecosystem stability. |
| **P3: Metabolic Function** | 20 | 20% | The 4 CLR ratios (CUR, FCR, MDR, PPR). Direct measure of the fermentation state — is the factory running smoothly? |
| **P4: Guild Balance** | 30 | **30%** | Are all 6 guilds within their healthy ranges? This is weighted highest because it captures the **actionable** imbalances — the specific teams that need intervention. |
| **P5: Safety Profile** | 10 | 10% | Binary/threshold checks — presence of disease-associated bacteria (*F. nucleatum*, *S. gallolyticus*, etc.), methane archaea overgrowth, BCFA pathway activity. |

**Why Guild Balance gets 30%:** It's the most intervention-relevant pillar. A low score here directly maps to specific action plan steps. The other pillars describe the context; guild balance describes the problem.

### Score Bands

| Score | Band | Meaning |
|-------|------|---------|
| 80-100 | Excellent | Well-balanced ecosystem, minor optimization possible |
| 60-79 | Good | Generally healthy with some areas for improvement |
| 40-59 | Fair | Noticeable imbalances requiring targeted intervention |
| 20-39 | Needs Attention | Significant imbalances affecting multiple systems |
| 0-19 | Critical | Severe dysbiosis requiring comprehensive restoration |

---

## 9. Priority Classification System

Every guild is classified into a priority level that determines how urgently it needs intervention. This uses a **9-scenario matrix** based on three axes:

1. **Range position**: Above range / Within range / Below range
2. **CLR competitive status**: Enriched (>+0.3) / Balanced (±0.3) / Suppressed (<-0.3)
3. **Guild type**: Beneficial (guilds 1-4) or Contextual (guilds 5-6)

### The 9 Scenarios

| Range × CLR | Enriched CLR | Balanced CLR | Suppressed CLR |
|-------------|-------------|-------------|----------------|
| **Above range** | OVERGROWTH | ABUNDANT | CROWDED |
| **Within range** | THRIVING | HEALTHY | UNDER PRESSURE |
| **Below range** | SUBSTRATE LIMITED | UNDERSTAFFED | DEPLETED |

Each scenario maps to a **priority level**:

| Level | Icon | Beneficial Guild Criteria | Contextual Guild Criteria |
|-------|------|--------------------------|--------------------------|
| **CRITICAL** | 🔴 | Absent (0%) or < 50% of minimum | > 3× above maximum |
| **1A** | 🟠 | Below range + CLR < -1.0 (being actively outcompeted) | > 2× above maximum |
| **1B** | 🟡 | Below range (other cases), or CLR < -0.5 within range | > maximum |
| **Monitor** | 🟢 | Within range, stable CLR | Within range |

### Priority Score Formula

```
Priority Score = Importance Weight × State Value × Evenness Modifier
```

- **Importance Weight**: Ecological role weight (Butyrate=1.2, Cross-feeders=1.1, Fiber=1.0, Bifido=0.9, Proteolytic=1.1, Mucin=0.6)
- **State Value**: From scenario (DEPLETED=10, UNDERSTAFFED=7, SUBSTRATE LIMITED=5, UNDER PRESSURE=3, OVERGROWTH=10, ABUNDANT=6, CROWDED=4, HEALTHY/THRIVING=0)
- **Evenness Modifier**: Low guild diversity amplifies urgency (J<0.40 → 1.2-1.3× multiplier)

Score thresholds: ≥8.0 = CRITICAL, ≥5.0 = 1A, ≥2.0 = 1B, <2.0 = Monitor.

This priority system flows through the entire pipeline: it determines guild priority labels in reports, ordering in narrative reports, and most critically — which probiotic mix is selected for the formulation.

---

## 10. Vitamin Signal Logic

Rather than relying on pathway abundance (which can be misleading), we use **compositional signals** — the presence or absence of specific vitamin-producing bacteria — to assess vitamin synthesis capacity.

### The Two-Category Framework

**Category 1 — Population Risk (Always Supplement):**
These are based on well-established population-level evidence:
- B12 50-100µg daily if vegan/vegetarian (mandatory)
- B12 500µg daily if age ≥50 (mandatory)
- Folate 400µg daily if pregnant/lactating (mandatory)

**Category 2 — Microbiome-Conditional (Signal-Based):**
These depend on what we see in the gut bacteria:

| Vitamin | Signal Type | How It Works | Risk Triggers |
|---------|-------------|-------------|---------------|
| **B12** | Inverse signal | Elevated *Akkermansia* (>8%) is paradoxically associated with B12 deficiency risk (Mendelian Randomization evidence, Hou et al., 2025) | Akkermansia >8% + supporting genera low |
| **Folate** | Diversity-dependent | Production capacity depends on: Shannon diversity (>2.0), *Bacteroides* (>5%), *Bifidobacterium* (>2%). Risk score = count of failed thresholds | Risk score ≥2 out of 3 |
| **Biotin** | Producer count | Based on detection of 4 key biotin-producing species: *B. fragilis*, *P. copri*, *F. varium*, *C. coli* | ≤1 producers detected out of 4 |
| **B-Complex** | Composition-based | Based on: *Bacteroides* >10%, F:B ratio <1.85, *Lachnospiraceae* + *Ruminococcaceae* >2%. Risk = count of failed criteria | Risk score ≥2 out of 3 + symptoms |

These vitamin signals appear in Section 4 of the metrics report and directly drive vitamin inclusion in the formulation.

---

## 11. The 8 Probiotic Mixes

We maintain a library of **8 pre-designed probiotic mixes**, each targeting a specific ecological pattern. The mix selection is **fully deterministic** — based on guild analysis results, not AI judgment. Each mix contains clinically studied strains with specific rationale.

**Standard dose: 50B CFU/day for all mixes.**

### Mix Overview

| # | Name | Strains | Primary Trigger | When It's Selected |
|---|------|---------|-----------------|-------------------|
| 1 | **Dysbiosis Recovery** | 5 | Broad ecosystem dysfunction | ≥3 beneficial guilds compromised, cross-feeders broken, fiber outcompeted |
| 2 | **Bifidogenic Restore** | 5 | Keystone guild failure | Bifidobacteria absent or depleted (rest of ecosystem preserved) |
| 3 | **Fiber & SCFA Restoration** | 4 | Substrate limitation | Butyrate/fiber degraders low, substrate-limited (CLR neutral/positive) |
| 4 | **Proteolytic Suppression** | 5 | Active threat | Proteolytic guild overgrowth (>5%, score ≥5.0) |
| 5 | **Mucus Barrier Restoration** | 5 | Barrier stress | Mucin degraders above range + diet-fed/neutral MDR |
| 6 | **Maintenance Gold Standard** | 8 | Healthy ecosystem | All guilds at Monitor priority — no actionable imbalances |
| 7 | **Psychobiotic** | 5 | Gut-brain primary | Clinician-directed for primary gut-brain axis intervention |
| 8 | **Fiber Expansion & Displacement** | 5 | Akkermansia overgrowth | Mucin degraders >10% + MDR >+0.5 + Fiber <30% |

### LP815 Psychobiotic Enhancement

**LP815** (*Lactiplantibacillus plantarum* LP815) is a psychobiotic strain that produces **50mg GABA** (a calming neurotransmitter) at 5B CFU. It can be **added to any mix** when:
- Stress ≥6/10, OR
- Stress ≥4/10 + mood/anxiety goal

This means any mix can become a partial psychobiotic formula when the client needs stress/mood support, without requiring the full Mix 7.

### Each Mix Has a Tailored Prebiotic Strategy

Each mix comes with specific prebiotic recommendations — which fibers to include, which to avoid, and how to adapt for sensitive clients (bloating, FODMAP intolerance, IBS). For example:
- **Mix 2 (Bifidogenic)** uses GOS + Inulin + FOS (bifidogenic fibers) as defaults, but switches to PHGG + Beta-glucans for sensitive clients
- **Mix 8 (Fiber Expansion)** uses a HIGH-LOAD strategy with complex polysaccharides (*Akkermansia* can't use) — Beta-glucans, Psyllium, Resistant Starch — specifically chosen to competitively displace *Akkermansia* by expanding fiber-degrading guilds

---

# PART III — THE PIPELINE (Technical)

This part describes the automated computational pipeline. It assumes familiarity with the concepts from Parts I-II.

---

## 12. Pipeline Architecture Overview

The pipeline has **4 independent stages**, each producing distinct outputs:

```
┌────────────────────────────────────────────────────────────────────┐
│  STAGE 1: BIOINFORMATICS               run_sample_analysis.sh     │
│                                                                     │
│  S3 Bucket → Download → QC → GMWI2 → Metrics Calculation          │
│                                                                     │
│  Output: _only_metrics.txt + _functional_guild.csv                 │
└───────────────────────────────┬────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────────┐
│  STAGE 2: JSON REPORT GENERATION        generate_report.py         │
│                                                                     │
│  Metrics → Parse → Score → Fields → LLM Narratives → JSON         │
│                                                                     │
│  Output: _microbiome_analysis.json (master) + _platform.json       │
└───────────────────────────────┬────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────────┐
│  STAGE 3: NARRATIVE REPORT              generate_narrative_report.py│
│                                                                     │
│  Analysis JSON → 10 LLM Calls → Markdown → PDF                    │
│                                                                     │
│  Output: _narrative_report.md + _narrative_report.pdf               │
└───────────────────────────────┬────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────────┐
│  STAGE 4: FORMULATION                   generate_formulation.py    │
│                                                                     │
│  Analysis JSON + Questionnaire → Rules → LLM → Weight Calc → JSON │
│                                                                     │
│  Output: formulation_master.json + manufacturing_recipe + dashboards│
└────────────────────────────────────────────────────────────────────┘
```

Each stage can be run independently. Stage 1 feeds Stage 2, which feeds Stages 3 and 4. The main pipeline orchestrator (`run_sample_analysis.sh`) runs all four stages sequentially for convenience.

---

## 13. Stage 1: Bioinformatics

**Script:** `platform_automation/pipeline_scripts/run_sample_analysis.sh`

### What Happens

1. **Discover samples** from the S3 bucket (`s3://nb1-prebiomics-sample-data/incoming/{batch}/`)
2. **Download 3 data types** per sample:
   - `functional_profiling/` — HUMAnN output (pathway abundances)
   - `raw_sequences/` — Original FASTQ files (paired-end R1/R2)
   - `taxonomic_profiling/` — MetaPhlAn output (species abundances)
3. **QC Precheck** — Validates data completeness and quality, generates confidence score (non-blocking — pipeline continues even if QC is low)
4. **GMWI2 Analysis** — Runs the Gut Microbiome Wellness Index algorithm using the taxonomic profile:
   - Uses a pre-trained sklearn LogisticRegression model (155 signature taxa)
   - Produces a health association score and lists detected signature taxa with their coefficients
5. **Integrated Metrics Report** — The core computational step. Calculates ALL metrics:
   - 7 compositional metrics (GMWI2, CB, BR, WD, HF, wGMWI2, SB)
   - Shannon diversity + Pielou evenness
   - 6 guild abundances with FFA weighting
   - 4 CLR diagnostic ratios (CUR, FCR, MDR, PPR)
   - Vitamin supplementation signals
   - Functional pathway analysis (SCFA, amino acids)
   - Select taxa screening (dysbiosis markers)
6. **Cleanup** — Deletes raw FASTQ files locally (they remain on S3; saves ~2GB per sample)
7. **Status tracking** — Writes `PIPELINE_SUCCESS` or `PIPELINE_FAILED` status file

### Input Data from Sequencing

The external lab performs **whole-genome shotgun (WGS) metagenomic sequencing** and runs two key bioinformatics tools before uploading:

- **MetaPhlAn v4** — Maps reads against a database of species-specific genetic markers to determine which species are present and their relative abundances. Output: `{sample}_metaphlan.txt`
- **HUMAnN v3/v4** — Maps reads against pathway databases (MetaCyc/UniRef) to determine which metabolic pathways are active. Output: `{sample}_pathabundance_relab.tsv` (relative pathway abundances) + `{sample}_pathcoverage.tsv` (pathway coverage scores)

### Output per Sample

```
analysis/{batch}/{sample}/
├── GMWI2/
│   ├── {sample}_run_GMWI2.txt           # Health index score
│   ├── {sample}_run_GMWI2_taxa.txt      # Detected signature taxa + coefficients
│   └── {sample}_run_metaphlan.txt       # Full species abundance table
├── bioinformatics/
│   └── only_metrics/
│       ├── {sample}_only_metrics.txt     # ← THE MAIN OUTPUT (comprehensive metrics report)
│       ├── {sample}_functional_guild.txt # Guild assignments (tab-delimited)
│       └── {sample}_functional_guild.csv # Guild assignments (CSV)
├── qc/
│   ├── {sample}_qc_precheck.json        # QC results (JSON)
│   └── {sample}_qc_precheck.md          # QC report (readable)
├── plots/
│   └── {sample}_integrated_analysis.pdf  # Visualization plots
└── logs/
    └── {sample}_pipeline.status          # Success/failure marker
```

---

## 14. Stage 2: JSON Report Generation

**Script:** `platform_automation/report_automation/generate_report.py`

This stage transforms the raw metrics into structured, interpreted JSON — both a comprehensive master file for internal use and a client-facing platform payload.

### Processing Chain

```
_only_metrics.txt ──→ parse_metrics.py ──→ Unified data dictionary
                                              │
        ┌─────────────────────────────────────┤
        ▼                                     ▼
   scoring.py                          overview_fields.py
   (5-pillar score 0-100)              (Deterministic interpretations)
        │                                     │
        │   ┌─────────────────────────────────┤
        │   │                                 │
        ▼   ▼                                 ▼
   narratives.py                     root_causes_fields.py
   (12 LLM calls to AWS Bedrock)     (Diagnostic flags, cascades)
        │                                     │
        │                                     ▼
        │                            action_plan_fields.py
        │                            (Prioritized intervention steps)
        │                                     │
        └──────────────┬──────────────────────┘
                       ▼
              platform_mapping.py
              (Extract client-facing content)
                       │
              ┌────────┴────────┐
              ▼                 ▼
    _microbiome_analysis.json   _platform.json
         (master file)          (API-ready payload)
```

### Module Breakdown

| Module | What It Does |
|--------|-------------|
| `parse_metrics.py` | Reads `_only_metrics.txt` and `_functional_guild.csv`, extracts all numeric values into a unified Python dictionary |
| `scoring.py` | Computes the 0-100 overall score from 5 pillars. Each pillar has both scientific and non-expert text interpretations |
| `overview_fields.py` | Computes all deterministic interpretation fields: overall balance, diversity assessment, 4 metabolic dials, guild status + priority levels, key strengths, key opportunities, vitamin risk signals |
| `narratives.py` | Makes 12 LLM calls to AWS Bedrock (Claude Sonnet) generating dual-format narratives: scientific + non-expert versions of summary, impacts, guild interpretations, metabolic assessment, etc. |
| `root_causes_fields.py` | Identifies diagnostic flags (e.g., "Protein-driven dysbiosis"), trophic cascade impacts, and reversibility assessment with estimated timeline |
| `action_plan_fields.py` | Generates prioritized intervention steps matching guild priority levels, with capacity assessments (using a "100-player" scale), timeline estimates, and progress forecasts |
| `platform_mapping.py` | Extracts non-expert content and structures it into a 5-tab platform JSON ready for API consumption |

### Output Files

**`microbiome_analysis_master_{sample}.json`** — The master file containing:
- Report metadata, executive summary
- Overall score (total, band, 5 pillars with dual interpretations)
- Ecological metrics (diversity, resilience, balance)
- Safety profile (dysbiosis markers, M. smithii, BCFA)
- Metabolic function (4 dials with descriptions)
- Vitamin synthesis (4 vitamins with risk assessments)
- 6 bacterial groups with status, CLR, evenness, priority level, dual interpretations
- Root causes (diagnostic flags, trophic cascades, reversibility)
- Action plan (prioritized steps)
- Debug data (raw metrics, guild summary)

**`microbiome_platform_{sample}.json`** — The 5-tab platform payload:
1. Overview tab (score, balance, dials, meanings)
2. Bacterial groups tab (6 guilds with capacity)
3. Root causes tab (flags, insights)
4. Vitamins tab (4 vitamins with status)
5. Action plan tab (prioritized steps)

**`microbiome_health_report_{sample}.html`** — Interactive HTML dashboard.

---

## 15. Stage 3: Narrative Report Generation

**Script:** `platform_automation/report_automation/generate_narrative_report.py`

This produces a comprehensive **5,000-7,000 word scientific narrative report** in Markdown (+ optional PDF), generated section-by-section through 10 AWS Bedrock LLM calls.

### The 10-Section Structure

| # | Section | Word Target | What It Covers |
|---|---------|-------------|---------------|
| 1 | Executive Summary | 400-500 | Overall pattern, critical finding, priority interventions |
| 2 | Compositional Metrics | 800-1,000 | All presence/abundance metrics, pattern classification |
| 3 | Diversity Signatures | 400-600 | Shannon, Pielou, ecosystem stability assessment |
| 4a | Guild Framework + CLR Dashboard | 1,200-1,500 | CLR methodology, ratio calculations, guild status table |
| 4b | Detailed Guild Assessments | 2,000-2,500 | All 6 guilds in A/B/C/D format (What We See / Means / Why / Restoration) |
| 4c | Metabolic Flow Diagram | 300-500 | ASCII parallel metabolic flow with substrate flows |
| 5 | Pathways & Vitamins | 600-800 | SCFA metabolism, vitamin signal assessment |
| 6 | Integrated Assessment | 800-1,000 | Cross-module pattern recognition, health implications |
| 7+8 | Restoration + Monitoring | 800-1,100 | Priority-ordered restoration steps, progress markers |
| 9+10 | Limitations + Disclaimer | 1,000-1,300 | Scientific caveats, medical disclaimer |

Each section is generated by a separate LLM call, with previous sections provided as context for consistency. The system prompt enforces strict formatting rules: third-person language, italicized taxonomy, non-prescriptive tone, no specific dosages or strain names.

The canonical **guild priority ordering** (CRITICAL → 1A → 1B → Monitor) is pre-computed by `shared/guild_priority.py` and injected into the LLM prompt to ensure the narrative report uses the exact same ordering as the JSON report and formulation.

### Output

- `narrative_report_{sample}.md` — Full Markdown report
- `narrative_report_{sample}.pdf` — PDF version (generated via pandoc + xelatex)

---

## 16. Stage 4: Formulation Pipeline

**Script:** `platform_automation/formulation_automation/generate_formulation.py`

This is the most complex stage. It takes the microbiome analysis JSON + client questionnaire and produces a complete personalized supplement formula. It has **6 internal stages** (A through F):

```
A. Parse Inputs → B. Deterministic Rules → C. LLM Decisions → D. Post-Processing → E. Weight Calculation → F. Output Assembly
```

This stage is covered in full detail in Part IV below.

---

# PART IV — THE FORMULATION ENGINE (Deep Dive)

This part provides a detailed walkthrough of how a personalized supplement formula is generated, from raw inputs to final manufacturing recipe.

---

## 17. Input Parsing

**Module:** `platform_automation/formulation_automation/parse_inputs.py`

The first step unifies two data sources into a single `unified_input` dictionary:

### Microbiome Data (from Stage 2 JSON)
- **Guild status**: All 6 guilds with abundance, CLR, status, evenness, priority level
- **CLR ratios**: CUR, FCR, MDR, PPR
- **Vitamin signals**: B12, Folate, Biotin, B-complex risk levels
- **Overall score**: Health score band and value
- **Root causes**: Diagnostic flags identifying the primary dysfunction pattern

### Questionnaire Data
- **Demographics**: Age, biological sex, BMI
- **Health goals**: Ranked list (e.g., "improve_skin_health", "boost_energy_reduce_fatigue", "reduce_stress_anxiety")
- **Digestive health**: Bloating severity (0-10), stool type (Bristol scale), digestive satisfaction
- **Lifestyle**: Stress level (0-10), sleep quality (0-10), sleep issues (onset/maintenance), exercise frequency, energy level
- **Medical**: Medications, reported vitamin deficiencies, medical conditions
- **Diet**: Diet pattern, food triggers, sensitivities
- **Completion tracking**: Which questionnaire sections were completed (some clients don't finish all sections)

The parser also computes a **questionnaire coverage assessment** — if important sections are missing, the formulation pipeline adjusts its confidence level and notes where it's making microbiome-only decisions.

---

## 18. The Rules Engine

**Module:** `platform_automation/formulation_automation/rules_engine.py`

All deterministic (non-AI) formulation decisions happen here. No LLM calls — pure Python logic against knowledge base JSON files. This is the "physics engine" of the formulation.

### 1. Sensitivity Classification

Based on digestive questionnaire data:
- **HIGH sensitivity**: Bloating ≥7/10, OR daily bloating, OR Bristol stool type 6-7, OR digestive satisfaction ≤3
- **MODERATE**: Between high and low thresholds, or insufficient data (default)
- **LOW**: Bloating ≤3/10 AND digestive satisfaction ≥7/10

Sensitivity directly controls **prebiotic dosing** — high sensitivity clients get conservative FODMAP-limited prebiotics (max 7-8g), low sensitivity can tolerate higher loads (up to 12g).

### 2. Health Claim Extraction

Maps questionnaire goals to EU health claim categories and identifies microbiome-driven vitamin needs:
- "improve_skin_health" → Skin Quality, Zinc, Vitamin E
- "reduce_stress_anxiety" → Stress/Anxiety, triggers timing rules for evening capsule
- Biotin risk_level ≥1 from microbiome → Fatigue + Skin Quality vitamin claims

### 3. Therapeutic Dose Triggers

Checks if client has reported deficiencies requiring elevated doses:
- Reported B12 deficiency + brain fog → therapeutic dose (not standard)
- Reported Iron deficiency + fatigue → enhanced dose

### 4. Prebiotic Dose Range Calculation

Determines allowed prebiotic gram range based on CFU tier + sensitivity:
- Standard 50B CFU + moderate sensitivity → 5-10g range
- Standard 50B CFU + high sensitivity → 3-7g range

### 5. Magnesium Need Assessment

3-criteria scoring:
- **Sleep**: Sleep quality ≤7 OR sleep goal → need
- **Sport**: Active lifestyle (moderate/vigorous exercise) → need
- **Stress**: Stress ≥6 OR stress/mood goal → need

Result: 0 needs = 0 capsules, 1 need = 1 capsule (750mg bisglycinate / 105mg elemental), ≥2 needs = 2 capsules (210mg elemental)

### 6. Softgel Need Assessment

Checks if client needs ANY of the 4 softgel components (Omega-3, D3, E, Astaxanthin). If yes, the fixed-composition softgel is included. Also checks contraindications (e.g., blood thinners → no omega).

### 7. Sleep Supplement Selection

Evidence-based decision tree:
- **Melatonin 1mg**: ONLY for sleep onset problems (difficulty_falling_asleep)
- **L-Theanine 200-400mg**: Default for arousal reduction (dose escalated for high stress + poor sleep)
- **Valerian Root 400mg**: Only for maintenance issues + sleep_quality ≤4

### 8. Timing Optimization

Determines morning vs evening for each component:
- **Magnesium**: Always evening
- **Ashwagandha**: Evening when calming goals present, morning only if pure energy goals with no calming needs
- **L-Theanine**: Evening when Mg is evening AND sleep/stress/anxiety goal present

### 9. Goal-Triggered Mandatory Supplements

Certain goals require specific supplements regardless of LLM selection:
- Energy/fatigue goal → B9 (Folate) + B12 + Vitamin C (standardized)

---

## 19. LLM Decisions

**Module:** `platform_automation/formulation_automation/llm_decisions.py`

Three key decisions involve AWS Bedrock LLM (Claude) because they require nuanced clinical reasoning:

### 1. Mix Selection (Deterministic — NOT LLM)

Despite being in the "decisions" stage, mix selection is **fully deterministic**. The algorithm evaluates guild priorities, CLR context, and scenario classifications to select the appropriate mix from the 8 options. This ensures consistency and auditability.

The decision logic follows a branching tree:
- **Branch A**: ≥3 beneficial guilds compromised → check if proteolytic/mucin overgrowth is dominant threat → Mix 1 or Mix 4/5/8
- **Branch B**: 1-2 beneficial guilds compromised → identify the specific bottleneck → Mix 2, 3, or 5
- **Branch C**: All beneficial guilds OK but contextual overgrowth → Mix 4, 5, or 8
- **Branch D**: Everything at Monitor → Mix 6 (Maintenance)

### 2. Supplement Selection (LLM)

The LLM selects vitamins, minerals, and non-vitamin supplements based on:
- Health claim categories (from rules engine)
- Microbiome vitamin signals
- Therapeutic/enhanced dose triggers
- Client goals and lifestyle data
- Knowledge base constraints (delivery format, dosing ranges, interactions)

The LLM returns structured JSON with substance, dose, delivery format, rationale, and interaction notes for each selected supplement. A strict **vitamin inclusion gate** (deterministic post-LLM filter) then removes any vitamins that lack clinical justification.

### 3. Prebiotic Design (LLM)

The LLM designs the prebiotic blend based on:
- The selected probiotic mix (each mix has preferred substrates)
- Client sensitivity classification
- FODMAP tolerance
- Total gram budget from rules engine
- Specific guild needs (e.g., if fiber degraders need substrate, favor complex polysaccharides)

Output: specific prebiotics with doses, FODMAP labeling, strategy description, and any condition-specific additions (e.g., pomegranate polyphenols for *Akkermansia* enrichment).

### LLM-Informed Conflict Resolution

When conflicts arise (e.g., polyphenol cap exceeded, mineral absorption competition with tied relevance scores), the LLM is asked to **break ties** — choosing which component to keep based on the client's primary goals. The LLM's decision is always validated by deterministic post-processing.

---

## 20. Post-Processing & Safety Gates

**Stage D** of the formulation pipeline applies a comprehensive series of safety filters and adjustments:

### 1. Exclusion Filters
- **Deterministic exclusions**: Remove any LLM-selected items that are handled by other pipeline stages (magnesium, omega, melatonin, prebiotic fibers) — prevents double-dosing
- **Vitamin inclusion gate**: Blocks unjustified B-vitamins (e.g., LLM adds B6 without a deficiency report or microbiome signal → removed because B6 toxicity risk at high doses)
- **Iron gate**: Males cannot receive iron unless explicitly deficient (KB rule: "men avoid")
- **Delivery format enforcement**: Fat-soluble vitamins (A, D, E) forced to softgel; water-soluble forced to sachet

### 2. Capsule-Only Substance Enforcement
Certain supplements (curcumin, berberine, quercetin, etc.) taste bitter/pungent and MUST NOT go in the sachet (drink). These are rerouted to capsule format per KB rules.

### 3. Polyphenol Management
- **Exclusion guards**: Quercetin auto-excluded for pregnancy, kidney disease, or anticoagulant medications
- **Piperine auto-addition**: Curcumin always gets Piperine at 1:100 ratio (absorption enhancer)
- **1000mg polyphenol cap**: Total polyphenol mass capped; excess resolved by LLM-informed dropping or deterministic trimming
- **Tier routing**: Tier 2 polyphenols (curcumin+piperine, bergamot) → dedicated morning capsule; Tier 1 → evening capsule; Sachet-safe (apple, pomegranate) → sachet

### 4. Interaction Safety
- **Herb-drug interactions**: Ashwagandha + thyroid medication (HIGH → auto-remove), Rhodiola + SSRIs (HIGH → auto-remove), Valerian + benzodiazepines (HIGH → auto-remove)
- **Mineral absorption conflicts**: Zinc vs Calcium, Zinc vs Iron, Calcium vs Iron — lower-relevance mineral removed based on goal scoring (or LLM tie-breaker)
- **Force-keep flag**: `--force-keep` CLI option overrides auto-removal for edge cases requiring human override

### 5. Health Claim Redundancy Check
Flags when multiple supplements target the same health claim — ensures complementary mechanisms rather than wasteful overlap.

---

## 21. Weight Calculation & Delivery Formats

**Module:** `platform_automation/formulation_automation/weight_calculator.py`

Every component is assigned to a physical delivery unit with strict capacity limits:

### Delivery Formats

| Unit | Format | Capacity | Timing | Contents |
|------|--------|----------|--------|----------|
| 1 | Probiotic Hard Capsule (Size 00) | 650mg | Morning | Probiotic strains (10mg per 1B CFU) |
| 2 | Omega Softgel (×1-2) | Fixed composition | Morning | Omega-3 + D3 + E + Astaxanthin |
| 3 | Daily Sachet | **19g max** | Morning | Prebiotics + water-soluble vitamins + supplements |
| 4 | Evening Capsule (Size 00) | 650mg | Evening | Ashwagandha, L-Theanine, Valerian, etc. |
| 5 | Polyphenol Capsule (Size 00) | 650mg | Morning | Curcumin+Piperine, Bergamot |
| 6 | Magnesium Capsule (Size 00, ×1-2) | 750mg each | Evening | Magnesium bisglycinate |

### Smart Sachet Overflow Resolution

The sachet is the most constrained unit (prebiotics alone can be 3-10g, leaving limited room for vitamins and supplements). When the sachet exceeds 19g, a 4-step smart resolution algorithm runs:

1. **Reduce doses** to knowledge base minimums
2. **Reroute** compatible supplements to evening capsule (checking timing restrictions)
3. **Drop redundant** supplements (same health claim, lower priority)
4. **Drop lowest-priority** supplements if still over capacity

### Evening Capsule Rebalancing

When evening components overflow a single 650mg capsule, the pipeline automatically splits into 2 evening capsules with balanced bin-packing and dose optimization (unused headroom used to increase therapeutic doses).

### Validation

The `FormulationCalculator` validates:
- No unit exceeds its capacity
- All probiotics fit within 650mg (max 65B CFU → 650mg at 10mg/B)
- Sachet ≤19g
- Each capsule ≤650mg
- All selected supplements are accounted for (supplement presence check)

---

## 22. Output Artifacts

Stage F assembles all outputs. For each sample, the formulation pipeline generates:

| File | Purpose | Consumer |
|------|---------|----------|
| `formulation_master_{sample}.json` | Complete formulation with all decisions, rationale, metadata | Internal reference |
| `formulation_platform_{sample}.json` | Platform-ready API payload | Client app/platform |
| `decision_trace_{sample}.json` | Board-readable decision audit trail | Board/investors |
| `manufacturing_recipe_{sample}.json` | Production specification (units, ingredients, weights) | Manufacturing |
| `manufacturing_recipe_{sample}.md` | Human-readable recipe (Markdown) | Production team |
| `manufacturing_recipe_{sample}.pdf` | Printable recipe (PDF) | Production floor |
| `component_rationale_{sample}.json` | "How this addresses your health" table | Client communication |
| Dashboard HTML files | Interactive visual dashboards | Client + board |
| `pipeline_log_{sample}.txt` | Audit trail of all pipeline decisions | QA/debugging |

### Component Registry

A **component registry** is built as a single source of truth — listing every substance in the final formula with:
- Substance name and dose
- Delivery unit (capsule, sachet, softgel, etc.)
- Category (probiotic, prebiotic, vitamin, supplement, etc.)
- **Source**: `microbiome_primary`, `microbiome_linked`, or `questionnaire_only`
- Health claims addressed
- What it targets and what informed the decision

This registry is consumed by the rationale table, dashboards, and source attribution percentage calculation.

---

# PART V — OPERATIONS & REFERENCE

---

## 23. Running the Pipeline — Command Reference

### Full Pipeline (End-to-End)

```bash
# Process all samples in a batch (download + GMWI2 + metrics + reports + formulation)
bash platform_automation/pipeline_scripts/run_sample_analysis.sh --batch nb1_2026_001

# Process a single sample
bash platform_automation/pipeline_scripts/run_sample_analysis.sh \
  --batch nb1_2026_001 --sample 1421263814738

# Preview without executing
bash platform_automation/pipeline_scripts/run_sample_analysis.sh \
  --batch nb1_2026_001 --dry-run
```

### Individual Stages

```bash
# Stage 2 only: JSON report (with LLM narratives)
cd platform_automation/report_automation
python3 generate_report.py --sample-dir /path/to/analysis/{batch}/{sample}/

# Stage 2: Without LLM (fast, deterministic fields only)
python3 generate_report.py --sample-dir /path/to/sample/ --no-llm

# Stage 3: Narrative report
python3 generate_narrative_report.py --sample-dir /path/to/analysis/{batch}/{sample}/

# Stage 4: Formulation
cd platform_automation/formulation_automation
python3 generate_formulation.py --sample-dir /path/to/analysis/{batch}/{sample}/

# Stage 4: Formulation without LLM (testing/offline mode)
python3 generate_formulation.py --sample-dir /path/to/sample/ --no-llm
```

### Batch Processing

```bash
# Stages 2-4 all support batch mode:
python3 generate_report.py --batch-dir /path/to/analysis/{batch}/
python3 generate_narrative_report.py --batch-dir /path/to/analysis/{batch}/
python3 generate_formulation.py --batch-dir /path/to/analysis/{batch}/
```

### Recalculation After Algorithm Updates

```bash
# Recalculate Stage 1 metrics only (no S3 download, no GMWI2)
bash platform_automation/pipeline_scripts/run_sample_analysis.sh \
  --batch nb1_2026_001 --metrics-only --force

# Then regenerate reports
python3 generate_report.py --batch-dir /path/to/analysis/{batch}/
```

### Useful Flags

| Flag | Purpose |
|------|---------|
| `--batch BATCH_ID` | Process specific batch |
| `--sample SAMPLE_ID` | Process single sample (requires --batch) |
| `--metrics-only` | Skip S3 download and GMWI2; recalculate metrics only |
| `--qc-only` | Run only QC precheck on existing local data |
| `--force` | Force reprocessing even if already complete |
| `--dry-run` | Preview without executing |
| `--no-llm` | Skip all LLM calls (faster, deterministic only) |
| `--force-keep` | Don't auto-remove supplements with high-severity interactions |

---

## 24. Directory Structure & File Map

### Top-Level Structure

```
/Users/pnovikova/Documents/work/
├── platform_automation/         ← ALL pipeline code lives here
│   ├── bioinformatics_scripts/  ← Stage 1 scripts + knowledge base + GMWI2 model
│   ├── report_automation/       ← Stage 2 + 3 scripts + knowledge base
│   ├── formulation_automation/  ← Stage 4 scripts + knowledge base
│   ├── pipeline_scripts/        ← Main orchestrator shell script
│   ├── shared/                  ← Shared modules (guild_priority.py, formatting.py)
│   ├── documentation/           ← This wiki + technical docs
│   └── testing/                 ← Test scripts + results
├── analysis/                    ← ALL sample outputs organized by batch
│   ├── nb1_2026_001/           ← Batch 1
│   │   ├── 1421263814738/      ← Sample (13-digit ID)
│   │   │   ├── GMWI2/         ← GMWI2 results
│   │   │   ├── bioinformatics/ ← Metrics outputs
│   │   │   ├── questionnaire/  ← Client questionnaire JSON
│   │   │   ├── reports/        ← All report outputs
│   │   │   │   ├── reports_json/  ← JSON files (master, platform, formulation)
│   │   │   │   ├── reports_md/    ← Markdown files (narrative, recipe)
│   │   │   │   ├── reports_pdf/   ← PDF files
│   │   │   │   └── reports_html/  ← HTML dashboards
│   │   │   └── logs/           ← Pipeline logs + status
│   │   └── ... more samples
│   └── knowledge_base/          ← Shared knowledge base files
├── data/                        ← Downloaded S3 data (per batch)
│   └── nb1_2026_001/
│       ├── functional_profiling/
│       ├── raw_sequences/        ← Deleted after processing
│       └── taxonomic_profiling/
├── documents/                   ← Reference documents, questionnaire templates
├── research_tasks/              ← Literature review artifacts
├── report_interpretation/       ← Framework documents (algorithm specs, templates)
├── rnd/                         ← Research & development artifacts
├── scripts/                     ← Legacy scripts (pre-automation)
└── work_tracking/               ← Sample tracking spreadsheets
```

### Per-Sample Output Structure (Complete)

```
analysis/{batch}/{sample}/
├── GMWI2/
│   ├── {sample}_run_GMWI2.txt
│   ├── {sample}_run_GMWI2_taxa.txt
│   └── {sample}_run_metaphlan.txt
├── bioinformatics/
│   └── only_metrics/
│       ├── {sample}_only_metrics.txt
│       ├── {sample}_functional_guild.txt
│       └── {sample}_functional_guild.csv
├── questionnaire/
│   └── questionnaire_{sample}.json
├── qc/
│   ├── {sample}_qc_precheck.json
│   └── {sample}_qc_precheck.md
├── reports/
│   ├── reports_json/
│   │   ├── microbiome_analysis_master_{sample}.json     ← Stage 2 master
│   │   ├── microbiome_platform_{sample}.json            ← Stage 2 platform
│   │   ├── formulation_master_{sample}.json             ← Stage 4 master
│   │   ├── formulation_platform_{sample}.json           ← Stage 4 platform
│   │   ├── decision_trace_{sample}.json                 ← Audit trail
│   │   ├── manufacturing_recipe_{sample}.json           ← Production spec
│   │   └── component_rationale_{sample}.json            ← Health table
│   ├── reports_md/
│   │   ├── narrative_report_{sample}.md                 ← Stage 3
│   │   └── manufacturing_recipe_{sample}.md             ← Readable recipe
│   ├── reports_pdf/
│   │   ├── narrative_report_{sample}.pdf
│   │   └── manufacturing_recipe_{sample}.pdf
│   └── reports_html/
│       ├── microbiome_health_report_{sample}.html       ← Stage 2 dashboard
│       ├── formulation_decision_trace_{sample}.html     ← Board dashboard
│       └── formulation_client_dashboard_{sample}.html   ← Client dashboard
├── logs/
│   ├── {sample}_pipeline.status
│   └── pipeline_log_{sample}.txt
└── plots/
    └── {sample}_integrated_analysis.pdf
```

---

## 25. Knowledge Base Reference

### Bioinformatics Knowledge Base (`bioinformatics_scripts/knowledge_base/`)

| File | Content |
|------|---------|
| `GMWI2_taxa_coefficients.tsv` | 155 signature taxa with logistic regression coefficients |
| `core_pathways_keywords.tsv` | MetaCyc pathway keywords for functional classification |

### Report Knowledge Base (`report_automation/knowledge_base/`)

| File | Content |
|------|---------|
| `guild_interpretation.json` | Guild definitions, CLR ratio interpretations, 9-scenario matrix, trophic cascade rules |
| `interpretation_rules.json` | Compositional metric thresholds, pattern classification logic |
| `static_content.json` | Universal text for all 5 platform tabs |
| `concise_report_framework.md` | Optimized LLM prompt/framework for narrative report generation |
| `dietary_inference.json` | Dietary pattern inference rules from CLR ratios |
| `vitamin_signals.json` | Vitamin signal definitions and risk thresholds |
| `dysbiosis_rules.json` | Dysbiosis marker thresholds and interpretations |
| `metabolic_functions.json` | Metabolic dial descriptions and context |
| `functional_pathways.json` | Pathway classification and interpretation |
| `population_thresholds.json` | Population-level reference data (dynamically updated) |
| `quality_and_accuracy.json` | Quality assessment rules |

### Formulation Knowledge Base (`formulation_automation/knowledge_base/`)

| File | Content |
|------|---------|
| `synbiotic_mixes.json` | All 8 probiotic mixes with strains, CFU, triggers, ecological rationale |
| `bacterial_strains.json` | Individual strain data (species, function, evidence) |
| `prebiotic_rules.json` | Prebiotic dosing by CFU tier + sensitivity |
| `clr_decision_rules.json` | CLR-based decision thresholds for mix selection |
| `vitamins_minerals.json` | Complete vitamin/mineral catalog with doses, delivery, interactions |
| `supplements_nonvitamins.json` | Non-vitamin supplement catalog (botanicals, amino acids, etc.) |
| `therapeutic_doses.json` | Therapeutic vs standard dose table for deficiency cases |
| `sensitivity_thresholds.json` | Sensitivity classification rules and prebiotic clamps |
| `goal_to_health_claim.json` | Mapping from questionnaire goals to EU health claim categories |
| `delivery_format_rules.json` | Capsule-only substances, polyphenol tier classification, capacity rules |
| `timing_rules.json` | Universal timing rules (morning/evening assignment logic) |

---

## 26. AWS Configuration

| Resource | Value |
|----------|-------|
| **S3 Bucket** | `s3://nb1-prebiomics-sample-data/incoming` |
| **Bedrock Model (Reports)** | `eu.anthropic.claude-sonnet-4-20250514-v1:0` |
| **Bedrock Model (Narratives)** | `eu.anthropic.claude-opus-4-6-v1` |
| **Bedrock Region** | `eu-west-1` |
| **Credentials** | `~/.aws/credentials` (standard AWS CLI configuration) |

### S3 Bucket Structure

```
s3://nb1-prebiomics-sample-data/incoming/
├── nb1_2026_001/
│   ├── functional_profiling/{sample_id}/
│   ├── raw_sequences/{sample_id}/
│   └── taxonomic_profiling/{sample_id}/
├── nb1_2026_002/
│   └── ...
└── ...
```

Batch IDs follow the pattern `nb1_2026_XXX` where XXX is a sequential batch number. Sample IDs are 13-digit numeric identifiers.

---

## 27. Quality Assurance

### QC Precheck (Stage 1)

The `qc_precheck.py` script validates data quality before analysis:
- Checks file completeness (are all expected files present?)
- Validates file sizes (not empty or truncated?)
- Computes a **Functional Evidence Score** (0-100)
- Assigns confidence tiers (HIGH/MODERATE/LOW)
- Currently **non-blocking** — low QC flags a warning but doesn't stop the pipeline

### Consistency Checks (Formulation)

`consistency_check.py` validates cross-sample formulation consistency within a batch:
- Are similar microbiome profiles getting similar mixes?
- Are there unexpected outliers?

### Sanity Check vs Knowledge Base

`sanity_check_vs_kb.py` validates that formulation outputs match knowledge base rules:
- Are all probiotic strains from the correct mix?
- Are prebiotic doses within allowed ranges?
- Are vitamin doses within standard/therapeutic limits?

### Supplement Presence Check

Built into the formulation pipeline — every LLM-selected supplement must appear in at least one delivery unit. Lost supplements are flagged as `🚨🚨🚨 LOST SUPPLEMENT` errors.

### Population Thresholds Watcher

`update_population_thresholds.py` + `START_THRESHOLDS_WATCHER.sh` — monitors incoming sample data to update population-level reference distributions as our sample count grows.

---

# PART VI — ROADMAP & FUTURE DEVELOPMENT

This part describes what's not yet built and where the project is heading. It distinguishes between near-term engineering work and long-term R&D.

---

## 28. The 16-Week Follow-Up (R&D — Not Yet Implemented)

Currently, each client goes through a single cycle:

```
Sample → Analysis → Formula → 16 weeks of supplementation → ???
```

The **follow-up protocol** is the most important missing piece:

### What It Would Look Like
1. Client submits a **second stool sample** after completing their 16-week protocol
2. Second sample goes through the same bioinformatics pipeline
3. **Before/after comparison**: Which guilds changed? Did the targeted guilds respond?
4. **Adaptive formulation v2**: Adjust the mix based on what worked and what didn't
5. **Response classification**: Fast (significant guild recovery) / Typical (partial) / Slow (minimal change)

### Open Questions
- What constitutes "success"? (Guild reaches healthy range? Score improves by X points?)
- How to handle non-responders? (Different mix? Higher doses? Longer protocol?)
- How to track compliance? (Did the client actually take the supplements consistently?)
- What's the optimal re-test interval? (8 weeks? 12 weeks? Full 16 weeks?)

### What's Already Conceptualized
The original PIPELINE_FLOW.md describes a Phase 1/2/3 system with dose escalation (conservative → moderate → full) and a responder classification framework. This hasn't been implemented in the automation but could inform the follow-up design.

---

## 29. Scientific Improvements

### Guild Model Refinement
Currently, the 6 guild healthy ranges (30-50%, 2-10%, 6-12%, 10-25%, 5-10%, 1-5%) are derived from literature. As our sample count grows, we can refine these ranges based on our own population data. The `population_thresholds.json` watcher already exists — it just needs enough data to be statistically meaningful.

### Strain-Level Resolution
Currently we use species-level MetaPhlAn profiling. **Strain-level** resolution would allow us to:
- Track whether our supplemented probiotic strains actually **engraft** (establish in the gut)
- Distinguish beneficial from pathogenic strains within the same species
- Personalize strain selection more precisely

### Metabolomics Integration
Currently we **predict** metabolic function from DNA (metagenomics). Adding actual **SCFA measurement** (stool calprotectin, breath tests, or direct SCFA quantification) would validate our predictions. "We see the genes for butyrate production — but are they actually producing butyrate?"

### Food Intolerance Module
A knowledge base file (`Food_intolerances_Microbial_Functional_alterations.numbers`) already exists documenting microbial signatures associated with food intolerances. This hasn't been integrated into the pipeline but could power a food sensitivity assessment.

### Dose-Response Modeling
Currently all mixes use a fixed 50B CFU dose. With enough before/after data, we could model optimal CFU based on baseline microbiome state — perhaps severely depleted guilds need higher initial doses.

### Evidence Base Expansion
The WGO (World Gastroenterology Organisation) RCT database (`WGO_RCTs_adults.xlsx`) documents all evidence-based probiotic-condition pairs. This could be systematically mined for new strain-condition associations to expand our mix library.

### Female Health Module
Currently the pipeline treats all adults uniformly. Developing **female-specific intervention strategies** is a priority area:
- Hormonal cycle considerations (certain probiotics may be more effective at different cycle phases)
- Menopause support (microbiome changes significantly during perimenopause)
- Fertility-related microbiome patterns
- Vaginal-gut microbiome axis interactions
- Pregnancy-safe formulation adjustments

### Deeper Questionnaire Utilization
Currently the questionnaire is primarily scanned for age, goals, contraindications, sleep, stress, and digestive symptoms. But the **nutrition/diet section** contains rich data that's underutilized:
- Fiber intake levels could directly inform prebiotic dosing strategy
- Protein source patterns could predict proteolytic guild status
- Meal timing could inform supplement timing
- Specific food preferences could guide prebiotic selection (e.g., if someone hates the taste of PHGG, use alternative fibers)

---

## 30. Technical / Engineering Improvements

### Full CI/CD Pipeline (Work in Progress)
Currently the pipeline runs locally on macOS. Containerization (Docker) and cloud deployment are being developed to enable:
- Reproducible environments
- Automated processing triggered by new S3 uploads
- Parallel batch processing
- Version-controlled algorithm deployments

### Platform API Integration
The `_platform.json` output is structured and ready for API consumption. The actual connection to the client-facing app/platform is pending.

### Nutritionist Review Platform (Planned)
A platform where the formulation JSON will be **directed for nutritionist review** — the nutritionist can add comments, approve or modify the formula, and their feedback is automatically processed back into the pipeline. This closes the human-in-the-loop before manufacturing.

### Automated Questionnaire Ingestion
Currently questionnaires are manually placed in sample directories. In the future, questionnaires will flow directly from the client app into the analysis directory, triggering formulation generation automatically.

### Batch Processing Dashboard
Currently batch results are logged to text files (`batch_summary.log`). A web dashboard showing all samples, their statuses, QC scores, scores, and formulation validation results would greatly improve operational visibility.

### LLM Cost Optimization
- Narrative reports use Claude Opus (~$15-20 per sample) — some sections could use the cheaper Claude Sonnet model
- Common narrative patterns could be cached and reused
- Deterministic text generation could replace LLM calls for formulaic sections

### Production Integration
The bridge from `manufacturing_recipe_{sample}.json` to an actual production order is currently manual. Automating this handoff would complete the pipeline end-to-end.

---

## 31. Bioinformatics Improvements

### QC Pipeline Hardening
The QC precheck exists but is currently **non-blocking** — even samples with LOW confidence proceed through the pipeline. Areas to improve:
- Define hard QC gates (what sample quality is truly too low to analyze?)
- Add additional quality metrics (read depth, contamination screening, extraction quality indicators)
- Implement automatic flagging for samples that need re-extraction or re-sequencing

### HUMAnN v4 Full Adoption
The pipeline auto-detects HUMAnN v3 vs v4, but pathway classification could be further refined for v4's updated databases and improved resolution.

### Resistome Profiling
Adding antibiotic resistance gene detection (using AMRFinderPlus or CARD database) could:
- Identify resistance patterns that affect probiotic colonization
- Guide strain selection away from resistant environments
- Add a safety dimension to the report

### Virome Analysis
Bacteriophage profiling could inform guild dynamics — phages that specifically target members of depleted guilds might be contributing to the depletion. This would add a causal dimension to our ecological assessment.

### Custom Reference Database
Currently using standard MetaPhlAn/HUMAnN databases. Building a custom marker database enriched for our 6 guilds' key species would improve sensitivity for the organisms we care most about.

### Population-Level Analytics
With growing sample counts across batches (currently 9+ batches), we can build:
- Population-level reference distributions for all our metrics
- Demographic-stratified reference ranges (age, sex, diet pattern)
- Batch effect monitoring (ensuring different sequencing runs are comparable)

---

## 32. Long-Term Data Science Vision

### Microbiome-Lifestyle Correlation Analysis
Currently, microbiome data and questionnaire data are used **independently** — microbiome drives mix selection, questionnaire drives supplement selection. The long-term vision is to **correlate** these datasets:
- How do microbiome profiles correlate with age, sex, activity level, diet, and lifestyle patterns?
- Are there microbiome "archetypes" that cluster with specific lifestyle patterns?
- Can we predict certain guild patterns from questionnaire data alone (as a preliminary assessment before sequencing)?

### Responder Group Identification
With enough before/after data (follow-up samples), we can:
- Cluster clients by microbiome pattern + questionnaire profile
- Identify groups that respond similarly to specific interventions
- Develop "responder profiles" — "clients with Pattern X + Lifestyle Y typically show Z improvement with Mix N"
- Move from individual optimization to population-informed personalization

### Predictive Modeling
Use accumulated data to build models that predict:
- Which mix will work best for a given microbiome pattern
- Expected timeline for guild recovery
- Which clients are likely to be fast vs slow responders
- Optimal prebiotic doses based on baseline guild composition

### Dietary Inference from Microbiome
Already partially implemented (CLR ratios suggest dietary patterns — e.g., negative CUR suggests high protein intake). Could be expanded into a full dietary assessment module:
- "Your microbiome suggests a diet high in protein and low in fiber"
- "Your *Prevotella*/*Bacteroides* ratio suggests a plant-based vs Western dietary pattern"
- Dietary recommendations that complement the supplement formula

---

## 33. Current Status — A Strong MVP

### What's Production-Ready Today

✅ **Full bioinformatics pipeline** — S3 download → GMWI2 → comprehensive metrics calculation, fully automated  
✅ **Structured JSON report generation** — 5-pillar scoring, guild analysis, dual-format interpretations, platform-ready JSON  
✅ **Narrative report generation** — 5,000-7,000 word scientific reports via LLM, with PDF output  
✅ **Complete formulation pipeline** — From microbiome analysis + questionnaire to manufacturing recipe  
✅ **8 probiotic mixes** with evidence-based strain selection and ecological rationale  
✅ **Comprehensive safety system** — drug interactions, mineral conflicts, polyphenol caps, sensitivity gating  
✅ **Multiple delivery formats** — capsules, softgels, sachets with smart capacity management  
✅ **Dashboard generation** — Interactive HTML dashboards for clients and board  
✅ **Batch processing** — Process entire batches with single commands  
✅ **Audit trail** — Decision traces, pipeline logs, component rationale for every sample  

### What's Comprehensive About It

This pipeline covers the entire journey from raw DNA sequences to a physical product specification. It considers:
- **Ecological context** (not just "is this bacterium present" but "how is it competing with other guilds")
- **Metabolic relationships** (trophic cascades, cross-feeding, substrate competition)
- **Clinical safety** (drug interactions, mineral absorption conflicts, pregnancy exclusions)
- **Physical constraints** (capsule capacity, sachet weight, taste/delivery format)
- **Client context** (goals, symptoms, sleep, stress, exercise, diet)
- **Production feasibility** (manufacturing recipes with exact weights and unit specifications)

### Where the Biggest Value-Adds Are

1. **Follow-up protocol** — Closes the feedback loop, proves the product works
2. **Female health module** — Expands addressable market and intervention depth
3. **Deeper questionnaire utilization** — More data, better personalization
4. **Population-level analytics** — Moves from literature-based ranges to data-driven ranges
5. **Responder modeling** — Predictive personalization based on accumulated outcomes

---

# Appendix A: Version Information

| Component | Current Version | Notes |
|-----------|----------------|-------|
| Algorithm Framework | v1.7 | CLR-Primary + Signal-Based Vitamins |
| Guild Analysis | v3.0 | FFA weighting, 9-scenario matrix, trophic cascades |
| Pipeline Automation | v1.0 | Platform automation codebase |
| GMWI2 Model | sklearn LogisticRegression | 155 signature taxa, 8,069 training metagenomes |
| Probiotic Mixes | 8 mixes | 4-8 strains each, 50B CFU standard |
| Priority System | Score-based | Importance × State × Evenness modifier |
| Supplement Framework | KB-driven | 13 knowledge base JSON files |

---

# Appendix B: Key Literature

- Castellarin et al. (2012). *F. nucleatum* infection in colorectal carcinoma. *Genome Research*, 22(2), 299-306.
- Cockburn & Koropatkin (2016). Polysaccharide degradation by the intestinal microbiota. *Trends in Microbiology*, 24(12), 988-1000.
- Derrien et al. (2017). *Akkermansia muciniphila* and its role in regulating host functions. *Microbial Pathogenesis*, 106, 171-181.
- Faust & Raes (2012). Microbial interactions: from networks to models. *Nature Reviews Microbiology*, 10(8), 538-550.
- Flint et al. (2012). Microbial degradation of complex carbohydrates in the gut. *Gut Microbes*, 3(4), 289-306.
- Gloor et al. (2017). Microbiome datasets are compositional. *Frontiers in Microbiology*, 8, 2224.
- Hou et al. (2025). Causal Links Between Gut Microbiota and Vitamin Deficiencies: Evidence from Mendelian Randomization Analysis.
- Louis & Flint (2009). Diversity of butyrate-producing bacteria from the human large intestine. *FEMS Microbiology Letters*, 294(1), 1-8.
- Louis & Flint (2017). Formation of propionate and butyrate by the human colonic microbiota. *Environmental Microbiology*, 19(1), 29-41.
- McCann (2000). The diversity–stability debate. *Nature*, 405(6783), 228-233.
- O'Callaghan & van Sinderen (2016). Bifidobacteria and their role as members of the human gut microbiota. *FEMS Microbiology Reviews*, 40(3), 340-376.
- Vital et al. (2014). Revealing the bacterial butyrate synthesis pathways. *mBio*, 5(2), e00889-14.
- Windey et al. (2012). Relevance of protein fermentation to gut health. *Molecular Nutrition & Food Research*, 56(1), 184-196.

---

*This wiki is a living document. As the pipeline evolves, sections should be updated to reflect the current state of the system.*

**END OF PROJECT WIKI**
