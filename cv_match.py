"""CV-based match scoring.

Cross-references a posting's extracted Requirements bullets against the candidate's
own skills list, so the report can show e.g. "Matches 6/9 stated requirements"
instead of leaving the reader to eyeball the overlap themselves.

The skills list is personal data (derived from the candidate's CV) and is loaded
from a local, gitignored JSON file rather than hardcoded here - this module and its
matching logic are generic and safe to commit, the skills themselves are not.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_SKILLS_PATH = "my_skills.json"


def load_skills(path: str | Path = DEFAULT_SKILLS_PATH) -> list[str]:
    """Load the candidate's skills list from a local JSON file (a flat list of strings).

    Returns an empty list if the file doesn't exist or can't be parsed, so CV-match
    scoring is simply skipped rather than erroring when it isn't configured - this
    file is gitignored and won't exist on a fresh clone until the candidate creates it.
    """
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to load skills file '%s': %s", p, e)
        return []

    if not isinstance(data, list):
        logger.warning("Skills file '%s' must contain a JSON list of strings.", p)
        return []

    return [str(s).strip() for s in data if str(s).strip()]


def _build_skill_patterns(skills: list[str]) -> list[re.Pattern]:
    return [re.compile(r"\b" + re.escape(skill.lower()) + r"\b") for skill in skills]


def score_requirements_match(
    requirements: list[str], skills: list[str]
) -> tuple[int, int]:
    """Count how many requirement bullets mention at least one of the candidate's skills.

    A requirement is counted as matched if any skill appears in it as a whole-word,
    case-insensitive substring (e.g. skill "UVM" matches requirement text containing
    "UVM" but not "UVMx"). Returns (matched_count, total_count); (0, 0) if there are
    no requirements to check.
    """
    if not requirements:
        return (0, 0)
    if not skills:
        return (0, len(requirements))

    patterns = _build_skill_patterns(skills)
    matched = 0
    for req in requirements:
        req_lower = req.lower()
        if any(pattern.search(req_lower) for pattern in patterns):
            matched += 1
    return (matched, len(requirements))


def format_match(matched: int, total: int) -> str:
    """Format a (matched, total) pair as a human-readable string, e.g. '6/9 (67%)'.
    Returns '' when there's nothing to report (no requirements were extracted)."""
    if total == 0:
        return ""
    pct = round(100 * matched / total)
    return f"{matched}/{total} ({pct}%)"
