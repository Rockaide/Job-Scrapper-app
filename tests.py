"""Comprehensive test suite for the Hardware DV Job Hunter application."""

from __future__ import annotations

import csv
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from db import JobDatabase
from filter import (
    CAT_ASIC_UVM,
    CAT_FPGA,
    CAT_GENERAL,
    CAT_RISCV,
    DEFAULT_MIN_SCORE,
    HARD_EXCLUSIONS_LIST,
    WORK_AUTH_EXCLUSIONS_LIST,
    classify_seniority,
    evaluate_job,
    extract_years_required,
)
from reporter import (
    CATEGORY_ORDER,
    PipelineMetrics,
    export_to_csv,
    export_to_markdown,
    extract_country,
    extract_requirements,
    format_salary,
)
from scraper import DEFAULT_QUERY_ROTATION, ScraperConfig


class TestFilterEngine(unittest.TestCase):
    """Tests for rule-based keyword extraction, scoring, and domain categorization."""

    def test_hard_exclusions_all_keywords(self):
        """Verify that every defined hard exclusion keyword triggers an immediate discard."""
        for kw in HARD_EXCLUSIONS_LIST:
            desc = f"Looking for an experienced candidate with hands-on skills in {kw} for test automation."
            result = evaluate_job(title="Software Engineer", description=desc)
            self.assertFalse(
                result.passed,
                f"Keyword '{kw}' should have triggered hard exclusion rejection.",
            )
            self.assertEqual(result.rejection_reason, "HARD_EXCLUSION")

    def test_exclusion_boundary_precision(self):
        """Ensure substring boundaries prevent false-positive rejections."""
        # 'reaction' should not trigger 'react'
        res = evaluate_job(
            title="Senior DV Engineer",
            description="Speed of reaction is important when running UVM regressions in VCS with SystemVerilog.",
        )
        self.assertTrue(res.passed)
        self.assertNotEqual(res.rejection_reason, "HARD_EXCLUSION")

    def test_domain_keyword_boundary_precision(self):
        """Ensure boundary matching avoids false-positive keyword matches."""
        res = evaluate_job(
            title="General Engineer",
            description="We investigate complex issues at maximum clock rates in our society.",
        )
        # 'issue' != 'iss', 'maximum' != 'axi', 'society' != 'soc'
        self.assertNotIn("iss", res.matched_keywords)
        self.assertNotIn("axi", res.matched_keywords)
        self.assertNotIn("soc", res.matched_keywords)
        self.assertFalse(res.passed)

    def test_must_match_core_riscv_or_standard_dv(self):
        """Verify that jobs matching only FPGA keywords are rejected."""
        res = evaluate_job(
            title="FPGA Engineer",
            description="Developing FPGA bitstreams using Vivado, Zynq, AXI, and DMA with VHDL.",
        )
        self.assertFalse(res.passed)
        self.assertEqual(res.rejection_reason, "NO_CORE_DV_OR_RISCV_KEYWORD")

    def test_minimum_score_threshold(self):
        """Verify that postings with score < min_score are rejected."""
        # 1 RISC-V keyword = 5 points < 8 default
        res = evaluate_job(
            title="Architect",
            description="Knowledge of RISC-V instruction sets.",
            min_score=8,
        )
        self.assertFalse(res.passed)
        self.assertEqual(res.rejection_reason, "LOW_SCORE")
        self.assertEqual(res.score, 5.0)

    def test_weighted_scoring_accuracy(self):
        """Verify weights: Core RISC-V (+5), Standard DV (+3), FPGA (+2)."""
        # RISC-V (5) + Spike (5) + UVM (3) + AXI (2) = 15
        res = evaluate_job(
            title="RISC-V DV Lead",
            description="Experience with RISC-V cores, Spike simulator, UVM testbenches, and AXI interconnect.",
        )
        self.assertTrue(res.passed)
        self.assertEqual(res.score, 15.0)
        self.assertEqual(res.category, CAT_RISCV)

    def test_category_assignment_riscv(self):
        """Verify primary domain category prioritizes RISC-V when RISC-V keywords are matched."""
        res = evaluate_job(
            title="RISC-V Verification Engineer",
            description="UVM testbenches for RISC-V processors using step-and-compare co-simulation.",
        )
        self.assertTrue(res.passed)
        self.assertEqual(res.category, CAT_RISCV)

    def test_category_assignment_asic_dv(self):
        """Verify ASIC / UVM DV categorization when standard DV dominates."""
        res = evaluate_job(
            title="ASIC DV Engineer",
            description="UVM, SystemVerilog, constrained-random testing, VCS, and SVA assertions.",
        )
        self.assertTrue(res.passed)
        self.assertEqual(res.category, CAT_ASIC_UVM)

    def test_category_assignment_fpga_accelerator(self):
        """Verify FPGA / Accelerator categorization when FPGA/accelerator keywords dominate."""
        res = evaluate_job(
            title="Hardware Accelerator DV",
            description="UVM verification for Vivado, Zynq, AXI, AXI-stream, and DMA hardware accelerators.",
        )
        self.assertTrue(res.passed)
        self.assertEqual(res.category, CAT_FPGA)

    def test_general_category_accepts_broader_digital_design_roles(self):
        """Verify a posting with only general digital-design keywords now passes."""
        res = evaluate_job(
            title="Digital Design Engineer",
            description=(
                "Join our ASIC team doing digital design and logic design in Verilog. "
                "Familiarity with Cadence and Synopsys toolchains, testbench development, "
                "and chip design for our next-generation semiconductor products."
            ),
        )
        self.assertTrue(res.passed)
        self.assertEqual(res.category, CAT_GENERAL)

    def test_fpga_only_still_insufficient_without_general_keywords(self):
        """Verify FPGA-only postings without any general/DV/RISC-V keyword still fail."""
        res = evaluate_job(
            title="FPGA Engineer",
            description="Developing FPGA bitstreams using Vivado, Zynq, AXI, and DMA with VHDL.",
        )
        self.assertFalse(res.passed)
        self.assertEqual(res.rejection_reason, "NO_CORE_DV_OR_RISCV_KEYWORD")

    def test_seniority_classification(self):
        """Verify title-based seniority classification."""
        self.assertEqual(classify_seniority("Senior RISC-V Verification Engineer"), "Senior+")
        self.assertEqual(classify_seniority("Staff DV Engineer"), "Senior+")
        self.assertEqual(classify_seniority("Junior Digital Design Engineer"), "Junior/Entry")
        self.assertEqual(classify_seniority("New Grad Verification Engineer"), "Junior/Entry")
        self.assertEqual(classify_seniority("DV Engineer"), "Unspecified")

    def test_exclude_senior_titles_drops_senior_postings(self):
        """Verify exclude_senior_titles=True rejects Senior+ titles regardless of score."""
        res = evaluate_job(
            title="Senior RISC-V Core Verification Engineer",
            description="RISC-V, UVM, SystemVerilog, Spike ISS, step-and-compare co-simulation.",
            exclude_senior_titles=True,
        )
        self.assertFalse(res.passed)
        self.assertEqual(res.rejection_reason, "SENIORITY_MISMATCH")

    def test_exclude_senior_titles_off_by_default(self):
        """Verify default behavior (exclude_senior_titles=False) still admits Senior+ titles."""
        res = evaluate_job(
            title="Senior RISC-V Core Verification Engineer",
            description="RISC-V, UVM, SystemVerilog, Spike ISS, step-and-compare co-simulation.",
        )
        self.assertTrue(res.passed)
        self.assertEqual(res.seniority, "Senior+")

    def test_work_auth_exclusions_all_keywords(self):
        """Verify every work-authorization exclusion keyword triggers a discard when opted in."""
        for kw in WORK_AUTH_EXCLUSIONS_LIST:
            desc = f"RISC-V, UVM, SystemVerilog verification role. Note: {kw} is required for this position."
            result = evaluate_job(
                title="DV Engineer",
                description=desc,
                exclude_citizenship_restricted=True,
            )
            self.assertFalse(
                result.passed,
                f"Keyword '{kw}' should have triggered citizenship/work-auth rejection.",
            )
            self.assertEqual(result.rejection_reason, "CITIZENSHIP_RESTRICTED")

    def test_citizenship_restriction_off_by_default(self):
        """Verify default behavior (exclude_citizenship_restricted=False) still admits these postings."""
        res = evaluate_job(
            title="DV Engineer",
            description="RISC-V, UVM, SystemVerilog. Active security clearance required.",
        )
        self.assertTrue(res.passed)

    def test_citizenship_restriction_does_not_affect_ordinary_postings(self):
        """Verify a posting with no citizenship/sponsorship language is unaffected."""
        res = evaluate_job(
            title="DV Engineer",
            description="RISC-V, UVM, SystemVerilog verification testbenches.",
            exclude_citizenship_restricted=True,
        )
        self.assertTrue(res.passed)

    def test_extract_years_required_basic_phrasings(self):
        """Verify years-of-experience extraction across common phrasings."""
        self.assertEqual(extract_years_required("5+ years of experience required."), 5)
        self.assertEqual(extract_years_required("3-5 years of relevant experience."), 3)
        self.assertEqual(extract_years_required("3 to 5 years of experience."), 3)
        self.assertEqual(extract_years_required("Minimum of 5 years experience."), 5)
        self.assertEqual(extract_years_required("At least 8 years of professional experience."), 8)

    def test_extract_years_required_takes_highest_mention(self):
        """Verify the highest stated experience floor wins when several are mentioned."""
        text = "2+ years of Python experience. 6+ years of RTL design experience required."
        self.assertEqual(extract_years_required(text), 6)

    def test_extract_years_required_avoids_unrelated_year_mentions(self):
        """Verify plain 'N years' mentions unrelated to experience are not matched."""
        self.assertIsNone(extract_years_required("We've been in business for 10+ years."))
        self.assertIsNone(extract_years_required("Supporting this product line for 15 years."))
        self.assertIsNone(extract_years_required(""))
        self.assertIsNone(extract_years_required(None))

    def test_exclude_high_experience_drops_postings_at_threshold(self):
        """Verify exclude_high_experience=True rejects postings requiring >= max_years_required."""
        res = evaluate_job(
            title="DV Engineer",
            description="RISC-V, UVM, SystemVerilog. 5+ years of experience required.",
            exclude_high_experience=True,
            max_years_required=5,
        )
        self.assertFalse(res.passed)
        self.assertEqual(res.rejection_reason, "EXPERIENCE_MISMATCH")
        self.assertEqual(res.years_required, 5)

    def test_exclude_high_experience_admits_postings_below_threshold(self):
        """Verify a posting requiring fewer years than the threshold still passes."""
        res = evaluate_job(
            title="DV Engineer",
            description="RISC-V, UVM, SystemVerilog. 2+ years of experience preferred.",
            exclude_high_experience=True,
            max_years_required=5,
        )
        self.assertTrue(res.passed)
        self.assertEqual(res.years_required, 2)

    def test_exclude_high_experience_off_by_default(self):
        """Verify default behavior (exclude_high_experience=False) still admits these postings."""
        res = evaluate_job(
            title="DV Engineer",
            description="RISC-V, UVM, SystemVerilog. 10+ years of experience required.",
        )
        self.assertTrue(res.passed)
        self.assertEqual(res.years_required, 10)


class TestDatabasePersistence(unittest.TestCase):
    """Tests for SQLite persistence, schema, and deduplication."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = Path(self.test_dir) / "test.db"
        self.db = JobDatabase(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_table_schema_and_initialization(self):
        """Verify table exists and contains all required columns."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(jobs)")
            cols = {row["name"] for row in cursor.fetchall()}

        expected_columns = {
            "id", "platform", "job_url", "title", "company",
            "location", "is_remote", "date_posted", "score",
            "category", "matched_keywords", "raw_description",
        }
        self.assertTrue(expected_columns.issubset(cols))

    def test_save_and_skip_duplicate_urls(self):
        """Verify that duplicate job_urls are skipped and not stored twice."""
        job = {
            "platform": "linkedin",
            "job_url": "https://linkedin.com/jobs/view/999",
            "title": "DV Engineer",
            "company": "ChipCorp",
            "location": "Remote",
            "is_remote": True,
            "date_posted": "2026-09-14",
            "score": 10.0,
            "category": CAT_ASIC_UVM,
            "matched_keywords": ["uvm", "vcs"],
            "raw_description": "UVM description",
        }

        # First insert succeeds
        self.assertTrue(self.db.save_job(job))
        self.assertEqual(self.db.count_jobs(), 1)
        self.assertTrue(self.db.url_exists("https://linkedin.com/jobs/view/999"))

        # Second insert with identical URL is skipped
        self.assertFalse(self.db.save_job(job))
        self.assertEqual(self.db.count_jobs(), 1)

    def test_batch_insert(self):
        """Verify batch inserting jobs."""
        jobs = [
            {
                "platform": "indeed",
                "job_url": f"https://indeed.com/viewjob?jk={i}",
                "title": f"Engineer {i}",
                "score": 10.0 + i,
            }
            for i in range(5)
        ]
        inserted = self.db.save_jobs_batch(jobs)
        self.assertEqual(inserted, 5)
        self.assertEqual(self.db.count_jobs(), 5)

    def test_save_job_round_trips_salary_fields(self):
        """Verify salary/compensation fields are persisted and read back correctly."""
        job = {
            "job_url": "https://linkedin.com/jobs/view/555",
            "title": "DV Engineer",
            "score": 10.0,
            "min_amount": 90000.0,
            "max_amount": 110000.0,
            "currency": "CAD",
            "salary_interval": "yearly",
        }
        self.assertTrue(self.db.save_job(job))
        stored = self.db.get_all_curated_jobs()[0]
        self.assertEqual(stored["min_amount"], 90000.0)
        self.assertEqual(stored["max_amount"], 110000.0)
        self.assertEqual(stored["currency"], "CAD")
        self.assertEqual(stored["salary_interval"], "yearly")

    def test_migration_adds_salary_columns_to_preexisting_database(self):
        """Verify a database created under the old schema (no salary columns) gets
        migrated safely, without losing existing data, when opened by JobDatabase."""
        legacy_db_path = Path(self.test_dir) / "legacy.db"
        conn = sqlite3.connect(str(legacy_db_path))
        conn.execute(
            """
            CREATE TABLE jobs (
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO jobs (platform, job_url, title, score) VALUES (?, ?, ?, ?)",
            ("linkedin", "https://linkedin.com/jobs/view/111", "Legacy DV Engineer", 12.0),
        )
        conn.commit()
        conn.close()

        migrated_db = JobDatabase(legacy_db_path)
        with migrated_db.get_connection() as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        self.assertIn("min_amount", cols)
        self.assertIn("max_amount", cols)
        self.assertIn("currency", cols)
        self.assertIn("salary_interval", cols)

        # Pre-existing row must survive the migration untouched
        self.assertEqual(migrated_db.count_jobs(), 1)
        stored = migrated_db.get_all_curated_jobs()[0]
        self.assertEqual(stored["title"], "Legacy DV Engineer")
        self.assertIsNone(stored["min_amount"])


class TestReporter(unittest.TestCase):
    """Tests for CSV and Markdown reporting."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.csv_path = Path(self.test_dir) / "test_report.csv"
        self.md_path = Path(self.test_dir) / "test_report.md"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_export_to_csv_columns_and_data(self):
        """Verify CSV export contains specified headers and correct field values."""
        jobs = [
            {
                "title": "RISC-V DV Lead",
                "company": "SiFive",
                "location": "Campinas, Brazil",
                "is_remote": True,
                "score": 18.0,
                "category": CAT_RISCV,
                "matched_keywords": ["risc-v", "spike", "uvm"],
                "job_url": "https://linkedin.com/jobs/view/777",
                "min_amount": 120000,
                "max_amount": 150000,
                "currency": "USD",
                "salary_interval": "yearly",
            },
            {
                "title": "DV Engineer",
                "company": "NoSalaryCorp",
                "job_url": "https://linkedin.com/jobs/view/778",
                "score": 10.0,
                "category": CAT_RISCV,
            },
        ]
        export_to_csv(jobs, self.csv_path)

        with open(self.csv_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            expected_headers = [
                "Title", "Company", "Location", "Remote", "Salary",
                "Score", "Category", "Matched Keywords", "URL"
            ]
            self.assertEqual(reader.fieldnames, expected_headers)
            rows = list(reader)
            self.assertEqual(len(rows), 2)
            row = rows[0]
            self.assertEqual(row["Title"], "RISC-V DV Lead")
            self.assertEqual(row["Company"], "SiFive")
            self.assertEqual(row["Remote"], "Yes")
            self.assertEqual(row["Salary"], "USD 120,000 - 150,000/yr")
            self.assertEqual(row["Score"], "18.0")
            self.assertEqual(row["Category"], CAT_RISCV)
            self.assertIn("risc-v", row["Matched Keywords"])
            self.assertEqual(rows[1]["Salary"], "")

    def test_export_to_markdown_country_grouping_and_ordering(self):
        """Verify Markdown groups jobs by country (target-market order) and sorts
        descending by score within each country."""
        jobs = [
            {
                "title": "FPGA Accelerator",
                "company": "AMD",
                "location": "Lyon, France",
                "score": 12.0,
                "category": CAT_FPGA,
                "job_url": "https://example.com/fpga",
            },
            {
                "title": "RISC-V Core Verifier (Toronto)",
                "company": "Tenstorrent",
                "location": "Toronto, ON, Canada",
                "score": 25.0,
                "category": CAT_RISCV,
                "job_url": "https://example.com/riscv-to",
            },
            {
                "title": "RISC-V Core Verifier (Mississauga)",
                "company": "Tenstorrent",
                "location": "Mississauga, ON",  # no country name -> city fallback
                "score": 18.0,
                "category": CAT_RISCV,
                "job_url": "https://example.com/riscv-miss",
            },
            {
                "title": "ASIC Verification Engineer",
                "company": "Qualcomm",
                "location": "Barcelona, Spain",
                "score": 15.0,
                "category": CAT_ASIC_UVM,
                "job_url": "https://example.com/asic",
            },
        ]
        export_to_markdown(jobs, self.md_path)

        with open(self.md_path, mode="r", encoding="utf-8") as f:
            content = f.read()

        # Country sections must appear in target-market priority order: Canada, France, Spain
        idx_canada = content.find("## Canada")
        idx_france = content.find("## France")
        idx_spain = content.find("## Spain")

        self.assertNotEqual(idx_canada, -1)
        self.assertNotEqual(idx_france, -1)
        self.assertNotEqual(idx_spain, -1)
        self.assertTrue(idx_canada < idx_france < idx_spain)

        # Within Canada, the higher-scoring Toronto posting must appear before Mississauga
        idx_toronto_job = content.find("RISC-V Core Verifier (Toronto)")
        idx_mississauga_job = content.find("RISC-V Core Verifier (Mississauga)")
        self.assertTrue(idx_canada < idx_toronto_job < idx_mississauga_job)

    def test_extract_country_from_explicit_name(self):
        """Verify country extraction when the location string names the country directly."""
        self.assertEqual(extract_country("Paris, France"), "France")
        self.assertEqual(extract_country("Geneva, Switzerland"), "Switzerland")
        self.assertEqual(extract_country("Barcelona, Spain"), "Spain")

    def test_extract_country_fallback_to_known_city(self):
        """Verify country extraction falls back to known target cities when the
        location string omits the country name."""
        self.assertEqual(extract_country("Mississauga, ON"), "Canada")
        self.assertEqual(extract_country("Eindhoven"), "Netherlands")

    def test_extract_country_remote_and_unknown(self):
        """Verify Remote and unrecognized locations are bucketed sensibly."""
        self.assertEqual(extract_country("Remote"), "Remote")
        self.assertEqual(extract_country("Austin, TX"), "Other / Unspecified")
        self.assertEqual(extract_country(""), "Other / Unspecified")

    def test_extract_requirements_from_labeled_section(self):
        """Verify requirements are pulled from a labeled section and stop at the next one."""
        description = """
We are seeking an experienced Senior RISC-V Core Verification Engineer.
Key Responsibilities:
- Architect and develop comprehensive UVM and SystemVerilog verification environments.
- Implement step-and-compare co-simulation using Spike ISS.
Qualifications:
- Deep expertise in RISC-V ISA, CPU verification, and lockstep execution models.
- Strong proficiency in UVM, SVA (SystemVerilog Assertions), and AXI interfaces.
Benefits:
- Competitive salary and equity.
- Fully remote work.
"""
        result = extract_requirements(description)
        self.assertEqual(
            result,
            [
                "Deep expertise in RISC-V ISA, CPU verification, and lockstep execution models.",
                "Strong proficiency in UVM, SVA (SystemVerilog Assertions), and AXI interfaces.",
            ],
        )

    def test_extract_requirements_handles_alternate_header_names(self):
        """Verify recognition of common header phrasing beyond the literal word 'Requirements'."""
        description = """
About the role: build next-gen FPGA accelerators.
What You'll Need:
- 2+ years of RTL design experience.
- Familiarity with Vivado and AXI-Stream.
About Us: we are a fast-growing startup.
"""
        result = extract_requirements(description)
        self.assertEqual(
            result,
            ["2+ years of RTL design experience.", "Familiarity with Vivado and AXI-Stream."],
        )

    def test_extract_requirements_empty_when_no_section_found(self):
        """Verify an unstructured posting with no recognizable section returns an empty list,
        so callers know to fall back to a generic excerpt."""
        description = "We build cool hardware. Come join our growing team in a fast-paced environment."
        self.assertEqual(extract_requirements(description), [])
        self.assertEqual(extract_requirements(""), [])

    def test_extract_requirements_caps_item_count_and_length(self):
        """Verify max_items and max_chars_per_item are respected."""
        bullets = "\n".join(f"- Requirement number {i} that is reasonably long" for i in range(20))
        description = f"Requirements:\n{bullets}\n"
        result = extract_requirements(description, max_items=3, max_chars_per_item=20)
        self.assertEqual(len(result), 3)
        for item in result:
            self.assertLessEqual(len(item), 23)  # 20 chars + "..."

    def test_format_salary_full_range(self):
        """Verify a full min/max salary range formats correctly."""
        job = {"min_amount": 120000, "max_amount": 150000, "currency": "USD", "salary_interval": "yearly"}
        self.assertEqual(format_salary(job), "USD 120,000 - 150,000/yr")

    def test_format_salary_min_only(self):
        """Verify a min-only salary is presented as a floor."""
        job = {"min_amount": 90000, "currency": "CAD", "salary_interval": "yearly"}
        self.assertEqual(format_salary(job), "CAD 90,000+/yr")

    def test_format_salary_max_only(self):
        """Verify a max-only salary is presented as a cap."""
        job = {"max_amount": 60000, "currency": "EUR", "salary_interval": "yearly"}
        self.assertEqual(format_salary(job), "Up to EUR 60,000/yr")

    def test_format_salary_hourly_uses_decimals(self):
        """Verify hourly rates render with two decimal places."""
        job = {"min_amount": 45.5, "max_amount": 60, "currency": "USD", "salary_interval": "hourly"}
        self.assertEqual(format_salary(job), "USD 45.50 - 60.00/hr")

    def test_format_salary_missing_returns_empty(self):
        """Verify no salary data yields an empty string, not a placeholder."""
        self.assertEqual(format_salary({}), "")
        self.assertEqual(format_salary({"min_amount": None, "max_amount": None}), "")


class TestScraperConfig(unittest.TestCase):
    """Tests for query rotation list and scraper configuration defaults."""

    def test_query_rotation_list(self):
        """Verify that default query rotation list contains all required search terms."""
        expected_queries = [
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
        self.assertEqual(DEFAULT_QUERY_ROTATION, expected_queries)

    def test_config_defaults(self):
        """Verify scraper configuration default parameters."""
        config = ScraperConfig()
        self.assertEqual(config.hours_old, 72)
        self.assertEqual(config.results_wanted, 50)
        self.assertFalse(config.is_remote)
        self.assertTrue(config.linkedin_fetch_description)


if __name__ == "__main__":
    unittest.main()
