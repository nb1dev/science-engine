#!/usr/bin/env python3
"""
Bedrock Client — Single entry point for all AWS Bedrock Claude API calls.

All LLM modules import from here. Configuration (model, region, temperature)
lives in one place. No other module should call boto3 directly.
"""

import json
import re
from typing import Dict

try:
    import boto3
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False

# ─── CONFIG ───────────────────────────────────────────────────────────────────

BEDROCK_MODEL = "eu.anthropic.claude-sonnet-4-20250514-v1:0"
BEDROCK_REGION = "eu-west-1"
MAX_TOKENS = 4096
OPUS_MODEL_ID = 'eu.anthropic.claude-opus-4-6-v1'


def call_bedrock(system_prompt: str, user_prompt: str, max_tokens: int = MAX_TOKENS,
                 model_id: str = None, temperature: float = 0.2) -> str:
    """Call AWS Bedrock Claude API and return response text.

    Args:
        system_prompt: System-level instructions.
        user_prompt: User message content.
        max_tokens: Maximum response tokens.
        model_id: Override model (default: Sonnet). Use OPUS_MODEL_ID for Opus.
        temperature: Sampling temperature. 0.2 for clinical, 0.05 for max reproducibility.

    Returns:
        Raw response text string.

    Raises:
        RuntimeError: If boto3 is not installed.
    """
    if not HAS_BOTO3:
        raise RuntimeError("boto3 not installed. Run: pip install boto3")

    from botocore.config import Config
    config = Config(read_timeout=300, connect_timeout=10, retries={'max_attempts': 3})
    client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION, config=config)

    response = client.invoke_model(
        modelId=model_id or BEDROCK_MODEL,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": temperature,
        })
    )

    result = json.loads(response["body"].read())
    return result["content"][0]["text"]


def _strip_html_tags(text: str) -> str:
    """Remove any HTML tags from LLM response text.

    LLM sometimes outputs inline HTML (e.g. <span style="font-weight:600">)
    in rationale strings. This strips them before JSON parsing so downstream
    JSON values stay clean.
    """
    return re.sub(r'<[^>]+>', '', text)


def extract_json_from_response(text: str) -> Dict:
    """Extract JSON from LLM response (handles markdown code blocks).

    Tries in order:
    1. JSON in ```json ... ``` code blocks
    2. Direct JSON parse of entire text
    3. First { ... } object found in text

    Raises:
        ValueError: If no valid JSON can be extracted.
    """
    # Strip any HTML tags that the LLM may have injected into response text
    text = _strip_html_tags(text)

    # Try to find JSON in code blocks
    json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group(1))

    # Try direct JSON parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in text
    brace_match = re.search(r'\{.*\}', text, re.DOTALL)
    if brace_match:
        return json.loads(brace_match.group(0))

    raise ValueError(f"Could not extract JSON from LLM response:\n{text[:500]}")
