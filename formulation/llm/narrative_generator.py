#!/usr/bin/env python3
"""
LLM Narrative Generation — Ecological rationale + input narratives.

Uses Opus for deeper scientific reasoning and natural language summaries.
"""

import json
from typing import Dict

from .bedrock_client import call_bedrock, extract_json_from_response, OPUS_MODEL_ID


def generate_ecological_rationale(unified_input: Dict, mix_result: Dict) -> Dict:
    """Generate ecological rationale when alternative mixes were considered.

    Returns:
        {selected_rationale, alternative_analysis, combined_assessment, recommendation}
        or {} if no alternative was considered.
    """
    alternative = mix_result.get("alternative_considered", "")
    if not alternative:
        return {}

    guilds = unified_input["microbiome"]["guilds"]
    clr = unified_input["microbiome"]["clr_ratios"]

    guild_lines = []
    for gk, gv in guilds.items():
        clr_val = gv.get("clr")
        clr_str = "CLR %.2f" % clr_val if isinstance(clr_val, (int, float)) else "CLR N/A"
        guild_lines.append("%s: %.1f%% (%s) %s priority=%s" % (
            gv.get("name", gk), gv.get("abundance_pct", 0),
            gv.get("status", "?"), clr_str, gv.get("priority_level", "?")))

    prompt = """SELECTED: Mix %d (%s)
Trigger: %s
ALTERNATIVE: %s
CLR Context: %s

GUILDS:
%s
CLR: CUR=%s, FCR=%s, MDR=%s, PPR=%s

Explain: 1. Why selected is appropriate 2. Why alternative was not chosen
3. Whether combined strategy works 4. Recommendation

Return JSON: {"selected_rationale": "...", "alternative_analysis": "...",
"combined_assessment": "...", "recommendation": "..."}""" % (
        mix_result.get("mix_id", 0), mix_result.get("mix_name", "?"),
        mix_result.get("primary_trigger", "?"), alternative,
        mix_result.get("clr_context", ""),
        "\n".join(guild_lines),
        clr.get("CUR"), clr.get("FCR"), clr.get("MDR"), clr.get("PPR"))

    try:
        response = call_bedrock(
            "You are a microbiome ecologist. Be concise and scientifically precise. "
            "NEVER use 'you' or 'your' — use 'this client' or 'this sample's'.",
            prompt, max_tokens=1500, model_id=OPUS_MODEL_ID)
        return extract_json_from_response(response)
    except Exception as e:
        print("  ⚠️ Ecological rationale failed: %s" % e)
        return {"error": str(e)}


def generate_input_narratives(unified_input: Dict, rule_outputs: Dict) -> Dict:
    """Generate human-readable narrative summaries for board review.

    Returns:
        {microbiome_narrative, questionnaire_narrative}
    """
    guilds = unified_input["microbiome"]["guilds"]
    clr = unified_input["microbiome"]["clr_ratios"]
    q = unified_input["questionnaire"]
    score = unified_input["microbiome"]["overall_score"]

    guild_lines = ["%s: %.1f%% (%s)" % (gv.get("name", gk), gv.get("abundance_pct", 0), gv.get("status", "?"))
                   for gk, gv in guilds.items()]

    prompt = """Summarize as TWO short narrative paragraphs for scientific board review.

MICROBIOME: Score: %s/100 (%s)
Guilds: %s
CLR: CUR=%s, FCR=%s, MDR=%s, PPR=%s

QUESTIONNAIRE: Age: %s, Sex: %s, Goals: %s
Stress: %s/10, Sleep: %s/10, Bloating: %s/10
Sensitivity: %s, Completion: %s%%

Return JSON: {"microbiome_narrative": "...", "questionnaire_narrative": "..."}""" % (
        score.get("total", "?"), score.get("band", "?"),
        "; ".join(guild_lines),
        clr.get("CUR"), clr.get("FCR"), clr.get("MDR"), clr.get("PPR"),
        q.get("demographics", {}).get("age", "?"),
        q.get("demographics", {}).get("biological_sex", "?"),
        ", ".join(q.get("goals", {}).get("ranked", [])[:3]),
        q.get("lifestyle", {}).get("stress_level", "?"),
        q.get("lifestyle", {}).get("sleep_quality", "?"),
        q.get("digestive", {}).get("bloating_severity", "?"),
        rule_outputs.get("sensitivity", {}).get("classification", "?"),
        q.get("completion", {}).get("completion_pct", 0))

    try:
        response = call_bedrock(
            "You write readable summaries for a scientific review board. "
            "NEVER use 'you' or 'your' — use 'this client' or 'this sample's'.",
            prompt, max_tokens=800, model_id=OPUS_MODEL_ID)
        return extract_json_from_response(response)
    except Exception as e:
        print("  ⚠️ Input narrative generation failed: %s" % e)
        return {"microbiome_narrative": "", "questionnaire_narrative": ""}
