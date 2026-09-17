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
   - **Seniority exclusion** (opt-out, on by default): discard if the *title* reads Senior/Staff/Principal/Director/Lead/Manager/Chief.
   - **Years-of-experience exclusion** (opt-out, on by default): discard if the *description* states a years-of-experience floor at or above `--max-years-required` (default 5) - catches postings that require senior-level tenure without using a Senior-type title.
   - **Keyword scoring**: weighted match against four keyword categories (see below); must hit at least one of RISC-V, Standard DV, or General Digital/Hardware Design to be eligible at all.
   - **Minimum score threshold** (`--min-score`, default `8.0`).
4. **Curate** — a listing that survives all of the above is printed immediately to the terminal, saved to `jobs.db` (including salary/compensation fields when the platform provided them), and added to this run's report.
5. **Collapse duplicates** — once the run ends, postings that are likely the same real job cross-posted on multiple platforms (matching normalized title + company + city) are merged into one report entry via `collapse_duplicates()`, keeping the highest-scoring version and listing every other source.
6. **Report** — (or if interrupted with `Ctrl+C` - whatever was curated so far is still exported, nothing is lost) `reporter.py` writes `curated_jobs_YYYY-MM-DD.csv` and `.md`, plus a terminal funnel summary showing how many listings were dropped at each stage. Each entry also gets a **CV match score**, if a local `my_skills.json` is present, showing how many of its extracted Requirements mention one of the candidate's own skills.

---

## Project Structure

```
├── main.py            # CLI entrypoint: arg parsing + orchestrates the streaming pipeline
├── scraper.py          # python-jobspy wrapper: query x location rotation, per-platform scraping
├── filter.py           # Regex keyword engine: exclusions, weighted scoring, categorization
├── db.py               # SQLite persistence & URL-based deduplication
├── reporter.py          # CSV/Markdown export, duplicate collapsing, live console output, funnel summary
├── cv_match.py          # Loads a local skills list and scores it against extracted requirements
├── demo_data.py         # Offline fixture listings for --demo / testing (no network calls)
├── tests.py             # unittest suite (filter rules, db, reporter, cv_match, scraper config)
├── requirements.txt     # python-jobspy, pandas, pydantic
└── README.md
```

Not committed to version control (see `.gitignore`): `.venv/`, `__pycache__/`, `jobs.db` (the local SQLite cache — regenerated per run), the dated `curated_jobs_*.csv`/`.md` outputs (regenerated per run, not source), `Vinicius_Rocca_CV.md`, and `my_skills.json` (the CV-derived skills list used for CV-match scoring - personal data, kept local only; see below).

---

## Current Configuration

As configured today, a default run (`python main.py`, no flags):

- **Platforms**: LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google Jobs (`DEFAULT_PLATFORMS` in `scraper.py`). Glassdoor is known to be unreliable for non-US locations (`jobspy` CSRF-token bootstrap issue on `.ca`/`.fr` domains) but fails gracefully without blocking the other platforms.
- **Locations**: Toronto, Montreal, Ottawa, Vancouver (Canada); Paris, Lyon (France); Eindhoven, Amsterdam (Netherlands); Geneva, Lausanne (Switzerland); Barcelona (Spain) — the candidate's target job markets. Each is searched with a 50-mile radius (`--distance`) so satellite/conurbated cities (Mississauga, Hamilton, Gatineau, Laval, Longueuil, greater Île-de-France, etc.) are included without listing them individually.
- **Queries**: 10 rotating search terms from `DEFAULT_QUERY_ROTATION` in `scraper.py`, ranging from RISC-V/CPU/ASIC/FPGA-specific titles to broader ones (`Hardware Engineer`, `Digital Design Engineer`, `Junior Verification Engineer`).
- **Posting age window**: last 2 weeks / 336 hours (`--hours-old`).
- **Seniority filter**: Senior+ titles excluded (`--include-senior` to disable).
- **Citizenship/sponsorship filter**: restricted postings excluded (`--include-restricted` to disable).
- **Years-of-experience filter**: postings requiring 5+ years excluded (`--include-high-experience` to disable, `--max-years-required` to change the threshold).
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

### Years-of-Experience Exclusions
`extract_years_required()` in `filter.py` scans the description for phrases like "5+ years of experience", "3-5 years of relevant experience", or "6+ years of RTL design experience" (requires an explicit tie to the word "experience"/"exp." to avoid false positives like "10+ years in business"). When a posting states several different experience requirements, the highest one is used, since that's the real gate. Postings at or above `--max-years-required` (default `5`) are excluded by default - this exists because the seniority filter above only reads the *title*, so a posting plainly titled "DV Engineer" that requires "8+ years" in the body would otherwise slip through untouched.

### Cross-Platform Duplicate Collapsing
The same real posting is often scraped separately from LinkedIn, Indeed, and Google Jobs (three different URLs, one underlying job). `collapse_duplicates()` in `reporter.py` fingerprints each curated posting by normalized (title, company, city) and merges matches into a single report entry - keeping the highest-scoring version's fields and listing every other source under **Also Found On** (Markdown) / **Cross-Posted** (CSV). This is a heuristic: postings with missing title or company are never merged (falls back to the URL as a unique key, so incomplete data can't cause a false merge), but two genuinely distinct concurrent openings with an identical title at the same company in the same city would also collapse into one entry.

### CV-Match Scoring
Each entry's extracted Requirements are cross-referenced against the candidate's own skills, from a local `my_skills.json` (a flat JSON list of strings, e.g. `["RISC-V", "UVM", "SystemVerilog", ...]`) - gitignored since it's derived from the candidate's CV. `cv_match.py`'s `score_requirements_match()` counts how many requirement bullets mention at least one listed skill (whole-word, case-insensitive), shown as e.g. **CV Match: 6/9 (67%)**. If `my_skills.json` doesn't exist (e.g. a fresh clone), this is silently skipped - no CV Match field is shown, everything else works normally. To enable it, create `my_skills.json` in the project root with your own skills list.

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

# Include Senior+ titles, citizenship/clearance/no-sponsorship postings, and/or high-experience postings
python main.py --include-senior --include-restricted --include-high-experience

# Raise the years-of-experience threshold instead of disabling the filter outright
python main.py --max-years-required 7

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
| `--include-high-experience` | `False` | Include postings requiring >= `--max-years-required` (excluded by default) |
| `--max-years-required` | `5` | Years-of-experience floor at which a posting is excluded |
| `--demo` | `False` | Run against offline fixture data, no scraping |
| `--db-path` | `jobs.db` | SQLite cache/persistence path |
| `--output-dir` | `.` | Directory for the CSV/Markdown report |
| `-v`, `--verbose` | `False` | Debug-level logging |

---

## Generated Artifacts (not committed to git)

1. **`jobs.db`** — local SQLite cache of every curated job ever seen (including salary/compensation fields when provided), used for cross-run URL deduplication. Column migrations (e.g. the salary columns) are applied automatically on startup via `ALTER TABLE`, so an existing database from an older version of this tool upgrades in place without data loss.
2. **`curated_jobs_YYYY-MM-DD.csv`** — columns: `Title`, `Company`, `Location`, `Remote`, `Salary`, `Score`, `Category`, `CV Match`, `Matched Keywords`, `Cross-Posted`, `URL`. `Salary`/`CV Match`/`Cross-Posted` are blank when not applicable (no compensation data, no `my_skills.json` or no extracted requirements, or no other source found, respectively).
3. **`curated_jobs_YYYY-MM-DD.md`** — grouped by country (Canada, France, Netherlands, Switzerland, Spain, Remote, then Other/Unspecified for anything that doesn't resolve to a target market), sorted descending by score within each country, each entry a clickable link back to the original posting, tagged with its domain category, its salary range when available (`format_salary()` in `reporter.py`, from `jobspy`'s own min/max/currency/interval fields - previously captured but silently discarded), and its CV match score when `my_skills.json` is configured. Country is inferred from the listing's own location text (`extract_country()` in `reporter.py`) - it checks for an explicit country name first, then falls back to matching known target/satellite cities, since some platforms omit the country.

   Each entry also shows a **Requirements** list rather than a generic first-few-sentences blurb: `extract_requirements()` scans the description for a recognized section header (`Requirements`, `Qualifications`, `What You'll Need`, `About You`, etc.), collects the bullets that follow it, and stops at the next recognized section (`Responsibilities`, `Benefits`, `About Us`, etc.). If no such section is found - some postings are unstructured prose - it falls back to the old generic excerpt under an **Overview** label instead.
4. **Terminal output** — a `[+] CURATED ...` line printed live as each match is found, followed by a funnel summary at the end: `Total Scraped -> Excluded by URL Cache -> Dropped by Hard Exclusions -> Dropped by Citizenship/Sponsorship -> Dropped by Seniority Mismatch -> Dropped by Experience Mismatch -> Dropped by Low Score -> Successfully Curated`.
