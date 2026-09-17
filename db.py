"""Database persistence and deduplication module for job postings.

Manages SQLite storage, table initialization, and URL-based deduplication
for hardware verification and accelerator design job leads.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Iterable, Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "jobs.db"

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT,
    job_url TEXT UNIQUE NOT NULL,
    title TEXT,
    company TEXT,
    location TEXT,
    is_remote INTEGER DEFAULT 0,
    date_posted TEXT,
    score REAL,
    category TEXT,
    matched_keywords TEXT,
    raw_description TEXT,
    min_amount REAL,
    max_amount REAL,
    currency TEXT,
    salary_interval TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_jobs_job_url ON jobs(job_url);
CREATE INDEX IF NOT EXISTS idx_jobs_category ON jobs(category);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC);
"""

# Columns added after the initial schema - applied via ALTER TABLE for databases that
# already exist on disk, since CREATE TABLE IF NOT EXISTS is a no-op on those.
_MIGRATION_COLUMNS: dict[str, str] = {
    "min_amount": "REAL",
    "max_amount": "REAL",
    "currency": "TEXT",
    "salary_interval": "TEXT",
}


class JobDatabase:
    """Encapsulates SQLite database operations for job posting persistence and deduplication."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.init_db()

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Provide a transactional database connection scope."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        """Initialize database tables and indexes if they do not exist, then apply
        any column migrations needed by a pre-existing database file."""
        with self.get_connection() as conn:
            conn.executescript(CREATE_TABLES_SQL)
            self._apply_migrations(conn)
        logger.debug("Database initialized at %s", self.db_path)

    def _apply_migrations(self, conn: sqlite3.Connection) -> None:
        """Add any columns from _MIGRATION_COLUMNS missing from an existing jobs table."""
        existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        for column, sql_type in _MIGRATION_COLUMNS.items():
            if column not in existing_columns:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {sql_type}")
                logger.info("Migrated database '%s': added column '%s'", self.db_path, column)

    def get_existing_urls(self) -> set[str]:
        """Fetch all stored job URLs for fast O(1) in-memory deduplication."""
        query = "SELECT job_url FROM jobs WHERE job_url IS NOT NULL"
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            urls = {row["job_url"] for row in cursor.fetchall()}
        return urls

    def url_exists(self, job_url: str) -> bool:
        """Check if a single job URL already exists in the database."""
        if not job_url:
            return False
        query = "SELECT 1 FROM jobs WHERE job_url = ? LIMIT 1"
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (job_url,))
            return cursor.fetchone() is not None

    def save_job(self, job_data: dict[str, Any]) -> bool:
        """Insert a curated job into the database.

        Returns True if inserted, False if already exists (skipped).
        """
        insert_sql = """
        INSERT OR IGNORE INTO jobs (
            platform, job_url, title, company, location,
            is_remote, date_posted, score, category,
            matched_keywords, raw_description,
            min_amount, max_amount, currency, salary_interval
        ) VALUES (
            :platform, :job_url, :title, :company, :location,
            :is_remote, :date_posted, :score, :category,
            :matched_keywords, :raw_description,
            :min_amount, :max_amount, :currency, :salary_interval
        )
        """
        is_remote_val = 1 if job_data.get("is_remote") else 0
        matched_kw = job_data.get("matched_keywords", "")
        if isinstance(matched_kw, (list, set, tuple)):
            import json
            matched_kw = json.dumps(list(matched_kw))

        params = {
            "platform": str(job_data.get("platform") or job_data.get("site") or ""),
            "job_url": str(job_data.get("job_url") or job_data.get("url") or "").strip(),
            "title": str(job_data.get("title") or "").strip(),
            "company": str(job_data.get("company") or "").strip(),
            "location": str(job_data.get("location") or "").strip(),
            "is_remote": is_remote_val,
            "date_posted": str(job_data.get("date_posted") or ""),
            "score": float(job_data.get("score", 0.0)),
            "category": str(job_data.get("category") or ""),
            "matched_keywords": matched_kw,
            "raw_description": str(job_data.get("raw_description") or job_data.get("description") or ""),
            "min_amount": job_data.get("min_amount"),
            "max_amount": job_data.get("max_amount"),
            "currency": job_data.get("currency"),
            "salary_interval": job_data.get("salary_interval"),
        }

        if not params["job_url"]:
            logger.warning("Skipping job insertion due to empty job_url: %s", params["title"])
            return False

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(insert_sql, params)
            inserted = cursor.rowcount > 0
            if inserted:
                logger.debug("Saved job to DB: %s (%s)", params["title"], params["job_url"])
            else:
                logger.debug("Job URL already exists, skipped: %s", params["job_url"])
            return inserted

    def save_jobs_batch(self, jobs: Iterable[dict[str, Any]]) -> int:
        """Insert a collection of curated jobs in a single transaction.

        Returns the number of newly inserted jobs.
        """
        count = 0
        with self.get_connection() as conn:
            cursor = conn.cursor()
            insert_sql = """
            INSERT OR IGNORE INTO jobs (
                platform, job_url, title, company, location,
                is_remote, date_posted, score, category,
                matched_keywords, raw_description,
                min_amount, max_amount, currency, salary_interval
            ) VALUES (
                :platform, :job_url, :title, :company, :location,
                :is_remote, :date_posted, :score, :category,
                :matched_keywords, :raw_description,
                :min_amount, :max_amount, :currency, :salary_interval
            )
            """
            for job in jobs:
                is_remote_val = 1 if job.get("is_remote") else 0
                matched_kw = job.get("matched_keywords", "")
                if isinstance(matched_kw, (list, set, tuple)):
                    import json
                    matched_kw = json.dumps(list(matched_kw))

                job_url = str(job.get("job_url") or job.get("url") or "").strip()
                if not job_url:
                    continue

                params = {
                    "platform": str(job.get("platform") or job.get("site") or ""),
                    "job_url": job_url,
                    "title": str(job.get("title") or "").strip(),
                    "company": str(job.get("company") or "").strip(),
                    "location": str(job.get("location") or "").strip(),
                    "is_remote": is_remote_val,
                    "date_posted": str(job.get("date_posted") or ""),
                    "score": float(job.get("score", 0.0)),
                    "category": str(job.get("category") or ""),
                    "matched_keywords": matched_kw,
                    "raw_description": str(job.get("raw_description") or job.get("description") or ""),
                    "min_amount": job.get("min_amount"),
                    "max_amount": job.get("max_amount"),
                    "currency": job.get("currency"),
                    "salary_interval": job.get("salary_interval"),
                }
                cursor.execute(insert_sql, params)
                if cursor.rowcount > 0:
                    count += 1

        logger.info("Persisted %d new jobs to database (%s)", count, self.db_path)
        return count

    def get_all_curated_jobs(self) -> list[dict[str, Any]]:
        """Retrieve all curated jobs sorted descending by score."""
        query = """
        SELECT id, platform, job_url, title, company, location,
               is_remote, date_posted, score, category, matched_keywords, raw_description,
               min_amount, max_amount, currency, salary_interval
        FROM jobs
        ORDER BY score DESC, id DESC
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            return [dict(row) for row in cursor.fetchall()]

    def count_jobs(self) -> int:
        """Count total jobs stored in the database."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM jobs")
            row = cursor.fetchone()
            return int(row[0]) if row else 0


# Convenience functions
def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> JobDatabase:
    """Initialize database and return a JobDatabase instance."""
    return JobDatabase(db_path)


def get_existing_urls(db_path: str | Path = DEFAULT_DB_PATH) -> set[str]:
    """Retrieve existing URLs from SQLite database."""
    return JobDatabase(db_path).get_existing_urls()
