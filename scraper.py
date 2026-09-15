"""Scraping engine for hardware verification and FPGA accelerator job listings.

Leverages `python-jobspy` across LinkedIn, Indeed, and Glassdoor with
query rotation, rate-limit resilience, and full description extraction.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Generator, Optional
import pandas as pd
from pydantic import BaseModel, Field

try:
    from jobspy import scrape_jobs
    from jobspy.model import Country
except ImportError:
    scrape_jobs = None
    Country = None

logger = logging.getLogger(__name__)

# Default query rotation specified in project requirements
DEFAULT_QUERY_ROTATION: list[str] = [
    "Design Verification Engineer",
    "DV Engineer",
    "RISC-V Verification Engineer",
    "CPU Verification Engineer",
    "ASIC Verification Engineer",
    "FPGA Verification Engineer",
    "Digital Design Engineer",
    "Hardware Engineer",
    "Junior Verification Engineer",
    "Verification Engineer",
]

DEFAULT_PLATFORMS: list[str] = ["linkedin", "indeed", "glassdoor", "zip_recruiter", "google"]

# Candidate's target job market: Canada (Toronto/Montreal/Ottawa/Vancouver) + France (Paris/Lyon)
DEFAULT_LOCATIONS: list[str] = [
    "Toronto, Canada",
    "Montreal, Canada",
    "Ottawa, Canada",
    "Vancouver, Canada",
    "Paris, France",
    "Lyon, France",
]


class ScraperConfig(BaseModel):
    """Configuration model for job scraping runs."""

    queries: list[str] = Field(default_factory=lambda: list(DEFAULT_QUERY_ROTATION))
    sites: list[str] = Field(default_factory=lambda: list(DEFAULT_PLATFORMS))
    locations: list[str] = Field(default_factory=lambda: list(DEFAULT_LOCATIONS))
    distance: Optional[int] = None
    hours_old: int = 72
    is_remote: bool = False
    results_wanted: int = 50
    linkedin_fetch_description: bool = True
    delay_between_queries: float = 2.0


def detect_indeed_country(location: Optional[str]) -> str:
    """Infer country_indeed parameter from a location string if possible."""
    if not location or Country is None:
        return "usa"

    loc_lower = location.lower()
    for name, member in Country.__members__.items():
        country_slug = member.value[0].lower()
        if country_slug in loc_lower or name.lower() in loc_lower:
            return country_slug

    return "usa"


class JobScraper:
    """High-level scraper executing query rotation across multiple job platforms and locations."""

    def __init__(self, config: Optional[ScraperConfig] = None) -> None:
        self.config = config or ScraperConfig()

    def scrape_single_query(self, query: str, location: Optional[str]) -> pd.DataFrame:
        """Execute a scrape for a single (query, location) pair across configured platforms."""
        if scrape_jobs is None:
            raise RuntimeError(
                "python-jobspy is not installed. Please install requirements first."
            )

        kwargs: dict[str, Any] = {
            "site_name": self.config.sites,
            "search_term": query,
            "location": location,
            "results_wanted": self.config.results_wanted,
            "hours_old": self.config.hours_old,
            "is_remote": self.config.is_remote,
            "linkedin_fetch_description": self.config.linkedin_fetch_description,
        }

        if self.config.distance is not None:
            kwargs["distance"] = self.config.distance

        country_indeed = detect_indeed_country(location)
        if country_indeed:
            kwargs["country_indeed"] = country_indeed

        logger.info(
            "Scraping query: '%s' | Sites: %s | Location: %s | Remote: %s",
            query,
            self.config.sites,
            location or "Global/Any",
            self.config.is_remote,
        )

        try:
            df = scrape_jobs(**kwargs)
            if df is None or df.empty:
                logger.warning("No listings returned for query '%s' in '%s'", query, location)
                return pd.DataFrame()

            logger.info("Found %d raw listings for query '%s' in '%s'", len(df), query, location)
            return df
        except Exception as e:
            logger.error(
                "Error scraping query '%s' in '%s': %s",
                query,
                location,
                e,
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )
            return pd.DataFrame()

    def scrape_all_iter(
        self,
    ) -> Generator[tuple[str, Optional[str], pd.DataFrame], None, None]:
        """Iterate through queries x locations, yielding each batch as it completes.

        Enables streaming/live consumption (e.g. filtering and printing curated hits
        as they're found) instead of waiting for the entire multi-query, multi-location
        scrape to finish before any results are visible.
        """
        locations: list[Optional[str]] = list(self.config.locations) or [None]
        total_runs = len(self.config.queries) * len(locations)
        run_idx = 0

        for query in self.config.queries:
            for location in locations:
                run_idx += 1
                logger.info("[%d/%d] Executing search for: '%s' in '%s'", run_idx, total_runs, query, location)
                df = self.scrape_single_query(query, location)
                yield query, location, df

                if run_idx < total_runs and self.config.delay_between_queries > 0:
                    time.sleep(self.config.delay_between_queries)

    def scrape_all(self) -> pd.DataFrame:
        """Non-streaming convenience wrapper: run scrape_all_iter to completion and
        return one aggregated, deduplicated DataFrame."""
        all_dfs = [df for _, _, df in self.scrape_all_iter() if not df.empty]

        if not all_dfs:
            logger.warning("Scraping completed with zero results across all queries/locations.")
            return pd.DataFrame()

        combined_df = pd.concat(all_dfs, ignore_index=True)

        # In-memory deduplication on job_url across different search queries in this run
        if "job_url" in combined_df.columns:
            initial_count = len(combined_df)
            combined_df = combined_df.drop_duplicates(subset=["job_url"], keep="first")
            dropped = initial_count - len(combined_df)
            if dropped > 0:
                logger.info("Deduplicated %d cross-query duplicate URLs in memory.", dropped)

        logger.info("Total aggregated unique listings from scrape: %d", len(combined_df))
        return combined_df
