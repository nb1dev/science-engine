"""
formatting.py — Shared display formatting helpers.

Used by formulation_automation and report_automation pipelines.
Centralised here to avoid circular imports between modules.
"""


def format_dose(value) -> str:
    """Format a dose value, stripping .0 from whole numbers.
    
    Examples: 1500.0 → "1500", 712.5 → "712.5", 0.9 → "0.9", None → "—"
    """
    if value is None:
        return "—"
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def sleep_label(score) -> str:
    """Convert sleep quality score (1-10, higher=better) to human-readable label.
    
    Disambiguates the "8/10 — good or bad?" question.
    Scale: 1 = very poor sleep quality, 10 = excellent sleep quality.
    """
    if score is None:
        return "not reported"
    try:
        score = int(score)
    except (ValueError, TypeError):
        return "not reported"
    if score <= 3:
        return "poor"
    elif score <= 5:
        return "below average"
    elif score <= 7:
        return "moderate"
    elif score <= 9:
        return "good"
    else:
        return "excellent"
