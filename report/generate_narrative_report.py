#!/usr/bin/env python3
"""
generate_narrative_report.py — Generate comprehensive narrative microbiome report via Bedrock LLM

Produces a 5,000-7,000 word Markdown report in the Concise Internal Assessment format,
generated section-by-section through multiple Bedrock API calls.

Input: _microbiome_analysis.json + _only_metrics.txt
Output: {sample_id}_narrative_report.md (+ optional PDF)

Usage:
  python3 generate_narrative_report.py --sample-dir /path/to/sample/
  python3 generate_narrative_report.py --batch-dir /path/to/batch/
  python3 generate_narrative_report.py --sample-dir /path/to/sample/ --from-json /path/to/analysis.json
"""

import argparse
import json
import logging
import os
import re
import sys
import glob
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

DEFAULT_MODEL_ID = 'eu.anthropic.claude-opus-4-6-v1'
DEFAULT_REGION = 'eu-west-1'


# ══════════════════════════════════════════════════════════════
#                    BEDROCK CLIENT
# ══════════════════════════════════════════════════════════════

def _create_bedrock_client(region: str = DEFAULT_REGION):
    """Create a boto3 Bedrock Runtime client with extended timeout for large sections."""
    try:
        import boto3
        from botocore.config import Config
        config = Config(
            read_timeout=300,  # 5 minutes — large sections need 60-90s
            connect_timeout=10,
            retries={'max_attempts': 3},
        )
        return boto3.client('bedrock-runtime', region_name=region, config=config)
    except ImportError:
        logger.error("boto3 not installed. Run: pip install boto3")
        return None
    except Exception as e:
        logger.error(f"Failed to create Bedrock client: {e}")
        return None


def _call_bedrock(client, prompt: str, system_prompt: str,
                  model_id: str = DEFAULT_MODEL_ID,
                  max_tokens: int = 4000) -> str:
    """Call Bedrock and return text response."""
    if client is None:
        return "[LLM unavailable]"

    body = {
        'anthropic_version': 'bedrock-2023-05-31',
        'max_tokens': max_tokens,
        'temperature': 0.2,
        'messages': [{'role': 'user', 'content': prompt}],
        'system': system_prompt,
    }

    try:
        response = client.invoke_model(
            modelId=model_id,
            contentType='application/json',
            accept='application/json',
            body=json.dumps(body),
        )
        result = json.loads(response['body'].read())
        return result['content'][0]['text'].strip()
    except Exception as e:
        logger.error(f"Bedrock call failed: {e}")
        return f"[LLM error: {str(e)[:200]}]"


# ══════════════════════════════════════════════════════════════
#                    DATA LOADING
# ══════════════════════════════════════════════════════════════

def _load_analysis_json(sample_dir: str, analysis_path: str = None) -> dict:
    """Load the _microbiome_analysis.json for a sample."""
    if analysis_path:
        with open(analysis_path) as f:
            return json.load(f)

    # Search in output dir first, then sample dir
    sample_id = os.path.basename(sample_dir.rstrip('/'))
    automation_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')

    candidates = [
        os.path.join(sample_dir, 'reports', 'reports_json', f'microbiome_analysis_master_{sample_id}.json'),
        os.path.join(sample_dir, 'reports', 'reports_json', f'{sample_id}_microbiome_analysis.json'),
        os.path.join(automation_dir, f'{sample_id}_microbiome_analysis.json'),
        os.path.join(sample_dir, 'report_json', f'{sample_id}_microbiome_analysis.json'),
    ]

    for path in candidates:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)

    raise FileNotFoundError(f"No _microbiome_analysis.json found for {sample_id}")


def _load_raw_metrics(sample_dir: str) -> str:
    """Load raw _only_metrics.txt as string for LLM context."""
    sample_id = os.path.basename(sample_dir.rstrip('/'))
    # New path first, fallback to legacy
    for subdir in ['bioinformatics/only_metrics', 'only_metrics']:
        metrics_path = os.path.join(sample_dir, subdir, f'{sample_id}_only_metrics.txt')
        if os.path.exists(metrics_path):
            with open(metrics_path) as f:
                return f.read()
    return ""


def _load_framework() -> str:
    """Load the optimized report generation framework."""
    framework_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'knowledge_base', 'concise_report_framework.md'
    )
    with open(framework_path) as f:
        return f.read()


# ══════════════════════════════════════════════════════════════
#                    METRICS CONTEXT BUILDER
# ══════════════════════════════════════════════════════════════

def _build_priority_section(guilds: dict) -> str:
    """Build canonical priority ordering using shared guild_priority module.
    Single source of truth — eliminates duplication across pipelines."""
    shared_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shared')
    if shared_dir not in sys.path:
        sys.path.insert(0, shared_dir)
    from guild_priority import format_priority_text
    return format_priority_text(guilds)


def _build_section7_skeleton(guilds: dict) -> str:
    """Pre-render the Section 7 guild order skeleton so the LLM cannot reorder.
    
    Returns a numbered list of guilds that need restoration, in canonical
    priority order (CRITICAL first, then 1A, then 1B). Monitor guilds are
    excluded — they don't need restoration steps.
    """
    shared_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shared')
    if shared_dir not in sys.path:
        sys.path.insert(0, shared_dir)
    from guild_priority import build_priority_list

    items = build_priority_list(guilds)
    lines = []
    step = 0
    for item in items:
        if item["priority_score"] <= 0:
            continue  # Skip Monitor — no restoration needed
        step += 1
        lines.append(
            f"  {step}. [{item['priority_level']}] {item['guild_name']} "
            f"({item['abundance_pct']:.1f}% → target range) — "
            f"{item['scenario'].lower()}, score {item['priority_score']}"
        )
    if not lines:
        return "  All guilds at Monitor — no restoration steps needed"
    return "\n".join(lines)


def _build_metrics_context(analysis: dict, raw_metrics: str) -> str:
    """Build comprehensive metrics context for the LLM."""
    sample_id = analysis.get('report_metadata', {}).get('sample_id', 'UNKNOWN')

    # Extract key data
    score = analysis.get('overall_score', {})
    eco = analysis.get('ecological_metrics', {})
    safety = analysis.get('safety_profile', {})
    met = analysis.get('metabolic_function', {})
    vitamins = analysis.get('vitamin_synthesis', {})
    guilds = analysis.get('bacterial_groups', {})
    root_causes = analysis.get('root_causes', {})
    debug = analysis.get('_debug', {})
    raw = debug.get('raw_metrics', {})

    # Build guild summary
    guild_lines = []
    for gname, gdata in guilds.items():
        clr_str = f"CLR {gdata.get('clr', 'N/A')}" if gdata.get('clr') is not None else "CLR undefined (<1%)"
        guild_lines.append(
            f"  {gname}: {gdata.get('abundance', 0):.2f}% "
            f"[range: {gdata.get('healthy_range', 'N/A')}] "
            f"status={gdata.get('status', 'N/A')}, {clr_str}, "
            f"J={gdata.get('evenness', 0):.2f} ({gdata.get('evenness_status', '')})"
        )

    # Build dial summary
    dials = met.get('dials', {})
    dial_lines = []
    for dk, dv in dials.items():
        dial_lines.append(f"  {dv.get('heading', dk)}: {dv.get('label', '')} (value={dv.get('value', 'N/A')}, raw={dv.get('raw_value', 'N/A')})")

    # Dysbiosis
    dysbiosis_lines = []
    for taxon, info in safety.get('dysbiosis_markers', {}).items():
        dysbiosis_lines.append(f"  {taxon}: {info.get('abundance', 0):.4f}% — {info.get('status', '')}")

    # Vitamins
    vitamin_lines = []
    for vname, vdata in vitamins.items():
        if isinstance(vdata, dict) and 'risk_level' in vdata:
            vitamin_lines.append(f"  {vname}: risk={vdata['risk_level']} ({vdata.get('risk_label', '')}) — {vdata.get('assessment', '')}")

    context = f"""SAMPLE ID: {sample_id}
REPORT DATE: {datetime.now().strftime('%B %d, %Y')}

═══ OVERALL SCORE ═══
Total: {score.get('total', 0)}/100 [{score.get('band', '')}]
Description: {score.get('description', '')}
Score Drivers: {json.dumps(score.get('score_drivers', {}), indent=2)}

═══ COMPOSITIONAL METRICS ═══
GMWI2: {raw.get('GMWI2', 'N/A')}
HF: {raw.get('HF', 'N/A')}
wGMWI2: {raw.get('wGMWI2', 'N/A')}
BR: {raw.get('BR', 'N/A')}
SB: {raw.get('SB', 'N/A')}
z-score: {raw.get('z_score', 'N/A')}
Shannon: {raw.get('Shannon', 'N/A')}
Pielou: {raw.get('Pielou', 'N/A')}
FB_ratio: {raw.get('FB_ratio', 'N/A')}

═══ ECOLOGICAL STATE ═══
Overall Balance: {json.dumps(eco.get('state', {}).get('overall_balance', {}), indent=2)}
Diversity: {json.dumps(eco.get('state', {}).get('diversity_resilience', {}), indent=2)}

═══ METABOLIC DIALS (CLR Ratios) ═══
CUR raw: {raw.get('CUR', 'N/A')}
FCR raw: {raw.get('FCR', 'N/A')}
MDR raw: {raw.get('MDR', 'N/A')}
PPR raw: {raw.get('PPR', 'N/A')}
{chr(10).join(dial_lines)}

═══ GUILDS (FFA-Weighted) ═══
{chr(10).join(guild_lines)}

═══ DYSBIOSIS MARKERS ═══
{chr(10).join(dysbiosis_lines)}
M. smithii: {safety.get('M_smithii_abundance', 0):.2f}%
BCFA pathways: {safety.get('bcfa_pathways_detected', 0)}

═══ VITAMIN SIGNALS ═══
{chr(10).join(vitamin_lines)}

═══ ROOT CAUSES ═══
Diagnostic flags: {json.dumps([f.get('flag', '') for f in root_causes.get('diagnostic_flags', [])], indent=2)}
Reversibility: {root_causes.get('reversibility', {}).get('label', 'N/A')} ({root_causes.get('reversibility', {}).get('estimated_timeline', 'N/A')})

═══ CANONICAL PRIORITY INTERVENTIONS (USE THIS ORDER — do NOT re-derive) ═══
{_build_priority_section(guilds)}

═══ SECTION 7 GUILD ORDER (use this EXACT order for Restoration Priorities) ═══
{_build_section7_skeleton(guilds)}

═══ SCORE DETAILS ═══
{json.dumps(score.get('details', {}), indent=2)}
"""

    # Append raw metrics if available (truncated to avoid token overflow)
    if raw_metrics:
        # Take first 3000 chars of raw metrics
        context += f"\n═══ RAW METRICS FILE (truncated) ═══\n{raw_metrics[:3000]}\n"

    return context


# ══════════════════════════════════════════════════════════════
#                    SECTION GENERATORS
# ══════════════════════════════════════════════════════════════

SECTION_CONFIGS = [
    {
        'name': 'Section 1: EXECUTIVE SUMMARY',
        'instruction': """Generate Section 1: EXECUTIVE SUMMARY (400-500 words). Be CONCISE — every sentence must add value.

Include ALL required elements in order:
1. Overall Pattern classification — be SPECIFIC (e.g., "Protein-Driven Dysbiosis with Bifidobacteria Depletion" NOT generic "Transitional Pattern"). Name the primary dysfunction.
2. Dysbiosis-Associated Markers (2-3 sentences, define E-S abbreviation). Include E-S CLR if elevated.
3. Critical Finding — most urgent single metabolic constraint
4. Metabolic State Summary — all 4 CLR ratios with values. Use "factory analogy" for clinical translation.
5. Priority Interventions — clean CRITICAL/1A/1B/Monitor hierarchy
6. Structural Concerns — bullet list (max 5 items)
7. Health Implications — ONE paragraph, max 80 words
8. Functional Pathways & Vitamin Biosynthesis — ONE paragraph, max 60 words""",
        'max_tokens': 2500,
    },
    {
        'name': 'Section 2: COMPOSITIONAL METRICS',
        'instruction': """Generate Section 2: COMPOSITIONAL METRICS (800-1,000 words).

Include subsections 2.1-2.5:
2.1 What We See — all presence/abundance/reference metrics with pattern classification
2.2 What This Means for Health — lay-friendly interpretation
2.3 Why This Happened — mechanistic drivers specific to this sample
2.4 Select Taxa Presence — table of 4 dysbiosis markers
2.5 Important Caveats""",
        'max_tokens': 4000,
    },
    {
        'name': 'Section 3: DIVERSITY SIGNATURES',
        'instruction': """Generate Section 3: DIVERSITY SIGNATURES (400-600 words).

Include 3.1-3.4:
3.1 What We See — Shannon, Pielou, zone classification
3.2 What This Means — ecosystem stability
3.3 Why This Pattern — drivers
3.4 Integration — link to compositional pattern""",
        'max_tokens': 2500,
    },
    {
        'name': 'Section 4 Part 1: GUILD FRAMEWORK + CLR RATIOS + STATUS TABLE',
        'instruction': """Generate Section 4 subsections 4.1, 4.2, and 4.3 (1,200-1,500 words).

4.1: CLR methodology explanation + interpretation table
4.2: CLR Ratio Dashboard — calculate all 4 ratios with formulas, values, interpretations. Use: CUR = [(Fiber_CLR + Bifido_CLR)/2] - Proteo_CLR; FCR = [(Butyrate_CLR + Cross_CLR)/2] - Bifido_CLR; MDR = Mucin_CLR - Fiber_CLR; PPR = Proteo_CLR - Butyrate_CLR. When guild <1%, treat CLR as 0. Include dashboard summary table.
4.3: Guild Status Table (all 6 guilds) with structural assessment.""",
        'max_tokens': 5000,
    },
    {
        'name': 'Section 4 Part 2: DETAILED GUILD ASSESSMENTS (All 6 Guilds)',
        'instruction': """Generate detailed assessments for ALL 6 guilds in A/B/C/D format (2,000-2,500 words TOTAL — approximately 350 words per guild).

Order by ecological priority for this sample. Each guild needs:
A) What We See (1-2 sentences: abundance, CLR, evenness)
B) What This Means (2-3 sentences: function, health impact)
C) Why This Pattern (2-3 sentences: drivers)
D) Ecological Restoration Mechanisms (150-200 words: metabolic role, cross-feeding, network consequences, recovery potential)

BE CONCISE. Each guild ~350 words total. No filler. Focus on what's UNIQUE to each guild.""",
        'max_tokens': 5000,
    },
    {
        'name': 'Section 4 Part 3: METABOLIC FLOW DIAGRAM',
        'instruction': """Generate Section 4.5: ASCII Parallel Metabolic Flow Diagram (300-500 words).

Show all 6 guilds with abundances, CLR values, substrate flows (Eat/Make/Effect), and system dynamics. Include key system insights (3-5 bullet points). Keep the diagram COMPACT.""",
        'max_tokens': 2000,
    },
    {
        'name': 'Section 5: FUNCTIONAL PATHWAYS & VITAMIN ASSESSMENT',
        'instruction': """Generate Section 5: FUNCTIONAL PATHWAYS & VITAMIN ASSESSMENT (600-800 words).

5.1: SCFA metabolism — pathways, capacity, realized efficiency
5.2: Vitamin signals — B12, Folate, Biotin, B-Complex with compositional risk indicators table and vitamin-specific signals. Include summary table.""",
        'max_tokens': 3000,
    },
    {
        'name': 'Section 6: INTEGRATED METABOLIC ASSESSMENT',
        'instruction': """Generate Section 6: INTEGRATED METABOLIC ASSESSMENT (800-1,000 words).

6.1: Cross-Module Pattern Recognition — convergent evidence
6.2: Health Implications Q&A (Energy? Barrier? Inflammation? Symptoms?)
6.3: Dietary Context Inference from CLR ratios (with disclaimer)
6.4: Why This Overall Pattern Emerged""",
        'max_tokens': 4000,
    },
    {
        'name': 'Sections 7-8: RESTORATION PRIORITIES + MONITORING',
        'instruction': """Generate Section 7: ECOLOGICAL RESTORATION PRIORITIES (400-600 words) and Section 8: MONITORING GUIDANCE (400-500 words).

⚠️ SECTION 7 CRITICAL RULE: You MUST use the EXACT guild order from "SECTION 7 GUILD ORDER" in the sample data above.
Do NOT re-derive priorities or reorder guilds. The order is pre-computed and authoritative.
CRITICAL guilds come first, then 1A, then 1B. SKIP guilds at Monitor priority — they don't need restoration.

Section 7: For each guild (in the EXACT order from SECTION 7 GUILD ORDER above):
- State current abundance → target range (early/intermediate/optimal)
- Explain why this priority level was assigned
- Define success markers for this guild
- NO prescriptive content (no dosages, strains, specific foods)

Section 8: Red flags, positive indicators, final success markers table. Use "early changes"/"full stabilization" NOT weeks/months.""",
        'max_tokens': 4000,
    },
    {
        'name': 'Sections 9-10 + REPORT SUMMARY',
        'instruction': """Generate Section 9: IMPORTANT LIMITATIONS (600-800 words), Section 10: MEDICAL DISCLAIMER (200-300 words), and REPORT SUMMARY (200-300 words).

Section 9: What we can/cannot measure, 5 mandatory scientific caveats (CLR sample-relative, abundance-function non-linear, proteolytic dose-dependent, guild capacity = healthy range max, substrate flow confounders), interpretation boundaries.

Section 10: Include verbatim: "This microbiome analysis provides insights into gut bacterial composition and potential metabolic functions based on DNA sequencing. It is NOT a diagnostic test and does NOT diagnose, treat, cure, or prevent any disease." + technical details.

REPORT SUMMARY: Brief 200-300 word summary of key findings, interventions, structural assessment, recovery potential.""",
        'max_tokens': 4000,
    },
]


# ══════════════════════════════════════════════════════════════
#     POST-LLM PRIORITY LABEL VALIDATION
# ══════════════════════════════════════════════════════════════

def _validate_priority_labels(section_text: str, guilds: dict) -> str:
    """Validate and auto-correct priority labels in LLM-generated text.

    The LLM sometimes writes incorrect priority labels (e.g., "1B" when the
    computed score says "1A"). This function enforces consistency with the
    canonical priority system from shared/guild_priority.py — the SAME source
    used by the formulation pipeline.

    Corrections are applied via regex replacement. Any corrections are logged.
    """
    shared_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shared')
    if shared_dir not in sys.path:
        sys.path.insert(0, shared_dir)
    from guild_priority import build_priority_list, GUILD_DISPLAY_NAMES

    items = build_priority_list(guilds)

    # Build guild name → correct priority label mapping
    # Include both internal and display names for matching
    label_map = {}  # {guild_name_lower: correct_label}
    for item in items:
        correct_label = item['priority_level']
        # Add all name variants for this guild
        for name in [item['guild_name'], item['guild_key']]:
            label_map[name.lower()] = correct_label
        # Also add display name variants from the shared module
        display = GUILD_DISPLAY_NAMES.get(item['guild_key'], '')
        if display:
            label_map[display.lower()] = correct_label

    # Valid priority labels in order of specificity
    VALID_LABELS = ['CRITICAL', '1A', '1B', 'Monitor']

    corrections = []
    corrected_text = section_text

    # Pattern 1: "CRITICAL — Guild Name" or "[CRITICAL] Guild Name" or "Priority CRITICAL"
    # Pattern 2: "Priority 1A — Guild Name" or "### 1. CRITICAL — Butyrate Producers"
    # Pattern 3: "(Priority Score: X.X)" — just verify label matches score context
    for item in items:
        if item['priority_score'] <= 0:
            continue  # Skip Monitor guilds (not in restoration section)

        correct = item['priority_level']
        guild_name = item['guild_name']

        # Build regex patterns that match this guild with any priority label
        # Escape guild name for regex
        guild_re = re.escape(guild_name)
        # Also try shorter/display variants
        guild_variants = [guild_re]
        display = GUILD_DISPLAY_NAMES.get(item['guild_key'], '')
        if display and display != guild_name:
            guild_variants.append(re.escape(display))

        for guild_pattern in guild_variants:
            # Match patterns like: "CRITICAL — Guild Name", "[1B] Guild Name",
            # "Priority 1A — Guild Name", "### N. CRITICAL — Guild Name"
            for wrong_label in VALID_LABELS:
                if wrong_label == correct:
                    continue  # Skip the correct label

                # Pattern: wrong_label followed by guild name (with connectors)
                pattern = rf'(\b{re.escape(wrong_label)}\b)([\s—\-:]+)({guild_pattern})'
                match = re.search(pattern, corrected_text, re.IGNORECASE)
                if match:
                    old_text = match.group(0)
                    new_text = old_text.replace(match.group(1), correct, 1)
                    corrected_text = corrected_text.replace(old_text, new_text, 1)
                    corrections.append(
                        f"  ⚠️ PRIORITY LABEL CORRECTION: '{guild_name}' "
                        f"was '{wrong_label}' → corrected to '{correct}' "
                        f"(score {item['priority_score']:.1f})"
                    )

                # Pattern: guild name followed by wrong_label in parentheses or after dash
                pattern2 = rf'({guild_pattern})([\s—\-:]+)(\b{re.escape(wrong_label)}\b)'
                match2 = re.search(pattern2, corrected_text, re.IGNORECASE)
                if match2:
                    old_text = match2.group(0)
                    new_text = old_text[:match2.start(3) - match2.start()] + correct + old_text[match2.end(3) - match2.start():]
                    corrected_text = corrected_text.replace(old_text, new_text, 1)
                    corrections.append(
                        f"  ⚠️ PRIORITY LABEL CORRECTION: '{guild_name}' "
                        f"was '{wrong_label}' → corrected to '{correct}' "
                        f"(score {item['priority_score']:.1f})"
                    )

    if corrections:
        logger.warning(f"  Post-LLM priority validation: {len(corrections)} correction(s) applied")
        for c in corrections:
            logger.warning(c)
    else:
        logger.info("  Post-LLM priority validation: all labels correct ✅")

    return corrected_text


# ══════════════════════════════════════════════════════════════
#                    REPORT ASSEMBLY
# ══════════════════════════════════════════════════════════════

def generate_narrative_report(sample_dir: str, analysis_path: str = None,
                               model_id: str = DEFAULT_MODEL_ID,
                               region: str = DEFAULT_REGION) -> str:
    """Generate the complete narrative report via section-by-section Bedrock calls."""
    sample_id = os.path.basename(sample_dir.rstrip('/'))
    logger.info(f"Generating narrative report for sample: {sample_id}")

    # Load data
    analysis = _load_analysis_json(sample_dir, analysis_path)
    raw_metrics = _load_raw_metrics(sample_dir)
    framework = _load_framework()

    # Build system prompt
    system_prompt = f"""You are generating a comprehensive scientific microbiome narrative report.
Follow this framework EXACTLY:

{framework}

CRITICAL RULES:
- Third-person only (NEVER "your")
- Italicize ALL taxonomic names with asterisks: *Bacteroides*, *Firmicutes*
- Non-prescriptive (no dosages, strains, specific foods, timing)
- Add space before numbers after < and >: "< 3%" not "<3%"
- Professional ecological assessment tone
- Each section MUST stand alone as complete content
- TOTAL REPORT MUST BE 5,000-7,000 WORDS. Be CONCISE. Avoid filler. Every sentence must add value.
- For pattern classification: Be SPECIFIC (e.g., "Protein-Driven Dysbiosis" not generic "Transitional"). Name the primary dysfunction.
- Use "factory analogy" in CLR dashboard integrated state: "The factory processes X but has lost Y production line..."
- DO NOT repeat information from previous sections. Each section covers NEW ground."""

    # Build metrics context
    metrics_context = _build_metrics_context(analysis, raw_metrics)

    # Create Bedrock client
    client = _create_bedrock_client(region)
    if client is None:
        logger.error("Cannot create Bedrock client — aborting narrative report generation")
        return ""

    # Generate header
    report_date = datetime.now().strftime('%B %d, %Y')
    header = f"""# Microbiome Analysis Report: Sample {sample_id}

**Report Date:** {report_date}
**Analysis Scope:** Ecological assessment of microbial composition, metabolic state, and restoration priorities

---

"""

    # Extract guilds for post-LLM priority validation
    guilds = analysis.get('bacterial_groups', {})

    # Sections that contain priority labels and need validation
    PRIORITY_SECTIONS = {
        'Section 1: EXECUTIVE SUMMARY',
        'Section 4 Part 1: GUILD FRAMEWORK + CLR RATIOS + STATUS TABLE',
        'Section 4 Part 2: DETAILED GUILD ASSESSMENTS (All 6 Guilds)',
        'Sections 7-8: RESTORATION PRIORITIES + MONITORING',
    }

    # Generate sections
    sections = [header]
    accumulated_context = ""

    for i, config in enumerate(SECTION_CONFIGS):
        logger.info(f"  Generating {config['name']}... ({i+1}/{len(SECTION_CONFIGS)})")

        # Build prompt with metrics + previous sections for consistency
        prompt = f"""Generate the following section for sample {sample_id}.

{config['instruction']}

SAMPLE DATA:
{metrics_context}

{"PREVIOUS SECTIONS (for consistency — do NOT repeat content):" + chr(10) + accumulated_context[-3000:] if accumulated_context else ""}

OUTPUT: Generate ONLY the requested section content in Markdown format. Start with the section heading. Do NOT include any meta-commentary."""

        section_text = _call_bedrock(client, prompt, system_prompt,
                                      model_id, config['max_tokens'])

        # Post-LLM priority label validation — ensures labels match
        # the canonical priority system used by the formulation pipeline
        if config['name'] in PRIORITY_SECTIONS and guilds:
            section_text = _validate_priority_labels(section_text, guilds)

        sections.append(section_text + "\n\n")
        accumulated_context += section_text + "\n\n"

        logger.info(f"    Generated ~{len(section_text.split())} words")

    # Generate footer
    footer = f"""---

**Report Generated:** {report_date}

---

**END OF MICROBIOME ANALYSIS REPORT**
"""
    sections.append(footer)

    # Assemble
    full_report = '\n'.join(sections)

    # Word count
    word_count = len(full_report.split())
    logger.info(f"  Total report: ~{word_count} words")

    return full_report


# ══════════════════════════════════════════════════════════════
#                    PDF GENERATION (OPTIONAL)
# ══════════════════════════════════════════════════════════════

def _markdown_to_pdf(md_path: str, pdf_path: str) -> bool:
    """Convert Markdown to PDF using pandoc (must be installed via brew)."""
    import subprocess
    import shutil

    pandoc_path = shutil.which('pandoc')
    if not pandoc_path:
        logger.warning("  PDF generation skipped — pandoc not found. Install with: brew install pandoc")
        return False

    try:
        result = subprocess.run(
            [pandoc_path, md_path, '-o', pdf_path,
             '--pdf-engine=xelatex',
             '-V', 'geometry:margin=2cm',
             '-V', 'fontsize=11pt',
             '-V', 'mainfont=Helvetica Neue',
             '-V', 'monofont=Courier New',
             '--highlight-style=tango'],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            logger.info(f"  PDF saved: {pdf_path}")
            return True
        else:
            # Try without pdflatex (use default engine)
            result2 = subprocess.run(
                [pandoc_path, md_path, '-o', pdf_path,
                 '-V', 'geometry:margin=2cm'],
                capture_output=True, text=True, timeout=60
            )
            if result2.returncode == 0:
                logger.info(f"  PDF saved: {pdf_path}")
                return True
            logger.warning(f"  PDF generation failed: {result2.stderr[:200]}")
            return False
    except subprocess.TimeoutExpired:
        logger.warning("  PDF generation timed out")
        return False
    except Exception as e:
        logger.warning(f"  PDF generation failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════
#                    MAIN PROCESSING
# ══════════════════════════════════════════════════════════════

def process_sample(sample_dir: str, analysis_path: str = None, **kwargs) -> str:
    """Generate narrative report for a single sample."""
    sample_id = os.path.basename(sample_dir.rstrip('/'))

    report_md = generate_narrative_report(sample_dir, analysis_path, **kwargs)

    if not report_md:
        logger.error(f"  Failed to generate narrative report for {sample_id}")
        return ""

    # Save to sample reports directory
    md_dir = os.path.join(sample_dir, 'reports', 'reports_md')
    pdf_dir = os.path.join(sample_dir, 'reports', 'reports_pdf')
    os.makedirs(md_dir, exist_ok=True)
    os.makedirs(pdf_dir, exist_ok=True)

    # Save Markdown
    md_filename = f'narrative_report_{sample_id}.md'
    md_path = os.path.join(md_dir, md_filename)
    with open(md_path, 'w') as f:
        f.write(report_md)
    logger.info(f"  Saved: {md_path}")

    # Try PDF generation
    pdf_filename = f'narrative_report_{sample_id}.pdf'
    _markdown_to_pdf(md_path, os.path.join(pdf_dir, pdf_filename))

    return md_path


def _has_analysis_json(sample_dir: str) -> bool:
    """Check if a sample has the required analysis JSON."""
    sample_id = os.path.basename(sample_dir)
    candidates = [
        os.path.join(sample_dir, 'reports', 'reports_json', f'microbiome_analysis_master_{sample_id}.json'),
        os.path.join(sample_dir, 'reports', 'reports_json', f'{sample_id}_microbiome_analysis.json'),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output', f'{sample_id}_microbiome_analysis.json'),
        os.path.join(sample_dir, 'report_json', f'{sample_id}_microbiome_analysis.json'),
    ]
    return any(os.path.exists(p) for p in candidates)


def _process_sample_safe(sample_dir: str, **kwargs) -> dict:
    """Thread-safe wrapper for process_sample — catches exceptions and returns result dict."""
    sample_id = os.path.basename(sample_dir)
    try:
        path = process_sample(sample_dir, **kwargs)
        return {'sample_id': sample_id, 'status': 'success', 'output': path}
    except Exception as e:
        logger.error(f"  Failed {sample_id}: {e}")
        return {'sample_id': sample_id, 'status': 'error', 'error': str(e)}


def process_batch(batch_dir: str, parallel: int = 1, **kwargs):
    """Process all samples in a batch directory.

    Args:
        batch_dir: Path to batch directory containing sample subdirectories
        parallel: Number of samples to process concurrently (default 1 = sequential).
                  Recommended: 3 for Bedrock Opus (stays within rate limits).
                  Each sample makes 10 sequential API calls, so parallel=3 means
                  ~3 concurrent Bedrock invocations at any time.
        **kwargs: Passed to process_sample (model_id, region, etc.)
    """
    sample_dirs = sorted(glob.glob(os.path.join(batch_dir, '*')))
    sample_dirs = [d for d in sample_dirs if os.path.isdir(d) and not d.endswith('.DS_Store')]

    # Filter to samples with analysis JSON
    eligible = []
    for sample_dir in sample_dirs:
        sample_id = os.path.basename(sample_dir)
        if _has_analysis_json(sample_dir):
            eligible.append(sample_dir)
        else:
            logger.warning(f"  Skipping {sample_id} — no _microbiome_analysis.json")

    logger.info(f"Batch narrative report generation: {len(eligible)} samples"
                f"{f' (parallel={parallel})' if parallel > 1 else ''}")

    if parallel > 1 and len(eligible) > 1:
        # ── PARALLEL MODE: ThreadPoolExecutor ──
        # Threads (not processes) because the workload is I/O-bound (waiting for
        # Bedrock API responses). Each thread holds ~50MB RAM. GIL is not a
        # bottleneck since we spend >99% of time in network I/O.
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = []
        with ThreadPoolExecutor(max_workers=parallel) as executor:
            future_to_sample = {
                executor.submit(_process_sample_safe, sd, **kwargs): os.path.basename(sd)
                for sd in eligible
            }
            for future in as_completed(future_to_sample):
                sample_id = future_to_sample[future]
                result = future.result()
                results.append(result)
                status_icon = '✅' if result['status'] == 'success' else '❌'
                logger.info(f"  {status_icon} {sample_id} — {result['status']}"
                            f" ({len(results)}/{len(eligible)})")
    else:
        # ── SEQUENTIAL MODE ──
        results = []
        for sample_dir in eligible:
            result = _process_sample_safe(sample_dir, **kwargs)
            results.append(result)

    success = sum(1 for r in results if r['status'] == 'success')
    errors = sum(1 for r in results if r['status'] == 'error')
    logger.info(f"\nBatch complete: {success} success, {errors} errors out of {len(eligible)} samples")
    return results


def main():
    parser = argparse.ArgumentParser(
        description='Generate narrative microbiome report via Bedrock LLM',
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--sample-dir', help='Path to single sample directory')
    group.add_argument('--batch-dir', help='Path to batch directory')

    parser.add_argument('--from-json', help='Path to specific _microbiome_analysis.json')
    parser.add_argument('--model-id', default=DEFAULT_MODEL_ID, help='Bedrock model ID')
    parser.add_argument('--region', default=DEFAULT_REGION, help='AWS region')
    parser.add_argument('--parallel', type=int, default=1,
                        help='Number of samples to process concurrently in batch mode '
                             '(default: 1 = sequential). Recommended: 3 for Bedrock Opus.')
    parser.add_argument('--verbose', '-v', action='store_true')

    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    kwargs = {'model_id': args.model_id, 'region': args.region}

    if args.sample_dir:
        process_sample(args.sample_dir, analysis_path=args.from_json, **kwargs)
    elif args.batch_dir:
        process_batch(args.batch_dir, parallel=args.parallel, **kwargs)


if __name__ == '__main__':
    main()
