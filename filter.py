"""Verification and domain filtering engine for hardware DV and accelerator jobs.

Performs rule-based keyword extraction, hard exclusions check, weighted scoring,
and primary domain categorization with strict prioritization for RISC-V, UVM, and FPGA.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Category Display Names
CAT_RISCV = "RISC-V / Processor DV"
CAT_ASIC_UVM = "ASIC / UVM DV"
CAT_FPGA = "FPGA / Accelerator Design"
CAT_GENERAL = "General Digital/Hardware Design"

# Weights per keyword match
WEIGHT_RISCV = 5
WEIGHT_DV = 3
WEIGHT_FPGA = 2
WEIGHT_GENERAL = 2

# Minimum score required by default
DEFAULT_MIN_SCORE = 8

# 1. Hard Exclusions (Discard immediately if matched)
HARD_EXCLUSIONS_LIST = [
    "selenium",
    "cypress",
    "playwright",
    "appium",
    "postman",
    "react",
    "angular",
    "figma",
    "ui/ux",
    "clinical validation",
    "gmp",
    "fda",
    "software quality assurance",
    "manual tester",
]

# Nationality/work-authorization exclusions (opt-out via exclude_citizenship_restricted).
# Strong-signal phrases only, to minimize false positives against generic EEO boilerplate
# like "must be authorized to work in <country>" (which most postings include regardless
# of whether they actually sponsor).
WORK_AUTH_EXCLUSIONS_LIST = [
    "us citizen",
    "u.s. citizen",
    "us citizens only",
    "citizens only",
    "us person",
    "u.s. person",
    "itar",
    "no visa sponsorship",
    "not provide sponsorship",
    "not able to sponsor",
    "unable to sponsor",
    "will not sponsor",
    "does not sponsor",
    "cannot sponsor",
    "without sponsorship",
    "security clearance",
    "secret clearance",
    "top secret clearance",
    "habilitation secret defense",
    "habilitation secret défense",
    "nationalite francaise",
    "nationalité française",
    "ressortissant francais",
    "ressortissant français",
    "canadian citizen or permanent resident",
]

# 2. Domain Keywords
RISCV_KEYWORDS_LIST = [
    "risc-v",
    "riscv",
    "spike",
    "iss",
    "instruction set simulator",
    "riscv-dv",
    "core-v-verif",
    "rvvi",
    "step-and-compare",
    "lockstep",
    "cpu verification",
    "processor verification",
]

HARDWARE_DV_KEYWORDS_LIST = [
    "uvm",
    "universal verification methodology",
    "systemverilog",
    "constrained-random",
    "functional coverage",
    "sva",
    "systemverilog assertions",
    "formal verification",
    "xcelium",
    "vcs",
    "questa",
]

FPGA_ACCELERATOR_KEYWORDS_LIST = [
    "zynq",
    "vivado",
    "vitis",
    "axi",
    "axi-stream",
    "dma",
    "hardware accelerator",
    "fixed-point",
    "rtl",
    "vhdl",
    "soc",
]

# General digital/hardware design keywords - broadens acceptance beyond the
# RISC-V/UVM/FPGA-accelerator niche to roles matching a wider DV/design profile.
GENERAL_HW_KEYWORDS_LIST = [
    "digital design",
    "hardware engineer",
    "asic",
    "verilog",
    "hdl",
    "hardware description language",
    "logic design",
    "chip design",
    "semiconductor",
    "modelsim",
    "cadence",
    "synopsys",
    "mentor graphics",
    "testbench",
    "co-simulation",
    "combinational logic",
    "sequential logic",
    "digital logic",
    "embedded hardware",
    "microarchitecture",
]

# Seniority signals inspected in the job title only (low false-positive risk).
SENIOR_TITLE_KEYWORDS = [
    "senior",
    "sr.",
    "staff",
    "principal",
    "distinguished",
    "director",
    "lead",
    "head of",
    "vp",
    "vice president",
    "manager",
    "chief",
]
JUNIOR_TITLE_KEYWORDS = [
    "junior",
    "jr.",
    "entry level",
    "entry-level",
    "associate",
    "new grad",
    "new graduate",
    "graduate",
    "intern",
    "internship",
    "co-op",
    "coop",
]


def _build_keyword_regex(keyword: str) -> re.Pattern:
    """Compile a precise case-insensitive regex for a keyword with boundary enforcement."""
    kw_lower = keyword.lower().strip()

    if kw_lower == "ui/ux":
        pattern = r"\bui\s*[/\\-]?\s*ux\b"
    elif kw_lower == "manual tester":
        pattern = r"\bmanual\s+testers?\b"
    elif kw_lower == "instruction set simulator":
        pattern = r"\binstruction\s+set\s+simulators?\b"
    elif kw_lower == "hardware accelerator":
        pattern = r"\bhardware\s+accelerators?\b"
    elif kw_lower == "systemverilog assertions":
        pattern = r"\bsystem\s*verilog\s+assertions?\b"
    elif kw_lower == "systemverilog":
        # Don't match if it's immediately followed by assertions (which is its own keyword)
        pattern = r"\bsystem\s*verilog\b(?!\s+assertions?\b)"
    elif kw_lower == "soc":
        pattern = r"\bsocs?\b"
    elif kw_lower == "questa":
        pattern = r"\bquesta(sim)?\b"
    elif kw_lower == "axi":
        # Don't match if it's part of axi-stream
        pattern = r"\baxi\b(?![-_ ]?stream\b)"
    elif kw_lower == "axi-stream":
        pattern = r"\baxi[-_ ]?stream\b"
    elif kw_lower == "risc-v":
        pattern = r"\brisc[- ]v\b(?![-_ ]?dv\b)"
    elif kw_lower == "riscv":
        pattern = r"\briscv\b(?![-_ ]?dv\b)"
    elif kw_lower == "riscv-dv":
        pattern = r"\briscv[-_ ]?dv\b"
    elif "-" in kw_lower:
        # e.g., 'step-and-compare', 'core-v-verif', 'fixed-point'
        parts = [re.escape(p) for p in kw_lower.split("-")]
        pattern = r"\b" + r"[-_\s]+".join(parts) + r"\b"
    elif " " in kw_lower:
        parts = [re.escape(p) for p in kw_lower.split()]
        pattern = r"\b" + r"\s+".join(parts) + r"\b"
    else:
        pattern = r"\b" + re.escape(kw_lower) + r"\b"

    return re.compile(pattern, re.IGNORECASE)


# Precompiled regex mappings: dict[str, re.Pattern]
HARD_EXCLUSION_PATTERNS: dict[str, re.Pattern] = {
    kw: _build_keyword_regex(kw) for kw in HARD_EXCLUSIONS_LIST
}
WORK_AUTH_EXCLUSION_PATTERNS: dict[str, re.Pattern] = {
    kw: _build_keyword_regex(kw) for kw in WORK_AUTH_EXCLUSIONS_LIST
}
RISCV_PATTERNS: dict[str, re.Pattern] = {
    kw: _build_keyword_regex(kw) for kw in RISCV_KEYWORDS_LIST
}
HARDWARE_DV_PATTERNS: dict[str, re.Pattern] = {
    kw: _build_keyword_regex(kw) for kw in HARDWARE_DV_KEYWORDS_LIST
}
FPGA_PATTERNS: dict[str, re.Pattern] = {
    kw: _build_keyword_regex(kw) for kw in FPGA_ACCELERATOR_KEYWORDS_LIST
}
GENERAL_PATTERNS: dict[str, re.Pattern] = {
    kw: _build_keyword_regex(kw) for kw in GENERAL_HW_KEYWORDS_LIST
}

TITLE_RISCV_PATTERN = re.compile(r"\b(risc[- ]?v|cpu|processor)\b", re.IGNORECASE)

SENIOR_TITLE_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in SENIOR_TITLE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
JUNIOR_TITLE_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in JUNIOR_TITLE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def classify_seniority(title: Optional[str]) -> str:
    """Classify a job title as Junior/Entry, Senior+, or Unspecified.

    Only inspects the title (not the description) to keep false-positive
    risk low - body text mentioning "senior engineers on the team" etc.
    should not taint the classification.
    """
    t = title or ""
    is_junior = bool(JUNIOR_TITLE_PATTERN.search(t))
    is_senior = bool(SENIOR_TITLE_PATTERN.search(t))
    if is_senior and not is_junior:
        return "Senior+"
    if is_junior and not is_senior:
        return "Junior/Entry"
    return "Unspecified"


class FilterResult(BaseModel):
    """Detailed evaluation result of filtering and scoring a job listing."""

    passed: bool
    rejection_reason: Optional[str] = None
    matched_exclusion: Optional[str] = None
    score: float = 0.0
    category: Optional[str] = None
    matched_keywords: list[str] = Field(default_factory=list)
    category_scores: dict[str, float] = Field(default_factory=dict)
    matched_by_category: dict[str, list[str]] = Field(default_factory=dict)
    seniority: str = "Unspecified"


def normalize_text(text: Optional[str]) -> str:
    """Normalize input text for regex scanning."""
    if not text:
        return ""
    # Standardize whitespace and non-breaking spaces
    cleaned = text.replace("\xa0", " ").replace("\r\n", "\n")
    return cleaned


def evaluate_job(
    title: str,
    description: str,
    min_score: float = DEFAULT_MIN_SCORE,
    exclude_senior_titles: bool = False,
    exclude_citizenship_restricted: bool = False,
) -> FilterResult:
    """Evaluate a job listing against verification criteria and assign weighted scores.

    Rules:
    1. Check hard exclusions -> discard if matched.
    2. If exclude_citizenship_restricted, discard postings requiring citizenship,
       security clearance, or explicitly offering no visa sponsorship.
    3. If exclude_senior_titles, discard titles classified as Senior+.
    4. Extract keywords from normalized title and description.
    5. Verify at least one keyword matches Core RISC-V, Standard Hardware DV,
       or General Digital/Hardware Design.
    6. Compute composite score:
       (Core RISC-V: +5 each, Standard DV: +3 each, FPGA/Accelerator: +2 each,
        General Digital/Hardware Design: +2 each).
    7. Verify score >= min_score.
    8. Tag surviving job with primary domain category.
    """
    norm_title = normalize_text(title)
    norm_desc = normalize_text(description)
    combined_text = f"{norm_title}\n{norm_desc}"
    seniority = classify_seniority(norm_title)

    # Step 1: Check Hard Exclusions
    for kw, pattern in HARD_EXCLUSION_PATTERNS.items():
        if pattern.search(combined_text):
            logger.debug("Job '%s' dropped by hard exclusion: %s", title, kw)
            return FilterResult(
                passed=False,
                rejection_reason="HARD_EXCLUSION",
                matched_exclusion=kw,
                seniority=seniority,
            )

    # Step 2: Citizenship/work-authorization gate (opt-in; e.g. main.py excludes by default)
    if exclude_citizenship_restricted:
        for kw, pattern in WORK_AUTH_EXCLUSION_PATTERNS.items():
            if pattern.search(combined_text):
                logger.debug("Job '%s' dropped: citizenship/work-auth restriction (%s)", title, kw)
                return FilterResult(
                    passed=False,
                    rejection_reason="CITIZENSHIP_RESTRICTED",
                    matched_exclusion=kw,
                    seniority=seniority,
                )

    # Step 3: Seniority gate (opt-in; e.g. main.py excludes Senior+ by default)
    if exclude_senior_titles and seniority == "Senior+":
        logger.debug("Job '%s' dropped: seniority mismatch (Senior+)", title)
        return FilterResult(
            passed=False,
            rejection_reason="SENIORITY_MISMATCH",
            seniority=seniority,
        )

    # Step 4: Match domain categories
    matched_riscv = [
        kw for kw, pat in RISCV_PATTERNS.items() if pat.search(combined_text)
    ]
    matched_dv = [
        kw for kw, pat in HARDWARE_DV_PATTERNS.items() if pat.search(combined_text)
    ]
    matched_fpga = [
        kw for kw, pat in FPGA_PATTERNS.items() if pat.search(combined_text)
    ]
    matched_general = [
        kw for kw, pat in GENERAL_PATTERNS.items() if pat.search(combined_text)
    ]

    all_matched = matched_riscv + matched_dv + matched_fpga + matched_general

    # Step 5: MUST match at least one keyword from Core RISC-V, Standard Hardware
    # DV, or General Digital/Hardware Design (FPGA/Accelerator alone is not enough).
    if not matched_riscv and not matched_dv and not matched_general:
        logger.debug(
            "Job '%s' dropped: no match in Core RISC-V, Standard Hardware DV, "
            "or General Digital/Hardware Design",
            title,
        )
        return FilterResult(
            passed=False,
            rejection_reason="NO_CORE_DV_OR_RISCV_KEYWORD",
            matched_keywords=all_matched,
            seniority=seniority,
        )

    # Step 6: Calculate weighted composite score
    score_riscv = len(matched_riscv) * WEIGHT_RISCV
    score_dv = len(matched_dv) * WEIGHT_DV
    score_fpga = len(matched_fpga) * WEIGHT_FPGA
    score_general = len(matched_general) * WEIGHT_GENERAL
    total_score = float(score_riscv + score_dv + score_fpga + score_general)

    category_scores = {
        CAT_RISCV: float(score_riscv),
        CAT_ASIC_UVM: float(score_dv),
        CAT_FPGA: float(score_fpga),
        CAT_GENERAL: float(score_general),
    }

    matched_by_category = {
        CAT_RISCV: matched_riscv,
        CAT_ASIC_UVM: matched_dv,
        CAT_FPGA: matched_fpga,
        CAT_GENERAL: matched_general,
    }

    # Step 7: Check min_score threshold
    if total_score < min_score:
        logger.debug(
            "Job '%s' dropped: score %.1f < threshold %.1f",
            title,
            total_score,
            min_score,
        )
        return FilterResult(
            passed=False,
            rejection_reason="LOW_SCORE",
            score=total_score,
            matched_keywords=all_matched,
            category_scores=category_scores,
            matched_by_category=matched_by_category,
            seniority=seniority,
        )

    # Step 8: Primary Domain Categorization
    # Prioritization:
    # 1. RISC-V / Processor DV: if RISC-V keywords matched and either
    #    title contains RISC-V/CPU or RISC-V score dominates the rest.
    # 2. Otherwise, whichever of ASIC/UVM DV, FPGA/Accelerator, or General
    #    Digital/Hardware Design scored highest.
    title_has_riscv = bool(TITLE_RISCV_PATTERN.search(norm_title))

    if score_riscv > 0 and (
        title_has_riscv or score_riscv >= max(score_dv, score_fpga, score_general)
    ):
        primary_category = CAT_RISCV
    else:
        ranked = sorted(
            [
                (score_dv, CAT_ASIC_UVM),
                (score_fpga, CAT_FPGA),
                (score_general, CAT_GENERAL),
            ],
            key=lambda pair: pair[0],
            reverse=True,
        )
        top_score, top_category = ranked[0]
        if top_score > 0:
            primary_category = top_category
        elif score_riscv > 0:
            primary_category = CAT_RISCV
        else:
            primary_category = CAT_ASIC_UVM

    return FilterResult(
        passed=True,
        score=total_score,
        category=primary_category,
        matched_keywords=all_matched,
        category_scores=category_scores,
        matched_by_category=matched_by_category,
        seniority=seniority,
    )
