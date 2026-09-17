"""Reporting and output generation module for hardware DV job postings.

Exports curated leads to structured CSV and sorted Markdown reports,
and renders a clean pipeline funnel summary in the terminal.
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from cv_match import format_match, load_skills, score_requirements_match
from filter import CAT_ASIC_UVM, CAT_FPGA, CAT_GENERAL, CAT_RISCV

logger = logging.getLogger(__name__)

# Preferred ordering for category reporting (still used for the terminal funnel breakdown)
CATEGORY_ORDER = [
    CAT_RISCV,
    CAT_ASIC_UVM,
    CAT_FPGA,
    CAT_GENERAL,
]

# Preferred ordering for country grouping in the Markdown report, matching the
# candidate's target-market priority.
COUNTRY_ORDER = [
    "Canada",
    "France",
    "Netherlands",
    "Switzerland",
    "Spain",
    "Remote",
]

_COUNTRY_NAME_PATTERNS: dict[str, re.Pattern] = {
    "Canada": re.compile(r"\bcanada\b", re.IGNORECASE),
    "France": re.compile(r"\bfrance\b", re.IGNORECASE),
    "Netherlands": re.compile(r"\b(netherlands|holland)\b", re.IGNORECASE),
    "Switzerland": re.compile(r"\b(switzerland|suisse|schweiz)\b", re.IGNORECASE),
    "Spain": re.compile(r"\b(spain|españa)\b", re.IGNORECASE),
}

# Fallback for listings whose location string omits the country name outright -
# matched against the candidate's known target/satellite cities.
_CITY_TO_COUNTRY: dict[str, str] = {
    "toronto": "Canada",
    "montreal": "Canada",
    "ottawa": "Canada",
    "vancouver": "Canada",
    "mississauga": "Canada",
    "hamilton": "Canada",
    "gatineau": "Canada",
    "laval": "Canada",
    "longueuil": "Canada",
    "burnaby": "Canada",
    "surrey": "Canada",
    "richmond": "Canada",
    "paris": "France",
    "lyon": "France",
    "villeurbanne": "France",
    "eindhoven": "Netherlands",
    "amsterdam": "Netherlands",
    "geneva": "Switzerland",
    "geneve": "Switzerland",
    "lausanne": "Switzerland",
    "barcelona": "Spain",
}


def extract_country(location: str) -> str:
    """Best-effort country extraction from a free-text location string, used to
    group the Markdown report. Checks for an explicit country name first, then
    falls back to matching known target/satellite cities (some platforms omit
    the country from the location field), then "Remote", then "Other"."""
    text = (location or "").strip()
    if not text:
        return "Other / Unspecified"

    for country, pattern in _COUNTRY_NAME_PATTERNS.items():
        if pattern.search(text):
            return country

    for city, country in _CITY_TO_COUNTRY.items():
        if re.search(rf"\b{re.escape(city)}\b", text, re.IGNORECASE):
            return country

    if re.search(r"\bremote\b", text, re.IGNORECASE):
        return "Remote"

    return "Other / Unspecified"


_INTERVAL_LABELS: dict[str, str] = {
    "yearly": "yr",
    "monthly": "mo",
    "weekly": "wk",
    "daily": "day",
    "hourly": "hr",
}


def format_salary(job: dict[str, Any]) -> str:
    """Format a human-readable salary range from jobspy's compensation fields
    (min_amount/max_amount/currency/salary_interval), or '' if the platform
    didn't provide salary data for this posting."""
    min_amount = job.get("min_amount")
    max_amount = job.get("max_amount")
    if not min_amount and not max_amount:
        return ""

    currency = str(job.get("currency") or "USD").strip()
    interval = str(job.get("salary_interval") or job.get("interval") or "yearly").strip().lower()
    label = _INTERVAL_LABELS.get(interval, interval)
    decimals = 2 if interval == "hourly" else 0

    def _fmt(amount: Any) -> str:
        return f"{float(amount):,.{decimals}f}"

    if min_amount and max_amount:
        return f"{currency} {_fmt(min_amount)} - {_fmt(max_amount)}/{label}"
    if min_amount:
        return f"{currency} {_fmt(min_amount)}+/{label}"
    return f"Up to {currency} {_fmt(max_amount)}/{label}"


def _normalize_for_fingerprint(text: str) -> str:
    """Lowercase and strip punctuation/whitespace noise for fuzzy comparison."""
    text = (text or "").lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _duplicate_fingerprint(job: dict[str, Any]) -> str:
    """Build a fuzzy-match key (normalized title + company + city) to identify the
    same real posting scraped from multiple platforms. Falls back to the job's own
    URL when title or company is missing, so postings with incomplete data are never
    incorrectly grouped together."""
    title = _normalize_for_fingerprint(str(job.get("title") or ""))
    company = _normalize_for_fingerprint(str(job.get("company") or ""))
    if not title or not company:
        return f"__unique__:{job.get('job_url') or job.get('url') or id(job)}"

    location = str(job.get("location") or "")
    city = _normalize_for_fingerprint(location.split(",")[0]) if location else ""
    return f"{title}|{company}|{city}"


def collapse_duplicates(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse postings that are likely the same real job scraped separately from
    multiple platforms (matching normalized title + company + city) into a single
    entry, keeping the highest-scoring version's fields and recording every other
    source under 'duplicate_sources'.

    This is a heuristic, not a guarantee: two genuinely distinct concurrent openings
    with an identical title at the same company in the same city would also collapse.
    Order of the input list (already score-sorted upstream) is preserved for groups.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for job in jobs:
        key = _duplicate_fingerprint(job)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(job)

    collapsed: list[dict[str, Any]] = []
    for key in order:
        group = groups[key]
        if len(group) == 1:
            collapsed.append(group[0])
            continue

        group_sorted = sorted(group, key=lambda j: float(j.get("score", 0.0)), reverse=True)
        primary = dict(group_sorted[0])
        primary["duplicate_count"] = len(group_sorted)
        primary["duplicate_sources"] = [
            {
                "platform": str(g.get("platform") or g.get("site") or "web"),
                "job_url": str(g.get("job_url") or g.get("url") or ""),
            }
            for g in group_sorted
        ]
        collapsed.append(primary)

    return collapsed


@dataclass
class PipelineMetrics:
    """Tracks metrics across each stage of the scraping and filtering pipeline."""

    total_scraped: int = 0
    excluded_by_url_cache: int = 0
    dropped_by_hard_exclusions: int = 0
    dropped_by_citizenship: int = 0
    dropped_by_seniority: int = 0
    dropped_by_experience: int = 0
    dropped_by_low_score: int = 0
    successfully_curated: int = 0
    category_counts: dict[str, int] = field(default_factory=dict)
    exclusion_breakdown: dict[str, int] = field(default_factory=dict)

    def record_cached(self) -> None:
        self.excluded_by_url_cache += 1

    def record_hard_exclusion(self, keyword: Optional[str] = None) -> None:
        self.dropped_by_hard_exclusions += 1
        if keyword:
            self.exclusion_breakdown[keyword] = self.exclusion_breakdown.get(keyword, 0) + 1

    def record_citizenship_exclusion(self, keyword: Optional[str] = None) -> None:
        self.dropped_by_citizenship += 1
        if keyword:
            self.exclusion_breakdown[keyword] = self.exclusion_breakdown.get(keyword, 0) + 1

    def record_seniority_mismatch(self) -> None:
        self.dropped_by_seniority += 1

    def record_experience_mismatch(self, keyword: Optional[str] = None) -> None:
        self.dropped_by_experience += 1
        if keyword:
            self.exclusion_breakdown[keyword] = self.exclusion_breakdown.get(keyword, 0) + 1

    def record_low_score(self) -> None:
        self.dropped_by_low_score += 1

    def record_curated(self, category: str) -> None:
        self.successfully_curated += 1
        self.category_counts[category] = self.category_counts.get(category, 0) + 1


def generate_filenames(output_dir: str | Path = ".", date_str: Optional[str] = None) -> tuple[Path, Path]:
    """Generate standardized CSV and Markdown report file paths."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    csv_file = out_path / f"curated_jobs_{date_str}.csv"
    md_file = out_path / f"curated_jobs_{date_str}.md"
    return csv_file, md_file


def export_to_csv(jobs: list[dict[str, Any]], target_file: Path | str) -> Path:
    """Export curated jobs to structured CSV.

    Columns: Title, Company, Location, Remote, Salary, Score, Category, CV Match,
    Matched Keywords, Cross-Posted, URL.
    """
    path = Path(target_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    skills = load_skills()

    fieldnames = [
        "Title",
        "Company",
        "Location",
        "Remote",
        "Salary",
        "Score",
        "Category",
        "CV Match",
        "Matched Keywords",
        "Cross-Posted",
        "URL",
    ]

    with open(path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for job in jobs:
            matched_kw = job.get("matched_keywords", [])
            if isinstance(matched_kw, (list, set, tuple)):
                kw_str = ", ".join(matched_kw)
            elif isinstance(matched_kw, str):
                import json
                try:
                    parsed = json.loads(matched_kw)
                    kw_str = ", ".join(parsed) if isinstance(parsed, list) else matched_kw
                except Exception:
                    kw_str = matched_kw
            else:
                kw_str = ""

            remote_val = job.get("is_remote")
            remote_str = "Yes" if remote_val in (True, 1, "1", "True", "true") else "No"

            raw_desc = job.get("raw_description") or job.get("description") or ""
            requirements = extract_requirements(raw_desc)
            matched, total = score_requirements_match(requirements, skills)

            other_sources = job.get("duplicate_sources") or []
            cross_posted = ", ".join(
                s["platform"].capitalize() for s in other_sources
                if s.get("job_url") != (job.get("job_url") or job.get("url"))
            )

            row = {
                "Title": str(job.get("title") or "").strip(),
                "Company": str(job.get("company") or "").strip(),
                "Location": str(job.get("location") or "").strip(),
                "Remote": remote_str,
                "Salary": format_salary(job),
                "Score": f"{float(job.get('score', 0.0)):.1f}",
                "Category": str(job.get("category") or "").strip(),
                "CV Match": format_match(matched, total),
                "Matched Keywords": kw_str,
                "Cross-Posted": cross_posted,
                "URL": str(job.get("job_url") or job.get("url") or "").strip(),
            }
            writer.writerow(row)

    logger.info("Exported %d curated jobs to CSV: %s", len(jobs), path)
    return path


def _clean_excerpt(description: str, max_chars: int = 240) -> str:
    """Generate a clean single-paragraph excerpt from markdown or raw text."""
    if not description:
        return ""
    lines = [line.strip() for line in description.splitlines() if line.strip()]
    full_text = " ".join(lines)
    # Remove markdown header markers if at start
    full_text = full_text.replace("###", "").replace("##", "").replace("#", "").strip()
    if len(full_text) > max_chars:
        return full_text[:max_chars].rstrip() + "..."
    return full_text


# Section headers that mark the start of the skills/requirements block we want to extract.
_REQUIREMENTS_HEADER_PHRASES = {
    "requirements", "requirement", "required qualifications", "minimum qualifications",
    "preferred qualifications", "basic qualifications", "key requirements",
    "qualifications", "key qualifications", "required skills", "technical skills",
    "skills required", "desired skills", "essential skills", "what you'll need",
    "what youll need", "what you need", "what you bring", "what we're looking for",
    "what we are looking for", "must haves", "must have", "you have", "about you",
    "your profile", "your background", "skills and experience", "experience required",
}

# Section headers that mark the start of a DIFFERENT block, used only to know where the
# requirements block ends (they are not extracted themselves).
_OTHER_SECTION_HEADER_PHRASES = {
    "responsibilities", "key responsibilities", "duties", "what you'll do",
    "what youll do", "what you will do", "role", "about the role", "about us",
    "about the company", "about the team", "benefits", "perks", "compensation",
    "salary", "how to apply", "equal opportunity", "equal employment", "diversity",
    "why join us", "our culture", "overview", "summary", "job summary", "description",
}


def _normalize_header_line(line: str) -> str:
    """Strip markdown decoration from a line, returning its bare lowercase text."""
    stripped = re.sub(r"^#{1,6}\s*", "", line.strip())
    stripped = stripped.strip("*_ \t")
    return stripped.rstrip(":").strip().lower()


def _header_kind(line: str) -> Optional[str]:
    """Classify a line as a 'requirements' header, an 'other' section header, or
    neither. Only short lines are considered, since real headers are brief."""
    norm = _normalize_header_line(line)
    if not norm or len(norm) > 40:
        return None
    if any(phrase in norm for phrase in _REQUIREMENTS_HEADER_PHRASES):
        return "requirements"
    if any(phrase in norm for phrase in _OTHER_SECTION_HEADER_PHRASES):
        return "other"
    return None


def extract_requirements(
    description: str, max_items: int = 8, max_chars_per_item: int = 200
) -> list[str]:
    """Extract the skills/requirements bullet points from a job description.

    Scans for a recognized "Requirements"/"Qualifications"/etc. section header, then
    collects the bullet lines that follow it, stopping at the next recognized section
    header (e.g. "Responsibilities", "Benefits") or at max_items. Returns an empty list
    if no such section is found - callers should fall back to a generic excerpt then,
    since not every posting is structured this cleanly.
    """
    if not description:
        return []

    text = description.replace("\xa0", " ").replace("\r\n", "\n")
    lines = text.split("\n")

    start_idx = None
    for i, line in enumerate(lines):
        if _header_kind(line) == "requirements":
            start_idx = i + 1
            break
    if start_idx is None:
        return []

    end_idx = len(lines)
    for j in range(start_idx, len(lines)):
        if _header_kind(lines[j]) is not None:
            end_idx = j
            break

    chunk_lines = lines[start_idx:end_idx]

    def _truncate(item: str) -> str:
        if len(item) > max_chars_per_item:
            return item[:max_chars_per_item].rstrip() + "..."
        return item

    bullets: list[str] = []
    for line in chunk_lines:
        item = re.sub(r"^[-*•●▪]+\s*", "", line.strip())
        item = re.sub(r"^\d+[.)]\s*", "", item).strip()
        if not item:
            continue
        bullets.append(_truncate(item))
        if len(bullets) >= max_items:
            break

    # No bullet-per-line structure (a single prose paragraph) - split on sentences instead.
    if len(bullets) <= 1 and chunk_lines:
        blob = " ".join(line.strip() for line in chunk_lines if line.strip())
        if blob:
            bullets = []
            for sentence in re.split(r"(?<=[.;])\s+", blob):
                sentence = sentence.strip()
                if not sentence:
                    continue
                bullets.append(_truncate(sentence))
                if len(bullets) >= max_items:
                    break

    return bullets


def export_to_markdown(jobs: list[dict[str, Any]], target_file: Path | str) -> Path:
    """Export curated jobs to formatted Markdown, grouped by country (target-market
    priority order) and sorted descending by Score within each country."""
    path = Path(target_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    skills = load_skills()

    # Sort all jobs descending by score
    sorted_jobs = sorted(jobs, key=lambda j: float(j.get("score", 0.0)), reverse=True)

    # Group jobs by country (already score-sorted, so each group stays sorted)
    grouped: dict[str, list[dict[str, Any]]] = {country: [] for country in COUNTRY_ORDER}
    other_jobs: list[dict[str, Any]] = []

    for job in sorted_jobs:
        country = extract_country(str(job.get("location") or ""))
        if country in grouped:
            grouped[country].append(job)
        else:
            other_jobs.append(job)

    date_str = datetime.now().strftime("%Y-%m-%d")
    total_count = len(jobs)

    lines: list[str] = [
        f"# Curated Hardware Verification & Accelerator Jobs ({date_str})",
        "",
        f"> **Total Curated Postings:** {total_count}  ",
        f"> **Generated On:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        "",
        "## Summary by Country",
        "",
        "| Country | Count | Top Score |",
        "|:---|:---:|:---:|",
    ]

    for country in COUNTRY_ORDER:
        country_jobs = grouped.get(country, [])
        top_score = f"{float(country_jobs[0].get('score', 0)):.1f}" if country_jobs else "-"
        lines.append(f"| **{country}** | {len(country_jobs)} | {top_score} |")

    if other_jobs:
        top_score = f"{float(other_jobs[0].get('score', 0)):.1f}" if other_jobs else "-"
        lines.append(f"| **Other / Unspecified** | {len(other_jobs)} | {top_score} |")

    lines.append("")
    lines.append("---")
    lines.append("")

    # Output listings by country in order, each group already sorted by score descending
    def render_country_block(country_name: str, job_list: list[dict[str, Any]]) -> None:
        if not job_list:
            return
        lines.append(f"## {country_name} ({len(job_list)} postings)")
        lines.append("")

        for idx, job in enumerate(job_list, 1):
            title = str(job.get("title") or "Untitled Position").strip()
            url = str(job.get("job_url") or job.get("url") or "#").strip()
            company = str(job.get("company") or "Unknown Company").strip()
            location = str(job.get("location") or "Unspecified Location").strip()
            category = str(job.get("category") or "Uncategorized").strip()
            remote_val = job.get("is_remote")
            remote_str = "Yes" if remote_val in (True, 1, "1", "True", "true") else "No"
            score = float(job.get("score", 0.0))
            platform = str(job.get("platform") or job.get("site") or "Web").capitalize()
            date_posted = str(job.get("date_posted") or "N/A").strip()

            # Format matched keywords as badges
            matched_kw = job.get("matched_keywords", [])
            if isinstance(matched_kw, str):
                import json
                try:
                    matched_kw = json.loads(matched_kw)
                except Exception:
                    matched_kw = [k.strip() for k in matched_kw.split(",") if k.strip()]

            kw_badges = " ".join(f"`{kw}`" for kw in matched_kw) if matched_kw else "`None`"

            raw_desc = job.get("raw_description") or job.get("description") or ""
            requirements = extract_requirements(raw_desc)
            salary = format_salary(job)
            matched, total = score_requirements_match(requirements, skills)
            cv_match = format_match(matched, total)

            lines.append(f"### {idx}. [{title}]({url})")
            company_line = (
                f"- **Company:** {company} | **Location:** {location} | "
                f"**Remote:** {remote_str} | **Score:** **{score:.1f}**"
            )
            if salary:
                company_line += f" | **Salary:** {salary}"
            lines.append(company_line)
            category_line = f"- **Category:** {category} | **Platform:** {platform} | **Posted:** {date_posted}"
            if cv_match:
                category_line += f" | **CV Match:** {cv_match}"
            lines.append(category_line)
            lines.append(f"- **Matched Keywords:** {kw_badges}")
            if requirements:
                lines.append("- **Requirements:**")
                for req in requirements:
                    lines.append(f"  - {req}")
            else:
                excerpt = _clean_excerpt(raw_desc)
                if excerpt:
                    lines.append(f"- **Overview:** {excerpt}")

            other_sources = [
                s for s in (job.get("duplicate_sources") or [])
                if s.get("job_url") and s["job_url"] != url
            ]
            if other_sources:
                links = " · ".join(
                    f"[{s['platform'].capitalize()}]({s['job_url']})" for s in other_sources
                )
                lines.append(f"- **Also Found On:** {links}")

            lines.append("")

        lines.append("---")
        lines.append("")

    for country in COUNTRY_ORDER:
        render_country_block(country, grouped.get(country, []))

    if other_jobs:
        render_country_block("Other / Unspecified", other_jobs)

    with open(path, mode="w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info("Exported %d curated jobs to Markdown: %s", len(jobs), path)
    return path


def print_live_job_hit(job: dict[str, Any]) -> None:
    """Print a single curated job the moment it's found, for live progress feedback
    during a long multi-query, multi-location scrape (instead of only seeing results
    once the entire run completes)."""
    title = str(job.get("title") or "Untitled Position").strip()
    company = str(job.get("company") or "Unknown Company").strip()
    location = str(job.get("location") or "Unspecified").strip()
    category = str(job.get("category") or "Uncategorized").strip()
    score = float(job.get("score", 0.0))
    url = str(job.get("job_url") or job.get("url") or "").strip()

    print(f"  [+] CURATED {score:>5.1f} pts | {category:<32} | {title} @ {company} ({location})", flush=True)
    if url:
        print(f"      {url}", flush=True)


def print_terminal_funnel(
    metrics: PipelineMetrics,
    csv_file: Optional[Path | str] = None,
    md_file: Optional[Path | str] = None,
    unique_postings: Optional[int] = None,
) -> None:
    """Display the formatted terminal output showing the pipeline funnel."""
    sep_double = "=" * 78
    sep_single = "-" * 78

    print("\n" + sep_double)
    print("                      HARDWARE DV JOB PIPELINE FUNNEL")
    print(sep_double)
    print(f"  [1] Total Scraped:                     {metrics.total_scraped:>5}")
    print(f"  [2] Excluded by URL Cache:             {metrics.excluded_by_url_cache:>5}  (Skipped existing entries)")
    print(f"  [3] Dropped by Hard Exclusions:        {metrics.dropped_by_hard_exclusions:>5}  (QA/Web/Clinical non-hardware)")
    print(f"  [4] Dropped by Citizenship/Sponsorship:{metrics.dropped_by_citizenship:>5}  (Clearance/citizenship/no-sponsorship)")
    print(f"  [5] Dropped by Seniority Mismatch:     {metrics.dropped_by_seniority:>5}  (Senior/Staff/Principal/Lead titles)")
    print(f"  [6] Dropped by Experience Mismatch:    {metrics.dropped_by_experience:>5}  (Requires 5+ years by default)")
    print(f"  [7] Dropped by Low Score:              {metrics.dropped_by_low_score:>5}  (Below threshold or off-domain)")
    print(sep_single)
    print(f"  [8] Successfully Curated:              {metrics.successfully_curated:>5}")
    if unique_postings is not None and unique_postings != metrics.successfully_curated:
        print(f"  [9] Unique After De-duplication:       {unique_postings:>5}  (Cross-platform duplicates collapsed)")
    print(sep_double)

    # Funnel Flow Summary
    print(
        f"  Pipeline Flow: {metrics.total_scraped} Scraped -> "
        f"{metrics.total_scraped - metrics.excluded_by_url_cache} New -> "
        f"{metrics.total_scraped - metrics.excluded_by_url_cache - metrics.dropped_by_hard_exclusions} Not Excluded -> "
        f"{metrics.successfully_curated} Curated"
    )
    print(sep_double)

    # Category Breakdown
    print("  Curated Category Breakdown:")
    for cat in CATEGORY_ORDER:
        count = metrics.category_counts.get(cat, 0)
        print(f"    * {cat:<30} : {count:>3} jobs")
    for cat, count in metrics.category_counts.items():
        if cat not in CATEGORY_ORDER:
            print(f"    * {cat:<30} : {count:>3} jobs")

    # Output Files
    if csv_file or md_file:
        print(sep_single)
        print("  Artifacts Generated:")
        if csv_file:
            print(f"    * CSV:      {csv_file}")
        if md_file:
            print(f"    * Markdown: {md_file}")

    print(sep_double + "\n")
