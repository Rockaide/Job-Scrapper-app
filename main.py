"""Main CLI Entrypoint for Hardware Verification Job Scraper and Ranker.

Scrapes, deduplicates, filters, ranks, and reports job opportunities
prioritizing RISC-V core verification and FPGA accelerator design.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from db import JobDatabase
from demo_data import SAMPLE_JOBS
from filter import (
    DEFAULT_MIN_SCORE,
    evaluate_job,
)
from reporter import (
    PipelineMetrics,
    export_to_csv,
    export_to_markdown,
    generate_filenames,
    print_live_job_hit,
    print_terminal_funnel,
)
from scraper import (
    DEFAULT_LOCATIONS,
    DEFAULT_PLATFORMS,
    DEFAULT_QUERY_ROTATION,
    JobScraper,
    ScraperConfig,
)

logger = logging.getLogger("hardware_job_hunter")


def configure_logging(verbose: bool = False) -> None:
    """Set up structured logging format and log level."""
    log_level = logging.DEBUG if verbose else logging.INFO
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=log_level, format=log_format, datefmt="%H:%M:%S")

    # Mute noisy third-party loggers unless deep debug
    if not verbose:
        for noisy in ("jobspy", "urllib3", "requests", "tls_client"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser matching project requirements."""
    parser = argparse.ArgumentParser(
        prog="job-hunter",
        description=(
            "Modular CLI application that scrapes, filters, and ranks job postings "
            "prioritizing RISC-V Core Verification and FPGA Hardware Accelerators."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Required CLI arguments from specification
    parser.add_argument(
        "--location",
        type=str,
        default=None,
        help=(
            "Single target city or country override (e.g. 'Remote'). "
            "When set, replaces the --locations list entirely for this run."
        ),
    )
    parser.add_argument(
        "--locations",
        type=str,
        default=";".join(DEFAULT_LOCATIONS),
        help=(
            "Semicolon-separated target cities/regions to search across "
            "(semicolon, not comma, since a location itself may read 'City, Country')"
        ),
    )
    parser.add_argument(
        "--distance",
        type=int,
        default=50,
        help=(
            "Search radius in miles around each target location, so conurbated/"
            "satellite cities are included (e.g. Mississauga/Hamilton near Toronto, "
            "Gatineau near Ottawa, Laval/Longueuil near Montreal, greater Ile-de-France "
            "around Paris). This is jobspy's own default radius."
        ),
    )
    parser.add_argument(
        "--hours-old",
        type=int,
        default=72,
        help="Post age window in hours",
    )
    parser.add_argument(
        "--is-remote",
        action="store_true",
        default=False,
        help="Filter exclusively for remote positions",
    )
    parser.add_argument(
        "--results-wanted",
        type=int,
        default=50,
        help="Number of listings per platform per query",
    )

    # Additional configuration options
    parser.add_argument(
        "--min-score",
        type=float,
        default=DEFAULT_MIN_SCORE,
        help="Minimum composite score required to curate a posting",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="jobs.db",
        help="Path to SQLite database for caching and persistence",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=".",
        help="Directory to save curated CSV and Markdown reports",
    )
    parser.add_argument(
        "--sites",
        type=str,
        default=",".join(DEFAULT_PLATFORMS),
        help="Comma-separated platforms to scrape (linkedin,indeed,glassdoor)",
    )
    parser.add_argument(
        "--queries",
        type=str,
        default=None,
        help="Custom comma-separated query terms (defaults to full rotation list)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        default=False,
        help="Run pipeline with realistic offline test dataset without live scraping",
    )
    parser.add_argument(
        "--include-senior",
        action="store_true",
        default=False,
        help="Include Senior/Staff/Principal/Lead/Director/Manager titles (excluded by default)",
    )
    parser.add_argument(
        "--include-restricted",
        action="store_true",
        default=False,
        help=(
            "Include postings requiring citizenship, security clearance, or explicitly "
            "offering no visa sponsorship (excluded by default)"
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose debug logging",
    )

    return parser


def process_item(
    item: dict[str, Any],
    existing_urls: set[str],
    seen_in_session: set[str],
    metrics: PipelineMetrics,
    args: argparse.Namespace,
) -> Optional[dict[str, Any]]:
    """Evaluate a single raw listing against dedup/filter rules.

    Returns the curated entry dict if the listing passed, otherwise None
    (metrics are updated either way).
    """
    metrics.total_scraped += 1

    url = str(item.get("job_url") or item.get("url") or "").strip()
    title = str(item.get("title") or "").strip()
    description = str(item.get("raw_description") or item.get("description") or "").strip()

    # Step A: Check Deduplication (URL Cache & Session Uniqueness)
    if not url or url in existing_urls or url in seen_in_session:
        metrics.record_cached()
        return None

    seen_in_session.add(url)

    # Step B: Evaluation and Scoring
    evaluation = evaluate_job(
        title=title,
        description=description,
        min_score=args.min_score,
        exclude_senior_titles=not args.include_senior,
        exclude_citizenship_restricted=not args.include_restricted,
    )

    if not evaluation.passed:
        if evaluation.rejection_reason == "HARD_EXCLUSION":
            metrics.record_hard_exclusion(evaluation.matched_exclusion)
        elif evaluation.rejection_reason == "CITIZENSHIP_RESTRICTED":
            metrics.record_citizenship_exclusion(evaluation.matched_exclusion)
        elif evaluation.rejection_reason == "SENIORITY_MISMATCH":
            metrics.record_seniority_mismatch()
        else:
            # Discarded due to missing core DV/RISC-V keyword or low score
            metrics.record_low_score()
        return None

    # Step C: Curate Job
    metrics.record_curated(evaluation.category or "Unknown")

    return {
        "platform": str(item.get("site") or item.get("platform") or "web"),
        "job_url": url,
        "title": title,
        "company": str(item.get("company") or "Unknown").strip(),
        "location": str(item.get("location") or "Unspecified").strip(),
        "is_remote": bool(item.get("is_remote", False)),
        "date_posted": str(item.get("date_posted") or ""),
        "score": evaluation.score,
        "category": evaluation.category,
        "matched_keywords": evaluation.matched_keywords,
        "raw_description": description,
    }


def run_pipeline(args: argparse.Namespace) -> int:
    """Execute the end-to-end scrape, filter, deduplicate, rank, and report pipeline."""
    configure_logging(verbose=args.verbose)
    logger.info("Initializing Hardware DV Job Hunter...")

    # 1. Initialize SQLite Database & Deduplication Cache
    db = JobDatabase(args.db_path)
    existing_urls = db.get_existing_urls()
    logger.info("Loaded %d existing URLs from database '%s'", len(existing_urls), args.db_path)

    # 2. Pipeline Metrics and Live Processing State
    metrics = PipelineMetrics()
    curated_jobs: list[dict[str, Any]] = []
    seen_in_session: set[str] = set()
    interrupted = False

    def handle_item(item: dict[str, Any]) -> None:
        """Evaluate one raw listing, printing and persisting it immediately if curated."""
        curated_entry = process_item(item, existing_urls, seen_in_session, metrics, args)
        if curated_entry is not None:
            curated_jobs.append(curated_entry)
            print_live_job_hit(curated_entry)
            db.save_job(curated_entry)

    # 3. Acquire & Evaluate Job Listings (Scraper or Demo Dataset), streamed as they arrive
    try:
        if args.demo:
            logger.info("Running in DEMO mode with realistic hardware DV job listings.")
            for item in SAMPLE_JOBS:
                handle_item(item)
        else:
            queries = (
                [q.strip() for q in args.queries.split(",") if q.strip()]
                if args.queries
                else list(DEFAULT_QUERY_ROTATION)
            )
            sites = [s.strip().lower() for s in args.sites.split(",") if s.strip()]

            # --location (singular) is a legacy override that replaces the full
            # --locations list with a single target (e.g. "Remote").
            locations = (
                [args.location]
                if args.location
                else [l.strip() for l in args.locations.split(";") if l.strip()]
            )

            scraper_config = ScraperConfig(
                queries=queries,
                sites=sites,
                locations=locations,
                distance=args.distance,
                hours_old=args.hours_old,
                is_remote=args.is_remote,
                results_wanted=args.results_wanted,
                linkedin_fetch_description=True,
            )

            scraper = JobScraper(scraper_config)
            for _query, _location, batch_df in scraper.scrape_all_iter():
                if batch_df.empty:
                    continue
                for item in batch_df.to_dict(orient="records"):
                    handle_item(item)
    except KeyboardInterrupt:
        interrupted = True
        logger.warning(
            "Interrupted mid-run: exporting the %d job(s) curated so far before exiting.",
            len(curated_jobs),
        )

    if not curated_jobs:
        logger.info("No newly curated jobs met the threshold during this run.")

    # 5. Export Output Reports
    csv_file, md_file = generate_filenames(output_dir=args.output_dir)
    export_to_csv(curated_jobs, csv_file)
    export_to_markdown(curated_jobs, md_file)

    # 6. Terminal Summary Funnel
    print_terminal_funnel(metrics, csv_file=csv_file, md_file=md_file)

    return 130 if interrupted else 0


def main() -> None:
    """CLI application entry point."""
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        sys.exit(run_pipeline(args))
    except KeyboardInterrupt:
        print("\n[!] Execution interrupted by user. Exiting gracefully.", file=sys.stderr)
        sys.exit(130)
    except Exception as err:
        logger.error("Fatal error during pipeline execution: %s", err, exc_info=args.verbose)
        sys.exit(1)


if __name__ == "__main__":
    main()
