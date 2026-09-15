"""Comprehensive test suite for the Hardware DV Job Hunter application."""

from __future__ import annotations

import csv
import os
import shutil
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
)
from reporter import (
    CATEGORY_ORDER,
    PipelineMetrics,
    export_to_csv,
    export_to_markdown,
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
            }
        ]
        export_to_csv(jobs, self.csv_path)

        with open(self.csv_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            expected_headers = [
                "Title", "Company", "Location", "Remote",
                "Score", "Category", "Matched Keywords", "URL"
            ]
            self.assertEqual(reader.fieldnames, expected_headers)
            rows = list(reader)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["Title"], "RISC-V DV Lead")
            self.assertEqual(row["Company"], "SiFive")
            self.assertEqual(row["Remote"], "Yes")
            self.assertEqual(row["Score"], "18.0")
            self.assertEqual(row["Category"], CAT_RISCV)
            self.assertIn("risc-v", row["Matched Keywords"])

    def test_export_to_markdown_category_grouping_and_ordering(self):
        """Verify Markdown groups jobs with RISC-V first and sorts descending by score."""
        jobs = [
            {
                "title": "FPGA Accelerator",
                "company": "AMD",
                "score": 12.0,
                "category": CAT_FPGA,
                "job_url": "https://example.com/fpga",
            },
            {
                "title": "RISC-V Core Verifier",
                "company": "Tenstorrent",
                "score": 25.0,
                "category": CAT_RISCV,
                "job_url": "https://example.com/riscv",
            },
            {
                "title": "ASIC Verification Engineer",
                "company": "Qualcomm",
                "score": 15.0,
                "category": CAT_ASIC_UVM,
                "job_url": "https://example.com/asic",
            },
        ]
        export_to_markdown(jobs, self.md_path)

        with open(self.md_path, mode="r", encoding="utf-8") as f:
            content = f.read()

        # RISC-V section must appear before ASIC DV, which must appear before FPGA
        idx_riscv = content.find(f"## {CAT_RISCV}")
        idx_asic = content.find(f"## {CAT_ASIC_UVM}")
        idx_fpga = content.find(f"## {CAT_FPGA}")

        self.assertNotEqual(idx_riscv, -1)
        self.assertNotEqual(idx_asic, -1)
        self.assertNotEqual(idx_fpga, -1)
        self.assertTrue(idx_riscv < idx_asic < idx_fpga)


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
