# Scientific Rationale — Microbiome Analysis Algorithm

**Purpose:** Documents the biological and ecological reasoning behind every algorithmic decision in the pipeline. This is the authoritative "why" document for the scoring, interpretation, and recommendation systems.

---

## 1. Guild Model — Why 6 Functional Guilds

### Rationale
Rather than analyzing 300+ individual species, we group bacteria into 6 functional guilds based on their metabolic role in the fermentation cascade. This approach is grounded in ecological guild theory (Faust & Raes, 2012) — organisms that use the same resources in the same way form functional units regardless of taxonomy.

### The 6 Guilds

| Guild | Scientific Name | Client Name | Ecological Role | Key References |
|-------|----------------|-------------|-----------------|----------------|
| **1. Fiber Degraders** | Fiber Degraders | Fiber-Processing Bacteria | Primary polysaccharide processors — ecosystem gatekeepers that determine whether the system is diet-fed or host-fed | Flint et al., 2012; Cockburn & Koropatkin, 2016 |
| **2. Bifidobacteria** | HMO/Oligosaccharide-Utilising Bifidobacteria | Bifidobacteria | Early fermentation amplifiers — convert oligosaccharides to lactate/acetate via phosphoketolase pathway. Rapid acidification. | O'Callaghan & van Sinderen, 2016 |
| **3. Intermediate Processors** | Cross-Feeders | Intermediate Processors | Secondary fermentation stabilizers — CRITICAL CONNECTORS between degradation and terminal SCFA production. Consume lactate, acetate, succinate, H₂ | Louis & Flint, 2017 |
| **4. Gut-Lining Energy Producers** | Butyrate Producers | Gut-Lining Energy Producers | Terminal SCFA producers — downstream-dependent, require acetate supply. Feed colonocytes. | Vital et al., 2014; Louis & Flint, 2009 |
| **5. Mucus-Layer Bacteria** | Mucin Degraders | Mucus-Layer Bacteria | Host-substrate users — adaptive buffer during fiber fluctuation, pathological when chronic | Derrien et al., 2017 |
| **6. Protein-Fermenting Bacteria** | Proteolytic Dysbiosis Guild | Protein-Fermenting Bacteria | Putrefactive strategy — produce ammonia, H₂S, phenols, indoles, BCFAs. Expansion indicates substrate competition shifted toward protein | Windey et al., 2012 |

### Trophic Cascade Model
The guilds operate in a **parallel processing model** (not purely sequential):
```
Fiber → Guild 1 (Fiber Degraders) → [Parallel: Guild 2 (Bifido) + Guild 3 (Intermediate Processors)] → Guild 4 (Butyrate Producers) → SCFA Pool
Backup: Guild 5 (Mucin) → always-on, increases when fiber decreases
Competing: Guild 6 (Proteolytic) → protein-driven alternative, produces inflammatory metabolites
```

**Key principle:** Healthy gut = carbohydrate-driven ecosystem. Dysbiosis = protein and mucin-driven ecosystem.

---

## 2. CLR Ratios — Why 4 Metabolic Dials

### Rationale
Centered Log-Ratio (CLR) transformation is the standard for compositional microbiome data (Gloor et al., 2017). We compute 4 diagnostic ratios from guild-level CLR values that capture fundamental metabolic trade-offs:

| Ratio | Formula | Measures | Biological Basis |
|-------|---------|----------|------------------|
| **CUR** (Carbohydrate Utilization) | [(Fiber_CLR + Bifido_CLR)/2] − Proteo_CLR | Substrate competition: carbs vs protein | Positive = carb guilds winning (favorable). Reflects whether dietary fiber or protein dominates as bacterial fuel. |
| **FCR** (Fermentation Completion) | [(Butyrate_CLR + Cross_CLR)/2] − Bifido_CLR | Terminal processing efficiency | Positive = efficient conversion of intermediates to SCFA. Negative = metabolite accumulation, broken cross-feeding networks. |
| **MDR** (Mucus Dependency) | Mucin_CLR − Fiber_CLR | Host vs dietary substrate reliance | Positive = bacteria consuming host mucus. Negative = diet-fed (healthy). Reflects fiber availability reaching the colon. |
| **PPR** (Putrefaction Pressure) | Proteo_CLR − Butyrate_CLR | Harsh vs gentle metabolite production | Positive = protein fermentation dominant (inflammatory). Negative = SCFA dominant (anti-inflammatory). |

### Threshold Rationale
| Ratio | Favorable | Neutral | Unfavorable |
|-------|-----------|---------|-------------|
| CUR | > +0.3 | ±0.3 | < −0.3 |
| FCR | > +0.3 | ±0.3 | < −0.3 |
| MDR | < −0.2 | ±0.2 | > +0.2 |
| PPR | < −0.2 | ±0.2 | > +0.2 |

Thresholds derived from observed distributions in healthy cohorts (internal reference data, n=50+). The ±0.3 and ±0.2 boundaries separate clinically meaningful metabolic states from noise.

---

## 3. Scoring Weights — Why 5 Pillars

### Pillar Architecture

| Pillar | Max Points | Weight | Rationale |
|--------|-----------|--------|-----------|
| Health Association | 20 | 20% | GMWI2 model (155 taxa, 8,069 metagenomes, 26 countries). Broadest validated health predictor. |
| Diversity & Resilience | 20 | 20% | Shannon + guild evenness. Species richness predicts ecosystem stability (McCann, 2000). |
| Metabolic Function | 20 | 20% | 4 CLR ratios. Direct measure of fermentation state. |
| Guild Balance | 30 | 30% | Largest weight because it captures the ACTIONABLE imbalances — specific teams that need intervention. |
| Safety Profile | 10 | 10% | Binary/threshold — dysbiosis markers either present or absent. Important but not differentiating for most samples. |

**Guild Balance gets 30% because** it's the most intervention-relevant pillar. A high score here means all teams are properly staffed; a low score directly maps to specific action plan steps. The other pillars describe the CONTEXT; guild balance describes the PROBLEM.

---

## 4. Recovery Estimation — Weighted Guild Resilience

### Rationale
Recovery potential depends on two factors:
1. **Species diversity** (Shannon) — more species = more pathways to recovery
2. **Beneficial guild resilience** (weighted evenness) — diverse teams recover faster than monocultures

### Guild Weights for Recovery

| Guild | Weight | Biological Justification |
|-------|--------|-------------------------|
| **Fiber Degraders** | **2.0** | Upstream gateway — rate-limiting primary producers. Diverse fiber degraders (multiple Bacteroides, Prevotella, Roseburia species) can each respond to different prebiotic substrates simultaneously, enabling parallel recovery pathways. Monoculture risk (low J) means recovery depends on a single species responding to intervention. (Flint et al., 2012; Cockburn & Koropatkin, 2016) |
| **Bifidobacteria** | **1.5** | Specialist amplifiers with rapid growth kinetics. When multiple species present (B. longum, B. adolescentis, B. bifidum), they respond to different prebiotic substrates (FOS, GOS, HMO-like). Rapid initial response (days). When ABSENT, cannot self-recover — requires external reintroduction. (O'Callaghan & van Sinderen, 2016) |
| **Intermediate Processors** | **1.5** | Syntrophic diversity directly predicts network recovery. Multiple species process lactate → propionate, acetate → butyrate through different pathways. High J = no single bottleneck species. They respond passively to upstream substrate flow improvement, so their diversity predicts HOW FAST recovery propagates downstream. (Louis & Flint, 2017) |
| **Butyrate Producers** | **1.0** | Terminal output, lowest weight because downstream-dependent. Different species use different pathways (acetyl-CoA, butyryl-CoA:acetate CoA-transferase, direct resistant starch). Diversity matters but recovery is largely passive — they expand once upstream guilds recover. (Vital et al., 2014) |
| **Proteolytic Guild** | **Excluded** | Not a recovery predictor — describes the PROBLEM's stability, not the SOLUTION's capacity. High proteolytic J = stable overgrowth (harder to displace), but this is already captured by the guild balance score. |
| **Mucin Degraders** | **Transition check only** | High mucin J with elevated abundance = smooth rebalancing. Low mucin J with high abundance = transition risk (monoculture crash possible). Checked separately as a safety modifier, not a recovery driver. |

### Classification Thresholds

| Level | Shannon | Resilience Score (max 6.0) | Timeline |
|-------|---------|---------------------------|----------|
| High | ≥ 3.0 | ≥ 4.0 | 8-12 weeks |
| Good | ≥ 2.5 | ≥ 2.5 | 12-16 weeks |
| Moderate | ≥ 2.0 | ≥ 1.5 | 16-24 weeks |
| Challenging | < 2.0 | any | 24+ weeks |

### Transition Safety
When mucin degraders are elevated (>8%) with low diversity (J<0.40), the rebalancing process may involve volatile population shifts. A transition caution note is added.

---

## 5. Vitamin Signal Logic

### B12 — Inverse Signal
Unlike other B vitamins, B12 microbiome signals are INVERSE: elevated Akkermansia (>8%) is paradoxically associated with B12 deficiency risk. This is based on Mendelian Randomization evidence (Hou et al., 2025, FDR<0.05). Additional nominal genera (Coprococcus, Enterorhabdus, Lactococcus) provide weaker supporting evidence (P<0.05, not FDR-corrected).

### Folate — Diversity-Dependent
Folate production capacity depends on: Shannon diversity (>2.0), Bacteroides presence (>5%), and Bifidobacterium presence (>2%). Risk score = count of failed thresholds (0-3).

### Biotin — Producer Count
Based on detection of 4 key biotin-producing indicator species: B. fragilis, P. copri, F. varium, C. coli. Risk = 3 − producers detected.

### B-Complex — Composition-Based
Based on: Bacteroides >10% (protective), F:B ratio <1.85, Lachnospiraceae + Ruminococcaceae >2%. Risk = count of failed criteria (0-3).

---

## 6. Safety Profile Thresholds

### Dysbiosis Markers
| Marker | Normal | Mild Concern | Significant | Rationale |
|--------|--------|-------------|-------------|-----------|
| *F. nucleatum* | < 0.1% | 0.1-0.5% | > 0.5% | CRC-associated (Castellarin et al., 2012) |
| *S. gallolyticus* | < 0.1% | 0.1-0.5% | > 0.5% | CRC-associated (Boleij et al., 2011) |
| *P. anaerobius* | < 0.1% | 0.1-0.5% | > 0.5% | CRC-associated (Tsoi et al., 2017) |
| *Escherichia-Shigella* | < 3.0% | 3-5% | > 5% | Normal commensal up to 3%, opportunistic above |

### Extreme Overgrowth
| Condition | Threshold | Rationale |
|-----------|-----------|-----------|
| Mucin Degraders | > 10% (2.5× above 4% max) | Indicates chronic barrier erosion beyond adaptive compensation |
| Proteolytic Guild | > 8% (1.6× above 5% max) | Indicates sustained pro-inflammatory metabolite production |
| M. smithii | > 10% | Methane-producing archaea overgrowth, associated with constipation (Pimentel et al., 2012) |
| BCFA pathways | ≥ 2 detected | Direct evidence of protein putrefaction activity |

---

## 7. Priority Classification

| Level | Beneficial Guilds | Contextual Guilds | Action |
|-------|------------------|------------------|--------|
| **CRITICAL** | Absent (0%) or < 50% of minimum | > 3× above maximum | Immediate intervention |
| **1A** | Below range + CLR < -1.0 (losing competition) | > 2× above maximum | High-priority intervention |
| **1B** | Below range (other) or CLR < -0.5 within range | > maximum | Moderate intervention |
| **Monitor** | Within range, stable CLR | Within range | Maintain |

---

## 8. Terminology Decisions

| Scientific | Client-Facing | Rationale |
|-----------|--------------|-----------|
| Cross-Feeders | Intermediate Processors | "Cross-feeders" is confusing for non-experts — sounds like they feed on each other. "Intermediate processors" accurately describes their role in the assembly line metaphor. |
| Butyrate Producers | Gut-Lining Energy Producers | Clients don't know what butyrate is. The functional description makes their importance immediately clear. |
| Mucin Degraders | Mucus-Layer Bacteria | "Mucin degraders" sounds destructive. "Mucus-layer bacteria" is neutral and accurate. |
| Proteolytic Dysbiosis Guild | Protein-Fermenting Bacteria | "Proteolytic dysbiosis" is jargon. "Protein-fermenting bacteria" describes what they do in words anyone understands. |
| Fiber Degraders | Fiber-Processing Bacteria | "Degraders" has negative connotation. "Processing" is positive and accurate. |
| Bifidobacteria | Bifidobacteria | Kept scientific name — it's well-known from probiotic marketing and builds trust. |

---

## Key Literature References

- Castellarin et al. (2012). *Fusobacterium nucleatum* infection is prevalent in human colorectal carcinoma. *Genome Research*, 22(2), 299-306.
- Cockburn & Koropatkin (2016). Polysaccharide degradation by the intestinal microbiota. *Trends in Microbiology*, 24(12), 988-1000.
- Derrien et al. (2017). *Akkermansia muciniphila* and its role in regulating host functions. *Microbial Pathogenesis*, 106, 171-181.
- Faust & Raes (2012). Microbial interactions: from networks to models. *Nature Reviews Microbiology*, 10(8), 538-550.
- Flint et al. (2012). Microbial degradation of complex carbohydrates in the gut. *Gut Microbes*, 3(4), 289-306.
- Gloor et al. (2017). Microbiome datasets are compositional: and this is not optional. *Frontiers in Microbiology*, 8, 2224.
- Hou et al. (2025). Mendelian randomization of gut microbiome and B-vitamin status. [Reference for B12 inverse signal]
- Louis & Flint (2009). Diversity, metabolism and microbial ecology of butyrate-producing bacteria from the human large intestine. *FEMS Microbiology Letters*, 294(1), 1-8.
- Louis & Flint (2017). Formation of propionate and butyrate by the human colonic microbiota. *Environmental Microbiology*, 19(1), 29-41.
- McCann (2000). The diversity–stability debate. *Nature*, 405(6783), 228-233.
- O'Callaghan & van Sinderen (2016). Bifidobacteria and their role as members of the human gut microbiota. *FEMS Microbiology Reviews*, 40(3), 340-376.
- Pimentel et al. (2012). Methane production and bowel transit. *Neurogastroenterology & Motility*, 24(12), 1069-e546.
- Vital et al. (2014). Revealing the bacterial butyrate synthesis pathways by analyzing (meta)genomic data. *mBio*, 5(2), e00889-14.
- Windey et al. (2012). Relevance of protein fermentation to gut health. *Molecular Nutrition & Food Research*, 56(1), 184-196.
