"""Realistic hardware verification and accelerator job fixtures for testing and demo modes."""

from __future__ import annotations

SAMPLE_JOBS = [
    # 1. Target RISC-V Core Verification (High Score)
    {
        "site": "linkedin",
        "job_url": "https://www.linkedin.com/jobs/view/10001",
        "title": "Senior RISC-V Core Verification Engineer",
        "company": "Tenstorrent",
        "location": "Campinas, Brazil",
        "is_remote": True,
        "date_posted": "2026-09-14",
        "description": """
We are seeking an experienced Senior RISC-V Core Verification Engineer.
Key Responsibilities:
- Architect and develop comprehensive UVM and SystemVerilog verification environments for out-of-order RISC-V processors.
- Implement step-and-compare co-simulation using Spike ISS (instruction set simulator) and RVVI interfaces.
- Leverage riscv-dv and core-v-verif frameworks for constrained-random instruction generation and functional coverage.
- Work with formal verification and VCS / Xcelium simulators to close RTL coverage on pipeline and memory subsystems.
Qualifications:
- Deep expertise in RISC-V ISA, CPU verification, and lockstep execution models.
- Strong proficiency in UVM, SVA (SystemVerilog Assertions), and AXI interfaces.
""",
    },
    # 2. CPU / Processor Verification (High Score)
    {
        "site": "indeed",
        "job_url": "https://www.indeed.com/viewjob?jk=10002",
        "title": "CPU Verification Engineer - Microarchitecture",
        "company": "Ventana Micro Systems",
        "location": "Remote",
        "is_remote": True,
        "date_posted": "2026-09-13",
        "description": """
Join our high-performance processor verification team building server-class RISC-V cores.
Responsibilities:
- Drive processor verification from testplan definition to silicon tapeout.
- Develop instruction set simulator (ISS) reference models and lockstep co-simulation testbenches.
- Write constrained-random tests and systemverilog assertions for cache coherency and DMA controllers.
- Utilize Questa and VCS simulators to achieve 100% functional coverage.
Requirements:
- BS/MS in Electrical/Computer Engineering.
- Experience with risc-v, cpu verification, and modern UVM methodologies.
""",
    },
    # 3. Standard ASIC / UVM DV (High Score)
    {
        "site": "linkedin",
        "job_url": "https://www.linkedin.com/jobs/view/10003",
        "title": "Principal ASIC Design Verification Engineer",
        "company": "Qualcomm",
        "location": "San Diego, CA",
        "is_remote": False,
        "date_posted": "2026-09-12",
        "description": """
Looking for a Principal ASIC DV Engineer to lead verification for modern mobile SoCs.
Requirements:
- Proven track record with Universal Verification Methodology (UVM) and SystemVerilog.
- Expertise in constrained-random testbench development, functional coverage, and SVA.
- Hands-on experience with Cadence Xcelium and Synopsys VCS simulation tools.
- Experience with formal verification techniques to verify complex RTL protocols.
""",
    },
    # 4. FPGA & Hardware Accelerator (High Score)
    {
        "site": "glassdoor",
        "job_url": "https://www.glassdoor.com/job/10004",
        "title": "FPGA Hardware Accelerator Design & Verification Engineer",
        "company": "AMD / Xilinx",
        "location": "San Jose, CA",
        "is_remote": True,
        "date_posted": "2026-09-14",
        "description": """
We are looking for an FPGA Verification and Design Engineer to build AI/ML hardware accelerators.
Key Requirements:
- Hands-on development on AMD Xilinx Zynq UltraScale+ using Vivado and Vitis toolchains.
- Design and verification of high-throughput AXI and AXI-stream interconnects and DMA engines.
- Write RTL in SystemVerilog and VHDL for fixed-point math computing units.
- Verification using UVM testbenches and constrained-random stimulus generation.
""",
    },
    # 5. FPGA Design with UVM and SoC
    {
        "site": "indeed",
        "job_url": "https://www.indeed.com/viewjob?jk=10005",
        "title": "Senior FPGA SoC Verification Engineer",
        "company": "NVIDIA",
        "location": "Austin, TX",
        "is_remote": True,
        "date_posted": "2026-09-11",
        "description": """
Develop advanced FPGA emulation models and verification suites for next-gen SoC accelerators.
Requirements:
- Solid RTL verification background with UVM and SystemVerilog.
- Familiarity with Zynq, Vivado, AXI protocols, and DMA controllers.
- Experience running regressions with Xcelium or VCS.
""",
    },
    # 6. Hard Exclusion: Selenium & Cypress (Software QA)
    {
        "site": "linkedin",
        "job_url": "https://www.linkedin.com/jobs/view/10006",
        "title": "Senior QA Automation Engineer",
        "company": "Fintech Solutions",
        "location": "Remote",
        "is_remote": True,
        "date_posted": "2026-09-14",
        "description": """
We need a QA Automation Engineer to test our web banking portal.
Skills required:
- Automated web testing with Selenium, Cypress, and Playwright.
- API testing using Postman.
- Front-end familiarity with React and Angular applications.
""",
    },
    # 7. Hard Exclusion: Clinical Validation & FDA
    {
        "site": "indeed",
        "job_url": "https://www.indeed.com/viewjob?jk=10007",
        "title": "Medical Device Verification & Validation Engineer",
        "company": "BioMed Health",
        "location": "Boston, MA",
        "is_remote": False,
        "date_posted": "2026-09-10",
        "description": """
Seeking a Verification and Validation Engineer for Class III medical equipment.
Responsibilities:
- Execute clinical validation protocols adhering to FDA guidelines and GMP standards.
- Prepare documentation for medical device regulatory submissions.
""",
    },
    # 8. Hard Exclusion: Software QA / Manual Tester
    {
        "site": "glassdoor",
        "job_url": "https://www.glassdoor.com/job/10008",
        "title": "Software Quality Assurance Analyst",
        "company": "AppCraft",
        "location": "Remote",
        "is_remote": True,
        "date_posted": "2026-09-13",
        "description": """
Looking for a manual tester and software quality assurance analyst.
Must have experience executing manual test scripts and reporting bugs in Jira.
Familiarity with Figma and UI/UX flows is a strong plus.
""",
    },
    # 9. Low Score: FPGA only without Core RISC-V or Standard DV
    {
        "site": "linkedin",
        "job_url": "https://www.linkedin.com/jobs/view/10009",
        "title": "Junior FPGA Firmware Developer",
        "company": "EdgeDevices",
        "location": "Remote",
        "is_remote": True,
        "date_posted": "2026-09-14",
        "description": """
Junior position developing basic FPGA modules with Vivado and VHDL.
Basic understanding of RTL design and fixed-point numbers.
""",
    },
    # 10. Low Score: Core RISC-V mentioned but score < 8
    {
        "site": "indeed",
        "job_url": "https://www.indeed.com/viewjob?jk=10010",
        "title": "Embedded Software Developer",
        "company": "IoT Systems",
        "location": "Remote",
        "is_remote": True,
        "date_posted": "2026-09-14",
        "description": """
Writing C firmware for small RISC-V microcontrollers.
Must know C/C++ and basic Linux device drivers.
""",
    },
    # 11. Generic Software: No verification or hardware keywords
    {
        "site": "linkedin",
        "job_url": "https://www.linkedin.com/jobs/view/10011",
        "title": "Backend Python Engineer",
        "company": "CloudTech",
        "location": "Campinas, Brazil",
        "is_remote": True,
        "date_posted": "2026-09-14",
        "description": """
Building cloud microservices with Python, FastAPI, Docker, and PostgreSQL.
Experience with AWS Lambda and Terraform.
""",
    },
    # 12. Duplicate job to test DB cache exclusion (same as 10001)
    {
        "site": "linkedin",
        "job_url": "https://www.linkedin.com/jobs/view/10001",
        "title": "Senior RISC-V Core Verification Engineer (Duplicate Post)",
        "company": "Tenstorrent",
        "location": "Campinas, Brazil",
        "is_remote": True,
        "date_posted": "2026-09-14",
        "description": "Duplicate description...",
    },
]
