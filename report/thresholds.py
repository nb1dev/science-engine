"""
thresholds.py — Load population thresholds from knowledge_base/population_thresholds.json

All scripts that need Shannon/Pielou/guild thresholds import from here.
Falls back to hardcoded defaults if the file doesn't exist yet.
"""

import json
import os
import logging

logger = logging.getLogger(__name__)

_THRESHOLDS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'knowledge_base', 'population_thresholds.json'
)

# Hardcoded fallbacks (initial values from n=41 analysis)
_FALLBACK = {
    'shannon': {'q25': 2.79, 'q50': 3.29, 'q75': 3.44},
    'pielou': {'q25': 0.66, 'q50': 0.72, 'q75': 0.76},
}

_cached = None


def load_thresholds() -> dict:
    """Load population thresholds. Caches after first load."""
    global _cached
    if _cached is not None:
        return _cached

    if os.path.exists(_THRESHOLDS_PATH):
        try:
            with open(_THRESHOLDS_PATH) as f:
                _cached = json.load(f)
            return _cached
        except Exception as e:
            logger.warning(f"Could not load thresholds: {e}")

    logger.info("Using fallback thresholds (population_thresholds.json not found)")
    _cached = _FALLBACK
    return _cached


def shannon_low() -> float:
    """Shannon Q25 — below this = low diversity."""
    return load_thresholds().get('shannon', {}).get('q25', 2.79)


def shannon_medium() -> float:
    """Shannon Q50 — population median. Below this = moderate diversity (challenge)."""
    return load_thresholds().get('shannon', {}).get('q50', 3.29)


def shannon_high() -> float:
    """Shannon Q75 — above this = high diversity."""
    return load_thresholds().get('shannon', {}).get('q75', 3.44)


def pielou_low() -> float:
    """Pielou Q25 — below this = low evenness."""
    return load_thresholds().get('pielou', {}).get('q25', 0.66)


def pielou_high() -> float:
    """Pielou Q75 — above this = high evenness."""
    return load_thresholds().get('pielou', {}).get('q75', 0.76)
