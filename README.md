# Hardware Verification & Accelerator Job Hunter

A modular Python CLI that scrapes, deduplicates, filters, scores, and reports job postings across **LinkedIn**, **Indeed**, **Glassdoor**, **ZipRecruiter**, and **Google Jobs** using [`python-jobspy`](https://github.com/cullenwatson/JobSpy), then curates them against a specific candidate profile: a junior/entry-level **Digital Design & Verification Engineer** background (RISC-V core verification, UVM/SystemVerilog, FPGA-based hardware accelerators).

It exists because generic job-board recommendations (e.g. LinkedIn's own feed) are noisy — they mix in unrelated software/QA/supply-chain roles and senior-only postings the candidate isn't eligible for. This tool applies a rule-based scoring and exclusion engine instead, tuned specifically to that profile.

---

## How It Works

The pipeline runs as one streaming pass, printing each match live instead of waiting for the full run to finish:

1. **Scrape** — for every `(query, location)` pair in the configured rotation, `scraper.py` calls `python-jobspy`'s `scrape_jobs()` across all configured platforms at once, with a location radius (`--distance`) so nearby/satellite cities are picked up automatically.
2. **Deduplicate** — each listing's URL is checked against both the SQLite cache (`jobs.db`, listings from previous runs) and an in-memory set (listings already seen earlier in the *same* run).
3. **Filter & score** — `filter.py` evaluates the title + description against several gates, in order:
   - **Hard exclusions**: discard immediately if it matches unrelated-domain keywords (Selenium/Cypress/React/manual QA/clinical validation/etc.).
   - **Citizenship / work-authorization exclusion** (opt-out, on by default): discard if it requires citizenship, a security clearance, ITAR/US-person status, or explicitly offers no visa sponsorship.
   - **Seniority exclusion** (opt-out, on by default): discard if the title reads Senior/Staff/Principal/Director/Lead/Manager/Chief.
   - **Keyword scoring**: weighted match against four keyword categories (see below); must hit at least one of RISC-V, Standard DV, or General Digital/Hardware Design to be eligible at all.
   - **Minimum score threshold** (`--min-score`, default `8.0`).
4. **Curate** — a listing that survives all of the above is printed immediately to the terminal, saved to `jobs.db`, and added to this run's report.
5. **Report** — once the run ends (or is interrupted with `Ctrl+C` — whatever was curated so far is still exported, nothing is lost), `reporter.py` writes `curated_jobs_YYYY-MM-DD.csv` and `.md`, plus a terminal funnel summary showing how many listings were dropped at each stage.

---

## Project Structure

```
├── main.py            # CLI entrypoint: arg parsing + orchestrates the streaming pipeline
├── scraper.py          # python-jobspy wrapper: query x location rotation, per-platform scraping
├── filter.py           # Regex keyword engine: exclusions, weighted scoring, categorization
├── db.py               # SQLite persistence & URL-based deduplication
├── reporter.py          # CSV/Markdown export, live per-job console output, funnel summary
├── demo_data.py         # Offline fixture listings for --demo / testing (no network calls)
├── tests.py             # unittest suite (filter rules, db, reporter, scraper config)
├── requirements.txt     # python-jobspy, pandas, pydantic
└── README.md
```

Not committed to version control (see `.gitignore`): `.venv/`, `__pycache__/`, `jobs.db` (the local SQLite cache — regenerated per run), and the dated `curated_jobs_*.csv`/`.md` outputs (regenerated per run, not source).

---

## Current Configuration

As configured today, a default run (`python main.py`, no flags):

- **Platforms**: LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google Jobs (`DEFAULT_PLATFORMS` in `scraper.py`). Glassdoor is known to be unreliable for non-US locations (`jobspy` CSRF-token bootstrap issue on `.ca`/`.fr` domains) but fails gracefully without blocking the other platforms.
- **Locations**: Toronto, Montreal, Ottawa, Vancouver (Canada); Paris, Lyon (France); Eindhoven, Amsterdam (Netherlands); Geneva, Lausanne (Switzerland); Barcelona (Spain) — the candidate's target job markets. Each is searched with a 50-mile radius (`--distance`) so satellite/conurbated cities (Mississauga, Hamilton, Gatineau, Laval, Longueuil, greater Île-de-France, etc.) are included without listing them individually.
- **Queries**: 10 rotating search terms from `DEFAULT_QUERY_ROTATION` in `scraper.py`, ranging from RISC-V/CPU/ASIC/FPGA-specific titles to broader ones (`Hardware Engineer`, `Digital Design Engineer`, `Junior Verification Engineer`).
- **Posting age window**: last 2 weeks / 336 hours (`--hours-old`).
- **Seniority filter**: Senior+ titles excluded (`--include-senior` to disable).
- **Citizenship/sponsorship filter**: restricted postings excluded (`--include-restricted` to disable).
- **Minimum score**: 8.0 (`--min-score`).

### Domain Keyword Categories & Weights
- **Core RISC-V & Processor Verification (+5 pts each):** `risc-v`, `riscv`, `spike`, `iss`, `instruction set simulator`, `riscv-dv`, `core-v-verif`, `rvvi`, `step-and-compare`, `lockstep`, `cpu verification`, `processor verification`
- **Standard Hardware DV & Methodologies (+3 pts each):** `uvm`, `universal verification methodology`, `systemverilog`, `constrained-random`, `functional coverage`, `sva`, `systemverilog assertions`, `formal verification`, `xcelium`, `vcs`, `questa`
- **FPGA, SoC & Accelerator Design/Verif (+2 pts each):** `zynq`, `vivado`, `vitis`, `axi`, `axi-stream`, `dma`, `hardware accelerator`, `fixed-point`, `rtl`, `vhdl`, `soc`
- **General Digital/Hardware Design (+2 pts each):** `digital design`, `hardware engineer`, `asic`, `verilog`, `hdl`, `hardware description language`, `logic design`, `chip design`, `semiconductor`, `modelsim`, `cadence`, `synopsys`, `mentor graphics`, `testbench`, `co-simulation`, `combinational logic`, `sequential logic`, `digital logic`, `embedded hardware`, `microarchitecture`

A curated posting must match at least one keyword from RISC-V, Standard DV, or General Digital/Hardware Design (FPGA/Accelerator keywords alone aren't sufficient). Primary category is RISC-V if RISC-V keywords dominate (or the title mentions RISC-V/CPU/processor); otherwise whichever of ASIC/UVM DV, FPGA/Accelerator, or General Digital/Hardware Design scored highest.

### Hard Exclusions
`selenium`, `cypress`, `playwright`, `appium`, `postman`, `react`, `angular`, `figma`, `ui/ux`, `clinical validation`, `gmp`, `fda`, `software quality assurance`, `manual tester`.

### Citizenship / Work-Authorization Exclusions
`us citizen`, `u.s. citizen`, `us person`, `itar`, `no visa sponsorship`, `unable to sponsor`, `will not sponsor`, `security clearance`, `secret clearance`, `top secret clearance`, `nationalité française`, `habilitation secret défense`, `canadian citizen or permanent resident`, and related phrasing — see `WORK_AUTH_EXCLUSIONS_LIST` in `filter.py`. This is a heuristic over free-text, not a legal determination: it can occasionally over-exclude a posting that only mentions clearance as a future growth opportunity, or miss a restriction phrased in an unlisted way.

### Seniority Exclusions
Titles containing `senior`, `sr.`, `staff`, `principal`, `distinguished`, `director`, `lead`, `head of`, `vp`, `manager`, `chief` are treated as Senior+ and excluded by default.

---

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## How to Run

```bash
# Demo mode: run the full pipeline against offline fixture data, no network calls
python main.py --demo

# Default live run: 10 queries x 11 cities, 2-week posting window, up to 50 results/query/platform
python main.py

# Narrower run for a quicker/cheaper test pass
python main.py --hours-old 72 --results-wanted 15

# Override the target cities (semicolon-separated, since each entry reads "City, Country")
python main.py --locations "Toronto, Canada;Paris, France"

# Single-location override (replaces the whole --locations list), e.g. remote-only
python main.py --location "Remote" --is-remote

# Widen the conurbation search radius (miles)
python main.py --distance 75

# Include Senior+ titles and/or citizenship/clearance/no-sponsorship postings
python main.py --include-senior --include-restricted

# Run the test suite
python -m unittest tests.py -v
```

### Full CLI Reference

| Flag | Default | Description |
|---|---|---|
| `--locations` | 11 target cities | Semicolon-separated cities/regions to search |
| `--location` | *(unset)* | Single-location override, replaces `--locations` entirely |
| `--distance` | `50` | Search radius in miles around each location |
| `--hours-old` | `336` | Maximum posting age, in hours (336 = 2 weeks) |
| `--is-remote` | `False` | Restrict to remote-only postings |
| `--results-wanted` | `50` | Listings requested per platform per query/location |
| `--min-score` | `8.0` | Minimum composite score to curate a posting |
| `--sites` | all 5 platforms | Comma-separated platforms to scrape |
| `--queries` | full rotation | Comma-separated custom search terms |
| `--include-senior` | `False` | Include Senior+ titles (excluded by default) |
| `--include-restricted` | `False` | Include citizenship/clearance/no-sponsorship postings (excluded by default) |
| `--demo` | `False` | Run against offline fixture data, no scraping |
| `--db-path` | `jobs.db` | SQLite cache/persistence path |
| `--output-dir` | `.` | Directory for the CSV/Markdown report |
| `-v`, `--verbose` | `False` | Debug-level logging |

---

## Generated Artifacts (not committed to git)

1. **`jobs.db`** — local SQLite cache of every curated job ever seen, used for cross-run URL deduplication.
2. **`curated_jobs_YYYY-MM-DD.csv`** — columns: `Title`, `Company`, `Location`, `Remote`, `Score`, `Category`, `Matched Keywords`, `URL`.
3. **`curated_jobs_YYYY-MM-DD.md`** — grouped by country (Canada, France, Netherlands, Switzerland, Spain, Remote, then Other/Unspecified for anything that doesn't resolve to a target market), sorted descending by score within each country, each entry a clickable link back to the original posting and tagged with its domain category. Country is inferred from the listing's own location text (`extract_country()` in `reporter.py`) - it checks for an explicit country name first, then falls back to matching known target/satellite cities, since some platforms omit the country.
4. **Terminal output** — a `[+] CURATED ...` line printed live as each match is found, followed by a funnel summary at the end: `Total Scraped -> Excluded by URL Cache -> Dropped by Hard Exclusions -> Dropped by Citizenship/Sponsorship -> Dropped by Seniority Mismatch -> Dropped by Low Score -> Successfully Curated`.
