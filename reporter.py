"""Reporting and output generation module for hardware DV job postings.

Exports curated leads to structured CSV and sorted Markdown reports,
and renders a clean pipeline funnel summary in the terminal.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from filter import CAT_ASIC_UVM, CAT_FPGA, CAT_GENERAL, CAT_RISCV

logger = logging.getLogger(__name__)

# Preferred ordering for category reporting
CATEGORY_ORDER = [
    CAT_RISCV,
    CAT_ASIC_UVM,
    CAT_FPGA,
    CAT_GENERAL,
]


@dataclass
class PipelineMetrics:
    """Tracks metrics across each stage of the scraping and filtering pipeline."""

    total_scraped: int = 0
    excluded_by_url_cache: int = 0
    dropped_by_hard_exclusions: int = 0
    dropped_by_citizenship: int = 0
    dropped_by_seniority: int = 0
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

    Columns: Title, Company, Location, Remote, Score, Category, Matched Keywords, URL.
    """
    path = Path(target_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "Title",
        "Company",
        "Location",
        "Remote",
        "Score",
        "Category",
        "Matched Keywords",
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

            row = {
                "Title": str(job.get("title") or "").strip(),
                "Company": str(job.get("company") or "").strip(),
                "Location": str(job.get("location") or "").strip(),
                "Remote": remote_str,
                "Score": f"{float(job.get('score', 0.0)):.1f}",
                "Category": str(job.get("category") or "").strip(),
                "Matched Keywords": kw_str,
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


def export_to_markdown(jobs: list[dict[str, Any]], target_file: Path | str) -> Path:
    """Export curated jobs to formatted Markdown sorted descending by Score and grouped by Category."""
    path = Path(target_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Sort all jobs descending by score
    sorted_jobs = sorted(jobs, key=lambda j: float(j.get("score", 0.0)), reverse=True)

    # Group jobs by category respecting CATEGORY_ORDER
    grouped: dict[str, list[dict[str, Any]]] = {cat: [] for cat in CATEGORY_ORDER}
    other_jobs: list[dict[str, Any]] = []

    for job in sorted_jobs:
        cat = job.get("category")
        if cat in grouped:
            grouped[cat].append(job)
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
        "## Summary by Domain Category",
        "",
        "| Category | Count | Top Score |",
        "|:---|:---:|:---:|",
    ]

    for cat in CATEGORY_ORDER:
        cat_jobs = grouped.get(cat, [])
        top_score = f"{float(cat_jobs[0].get('score', 0)):.1f}" if cat_jobs else "-"
        lines.append(f"| **{cat}** | {len(cat_jobs)} | {top_score} |")

    if other_jobs:
        top_score = f"{float(other_jobs[0].get('score', 0)):.1f}" if other_jobs else "-"
        lines.append(f"| **Other Categories** | {len(other_jobs)} | {top_score} |")

    lines.append("")
    lines.append("---")
    lines.append("")

    # Output listings by category in order
    def render_category_block(cat_name: str, job_list: list[dict[str, Any]]) -> None:
        if not job_list:
            return
        lines.append(f"## {cat_name} ({len(job_list)} postings)")
        lines.append("")

        for idx, job in enumerate(job_list, 1):
            title = str(job.get("title") or "Untitled Position").strip()
            url = str(job.get("job_url") or job.get("url") or "#").strip()
            company = str(job.get("company") or "Unknown Company").strip()
            location = str(job.get("location") or "Unspecified Location").strip()
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

            excerpt = _clean_excerpt(job.get("raw_description") or job.get("description") or "")

            lines.append(f"### {idx}. [{title}]({url})")
            lines.append(
                f"- **Company:** {company} | **Location:** {location} | **Remote:** {remote_str} | **Score:** **{score:.1f}**"
            )
            lines.append(
                f"- **Platform:** {platform} | **Posted:** {date_posted}"
            )
            lines.append(f"- **Matched Keywords:** {kw_badges}")
            if excerpt:
                lines.append(f"- **Overview:** {excerpt}")
            lines.append("")

        lines.append("---")
        lines.append("")

    for cat in CATEGORY_ORDER:
        render_category_block(cat, grouped.get(cat, []))

    if other_jobs:
        render_category_block("Other Opportunities", other_jobs)

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
    print(f"  [6] Dropped by Low Score:              {metrics.dropped_by_low_score:>5}  (Below threshold or off-domain)")
    print(sep_single)
    print(f"  [7] Successfully Curated:              {metrics.successfully_curated:>5}")
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
