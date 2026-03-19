# Archive — Knowledge Base v1 (17 March 2026)

## What is archived here

| File | Description |
|------|-------------|
| `synbiotic_mixes_v1.json` | Probiotic mix definitions v1.0.0 — strains, default prebiotic formulas, ecological rationale for all 8 mixes |
| `prebiotic_rules_v1.json` | Prebiotic selection and dosing rules v1.0.0 — per-mix substrate requirements, FODMAP overrides, dosing tiers |

## Why archived

These files were replaced on **17 March 2026** with v2 versions based on updated source documents:
- `documents/supplement_module/final/probiotic_mixes_v2.xlsx` — updated strain roster with confirmed suppliers (IFF, Novonesis, Probiotical, Verb Biotics)
- `documents/supplement_module/final/prebiotics4mixes_v2.md` — updated substrate rationale per mix

## Key changes in v2

### synbiotic_mixes.json
All 8 mixes received updated strain definitions. Summary of changes:

| Mix | v1 strains | v2 strains |
|-----|-----------|-----------|
| Mix 1 — Dysbiosis Recovery | L. acidophilus CL1285; L. reuteri DSM 17938; B. longum DSM 24736 | L. acidophilus NCFM (IFF); L. reuteri UALre-16™ (Novonesis); B. longum Bl-05 (IFF) |
| Mix 2 — Bifidogenic Restore | B. breve BR03 only; L. acidophilus CL1285 | B. breve BR03+B632 premix (Probiotical); L. acidophilus NCFM (IFF) |
| Mix 3 — Fiber & SCFA Restoration | L. plantarum KABP022 + KABP023 (2 CECT strains) | L. plantarum UALp-05™ (Novonesis) + L. acidophilus NCFM (IFF); 4 strains at 12.5B each |
| Mix 4 — Proteolytic Suppression | L. reuteri ATCC PTA 6475; L. reuteri DSM 17938; B. longum DSM 24736 | L. reuteri UALre-16™ (Novonesis); L. casei 431™ (Novonesis); B. longum Bl-05 (IFF) |
| Mix 5 — Mucus Barrier Restoration | L. plantarum ECGC 13110402; L. paracasei CNCM I-1518; L. casei Shirota | L. plantarum 299v (IFF); B. animalis lactis HN019 (IFF); B. infantis/longum 35624 (Novonesis); B. breve BR03+B632 premix (Probiotical) |
| Mix 6 — Maintenance Gold Standard | 8 De Simone strains (DSM 24730–24737) | 6 strains: LA-5™, UALpc-04™, Lpla33™, UASt-09™ (all Novonesis); Bl-05 (IFF); BR03+B632 premix (Probiotical); 8.3B each |
| Mix 7 — Psychobiotic | Individual LF16, LR06, LP01, B. longum 04 entries | 40B Probiotical premix (4 strains combined) + 10B LP815 (Verb Biotics) |
| Mix 8 — Fiber Expansion & Competitive Displacement | L. plantarum KABP022; B. longum DSM 24736 | L. plantarum UALp-05™ (Novonesis); B. longum Bl-05 (IFF) |

All mixes: 50B CFU total. Mix 3 uses 4 strains at 12.5B each; Mix 6 uses 6 strains at ~8.3B each; Mix 7 uses 40B premix + 10B LP815.

### prebiotic_rules.json
- Mix 3 (`mix_3`): `mix_name` corrected to "Fiber & SCFA Restoration"; GOS added to `must_include`; rationale updated for NCFM fructan degradation pathway
- Mix 5 (`mix_5`): GOS moved to `must_include`; HMOs added to `highly_recommended`; rationale updated for Bifido-heavy composition
- Mix 6 (`mix_6`): `mix_name` updated; minor substrate note updates
- Mix 7 (`mix_7`): tryptophan note added; serotonin precursor rationale made explicit

## Rollback instructions

To restore v1:
```bash
cp science-engine/formulation/knowledge_base/archive/v1_2026_03_17/synbiotic_mixes_v1.json \
   science-engine/formulation/knowledge_base/synbiotic_mixes.json

cp science-engine/formulation/knowledge_base/archive/v1_2026_03_17/prebiotic_rules_v1.json \
   science-engine/formulation/knowledge_base/prebiotic_rules.json
```

No script changes are needed for rollback — all pipeline scripts load KB files by filename only.
