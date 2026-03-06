# Priority System — Historical Changes

## Version 2.0 — March 2026 (Current)

### New Formula: `priority = importance × state × evenness_modifier`

Replaces two independent priority calculation systems with a single, unified approach.

### Components

**IMPORTANCE weights** (guild ecological role):
| Guild | Weight | Rationale |
|-------|--------|-----------|
| Fiber Degraders | 1.0 | Baseline — entry point of fermentation chain |
| Butyrate Producers | 1.2 | Direct gut lining energy source |
| Bifidobacteria | 0.9 | Lactate amplifier (important but not sole bottleneck) |
| Cross-Feeders | 1.1 | Central linking role in trophic chain |
| Proteolytic Guild | 1.1 | Harsh byproducts — high impact when overgrown |
| Mucin Degraders | 0.6 | Barrier risk but lower systemic impact |

**STATE values** (from 9-scenario matrix: range position × CLR competition):

Beneficial guilds:
| Scenario | Value | Condition |
|----------|-------|-----------|
| DEPLETED | 10 | Below range + CLR suppressed |
| UNDERSTAFFED | 7 | Below range + CLR balanced |
| SUBSTRATE LIMITED | 5 | Below range + CLR enriched |
| UNDER PRESSURE | 3 | Within range + CLR suppressed |
| HEALTHY/THRIVING | 0 | Within range or above, no issues |

Contextual guilds (proteolytic, mucin):
| Scenario | Value | Condition |
|----------|-------|-----------|
| OVERGROWTH | 10 | Above range + CLR enriched |
| ABUNDANT | 6 | Above range + CLR balanced |
| CROWDED | 4 | Above range + CLR suppressed |
| FAVORABLE | 0 | Within/below range |

**EVENNESS modifier** (asymmetric by guild type, evidence-based):
- Only applies when BOTH: community J < 0.40 AND guild state > 0
- Beneficial guilds: J < 0.40 → ×1.2, J 0.40–0.70 → ×1.1, J ≥ 0.70 → ×1.0
- Contextual guilds: J < 0.40 → ×1.3, J 0.40–0.70 → ×1.1, J ≥ 0.70 → ×1.0
- Rationale: Lower evenness = monoculture = more fragile. Contextual overgrowth in uneven community is more concerning (×1.3) than beneficial depletion (×1.2, may be transient).

**Priority labels from score:**
| Score | Label | Color |
|-------|-------|-------|
| ≥ 8.0 | CRITICAL | Red (#e74c3c) |
| 5.0 – 7.9 | 1A | Orange (#e67e22) |
| 2.0 – 4.9 | 1B | Amber (#f39c12) |
| < 2.0 or 0 | Monitor | Teal (#2ecc71) |

**Color scheme:**
- CRITICAL: Red `#e74c3c`
- 1A: Orange `#e67e22`  
- 1B: Amber `#f39c12`
- Monitor: Teal/Green `#2ecc71`

---

## Version 1.0 — February 2026 (REPLACED)

### What was replaced

**TWO independent priority systems existed:**

#### System A: `overview_fields.py → compute_bacterial_groups()`
Simple abundance + CLR threshold rules:
- Beneficial: absent/<50% min → CRITICAL, below min + CLR<-1 → 1A, below min → 1B, etc.
- Contextual: >3× max → CRITICAL, >2× max → 1A, >max → 1B

**Problem:** No ecological weighting. A depleted mucin degrader (low systemic impact) could be labeled CRITICAL while a depleted fiber degrader (starves entire chain) was only 1B.

#### System B: `action_plan_fields.py → _compute_priority_score()`
Weighted scoring: `severity × dependency_multiplier × evenness_caution`
- dependency_multiplier: Cross-Feeders=3.0, Fiber=2.0, Butyrate/Bifido=1.5, Mucin/Proteo=1.0
- Labels derived from score+rank thresholds

**Problem:** Independent from System A, producing different CRITICAL/1A/1B labels for the same client between documents.

### Why it was replaced
Batch 001 review (Marijn Lewis, Feb 2026) identified priority inconsistencies in 5/6 clients:
- Priority 1A/1B swapped between decision trace, narrative report, health report, and action plan
- Different documents showed different priority orders for the same guilds
- Root cause: two independent systems deriving labels differently

### Migration
- `overview_fields.py`: priority_level calculation replaced with unified system
- `action_plan_fields.py`: `_compute_priority_score()` and `_derive_priority_label()` replaced with unified system
- `generate_formulation.py`: `_build_priority_interventions()` now produces canonical sorted list stored in master JSON
- All downstream consumers (dashboards, reports, traces) read from master JSON — never recalculate
