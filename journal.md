# Project Journal

## 2026-04-20 — Session 1

### Context / motivation
PA fracking completion reports contain water source records (free-text name + volume).
Earlier work tried to link these to PA DEP water databases (PASDA, WMPDU) but hit a
dead end: DEP data is incomplete, naming is inconsistent, and focus-group users were
skeptical of results derived from it. This project pivots to USGS NHD as the reference
dataset and focuses on what the completion data can reliably show.

### Step 1 — Data exploration
Examined the two input parquet files:
- `well_junction_table.parquet` (49,363 rows): links wells → sources via `planSource`
  free-text, `volume`, `site_ID`; 3,757 unique `planSource` strings
- `FPW_master_water_source.parquet` (2,882 rows): unique sources with lat/lon and PA DEP
  flags; only ~15 rows have coordinates (manual curation was abandoned as too slow)

Top `planSource` values showed clear patterns: SRBC docket strings dominate, along with
plain creek/river names, water authority taps, and recycled-water entries.

### Step 2 — Source type classifier (`source_classifier.ipynb`)
Built a regex priority classifier assigning each `planSource` to one of seven types:
`reuse`, `interconnection`, `groundwater`, `impoundment`, `surface_direct`, `srbc_only`,
`ambiguous`.

Key terms added for interconnection after user guidance: `intc`, `tap`, `vending`, `vault`,
`hydrant` (plus `authority`, `municipal`, `meter`, `water company`).

**Coverage results** (by reported volume):
- `surface_direct`: 49%
- `impoundment`: 16%
- `interconnection`: 15%
- `ambiguous`: 15%
- `reuse`: 3%
- `groundwater`: 1%

65% of volume (surface_direct + impoundment) is potentially NHD-linkable.

### Step 3 — SRBC feature name extractor
SRBC permit strings embed both an operator name and a water feature name, e.g.:
`"Cabot, Tunkhannock Creek [SRBC Docket Number 20180605]"`.

Built `extract_srbc_feature()` in `source_classifier.ipynb` to isolate the feature name.
Logic: strip SRBC bracket (including malformed `(SRBC...\]` variant and bare
`SRBC Docket No.` format), split on commas, search reversed segments for water keywords,
truncate at last water keyword, strip leading operator-name tokens.

Match rate: **88.6%** of 13,471 SRBC records got a clean feature name.
Top extracted names: Tunkhannock Creek (3,698), Meshoppen Creek (2,660), Susquehanna (1,287).

### Step 4 — Well coordinates
Added well lat/lon to junction table by joining `skinny_df.parquet` on `api10`
(groupby → first, since all rows per well share the same coordinates).

Result: **79.1%** of junction rows now have well coordinates.
For NHD candidates specifically: **94.4%** have well proxy coordinates.

Coordinate strategy: source coordinates from master table take precedence; median well
lat/lon per `planSource` used as fallback proxy.

### Step 5 — NHD data download
Downloaded USGS NHD for Pennsylvania from:
`https://prd-tnm.s3.amazonaws.com/StagedProducts/Hydrography/NHD/State/GDB/NHD_H_Pennsylvania_State_GDB.zip`
(247 MB, file geodatabase format)

Extracted two layers using geopandas + pyogrio, keeping only named features, reprojected
to WGS84, saved to `data/NHD_PA_named.gpkg` (193 MB):
- NHDFlowline: 127,509 named features
- NHDWaterbody: 2,863 named features

### Step 6 — NHD fuzzy matcher (`nhd_matcher.ipynb`)
Built spatial + fuzzy matcher:
- **Name extraction**: general `extract_search_name()` strips SRBC brackets, parentheticals,
  state abbreviations, trailing station/source IDs; splits on comma / ` - ` / ` @ `;
  truncates at last water keyword; strips leading operator tokens
- **Name normalization**: expand abbreviations (N→North, Br→Branch, Unt→Unnamed Tributary,
  etc.), lowercase, strip punctuation
- **Spatial filter**: NHD features within 50 km bounding box of coordinate
- **Fuzzy score**: `rapidfuzz.fuzz.token_sort_ratio` (handles word-order variation)
- Returns best NHD match per source

Iterative fixes applied:
- Added ` - ` and ` @ ` as delimiters (fixed "Susquehanna Gas Field Services - Susquehanna River")
- Added `garrison` to operator prefix blocklist

**Final match quality** (1,035 candidates with extractable name):
- Score ≥ 90: 724 (70%)
- Score ≥ 80: 797 (77%)
- Score < 60: 29 (3%)

Results saved to `data/nhd_match_results.parquet`.

### Step 7 — Join match results to junction table (`nhd_matcher.ipynb`, cells 13-14)
Merged `nhd_match_results` back to `well_junction_table` on `planSource` (left join),
added `match_tier` column, reported volume by tier.

**Volume attribution — all 49,363 junction rows:**

| Tier | Records | Volume (Mgal) | % of total |
|---|---|---|---|
| high (≥90) | 13,904 | 36,876 | 37.0% |
| good (80-89) | 840 | 1,291 | 1.3% |
| fair (60-79) | 3,722 | 9,270 | 9.3% |
| low (<60) | 129 | 467 | 0.5% |
| unmatched | 22,137 | 51,747 | 51.9% |

**Surface / impoundment / SRBC candidates only:**

| Tier | Records | Volume (Mgal) | % of candidate vol |
|---|---|---|---|
| high (≥90) | 13,904 | 36,876 | 56.5% |
| good (80-89) | 840 | 1,291 | 2.0% |
| fair (60-79) | 3,722 | 9,270 | 14.2% |
| low (<60) | 129 | 467 | 0.7% |
| unmatched | 5,766 | 17,325 | 26.6% |

The high+good tiers cover **58.5% of surface/impoundment/SRBC volume** with reliable NHD
linkage. The 26.6% unmatched within this candidate set are sources with no extractable
feature name (mostly impoundments without a stream name) or no nearby NHD feature found.

The large "unmatched" share in the all-rows table (51.9%) reflects non-surface source types
(reuse, interconnection, groundwater, ambiguous) that were never NHD candidates.

Output saved to `data/junction_nhd_matched.parquet` (49,363 rows, 21 columns).

### Step 8 — `dont_know` classifier bucket
Examined the ambiguous bucket (15% of volume). Top patterns by volume:
- **SWW** (3,865 Mgal, 45 strings) — e.g. "Newton SWW", "Monroe SWW"
- **SPWA/SWPA** (2,714 Mgal, 35 strings) — water authority abbreviations
- **Aqua** (1,312 Mgal, 19 strings) — Aqua Pennsylvania utility
- **WI suffix** (1,057 Mgal, 86 strings) — e.g. "Huff WI", "Parys WI"
- **NKWA/MAWC/MANK** (845 Mgal, 21 strings) — municipal water authorities
- **Clermont** (483 Mgal, 19 strings) — numbered site names, e.g. "Clermont #2"
- **Quarry/Mine** (328 Mgal, 10 strings) — e.g. "Goodwin Quarry"
- **Brine** (137 Mgal, 31 strings)
- **AWS Withdrawal** (105 Mgal, 1 string)

Decision: classify all as `dont_know` rather than guessing type. These are
recognizable patterns whose source type is not reliably determinable from the name alone.

Added `dont_know` as a new rule (between `srbc_only` and `ambiguous` fallthrough) in
both `source_classifier.ipynb` and `nhd_matcher.ipynb`.

**Updated coverage by volume:**
- `surface_direct`: 49.1%
- `impoundment`: 16.0%
- `interconnection`: 15.4%
- `dont_know`: 10.9% ← new
- `ambiguous`: 4.2% ← down from 15%
- `reuse`: 2.6%, `groundwater`: 1.3%

Reran both notebooks; output files regenerated (NHD match results unchanged since
`dont_know` sources are not NHD candidates).

### Next steps (as of end of session)
- `dont_know` bucket (11% of volume): some entries (SWW, WI) might be resolvable
  with additional reference data if worth pursuing
- `ambiguous` bucket (4% of volume, ~626 unique strings): further rule refinement possible
- Consider WV NHD download for wells near the PA/WV border (Fish Creek WV, Monongahela)
- Consider SRBC docket lookup to fill coordinate gaps for unmatched impoundment sources

---

## 2026-04-21 — Session 2

### Step 9 — SRBC docket PDF lookup (`srbc_docket_lookup.ipynb`)

Built a scraper for the SRBC WAAV portal. No public API exists; the portal returns PDFs at:
`https://www.srbc.gov/waav/Search/getdocket?projectnumber={docket}&documenttype=Approval&isabre=False`

Parser using `pdfplumber`: splits PDF text on "Source Information" headers, extracts
`Approved Source`, lat/lon from `Withdrawal Location ... Lat: X.X N Long: X.X W`, county,
municipality, subbasin, approval type. Guard added to skip sections where "Approved Source:"
is absent (section headings produce spurious empty records otherwise).

For multi-source dockets: used `rapidfuzz.fuzz.partial_ratio` to match each planSource
against the SRBC-confirmed source names and pick the best.

Results: **all 72 unique dockets** successfully parsed, all with usable coordinates.
Outputs: `data/srbc_docket_info.parquet` (72 rows), `data/srbc_coords_lookup.parquet`
(397 unique planSources, 387 with coordinates).

### Step 10 — WV NHD integration (`extract_wv_nhd.py`)

Downloaded WV NHD state GDB (177 MB) from USGS. Extracted named NHDFlowline and
NHDWaterbody features in eastern WV (lon > -82.5, lat > 38.5) — 59,336 features.
Combined with PA named features (130,372) to produce `NHD_combined_named.gpkg`
(189,708 total features). Large files excluded from git via `.gitignore`.

### Step 11 — SRBC re-match (nhd_matcher.ipynb, cells 12-13)

For all SRBC-tagged candidates, ran three (name, coord) combinations and kept best score:
- **Run A**: planSource-extracted name + SRBC withdrawal-point coords
- **Run B**: SRBC approved_source name + SRBC coords
- **Run C**: planSource-extracted name + original well-proxy coords (fallback when SRBC
  coords are misleading, e.g. docket covers a different source than the planSource name)

Score ≥ 90 improved from **724 → 822**.

### Step 12 — WV border re-match (nhd_matcher.ipynb, cell after 13)

Added WV-specific name expansions: `Mon → Monongahela`, `Nunkard → Dunkard`, `Whg → Wheeling`.

Added fallback for degenerate extracted names: when `extract_search_name()` degrades to
a bare water-type word (e.g. `"River"` from `"Monongahela @ River Speers"`), fall back to
the planSource stripped of `@ location` suffixes before applying normalization.

Combined NHD (`NHD_combined_named.gpkg`) used so WV streams (Monongahela River,
North Fork Dunkard Fork, Fish Creek) are findable.

Result: **26 border sources improved**, score ≥ 90 went from **822 → 846**.
All "Mon River" variants (25 sources) now correctly link to Monongahela River at score 100.
"North Fork Nunkard Fork" → "North Fork Dunkard Fork" at score 100.

**Final NHD match quality (1,035 candidates):**
- Score ≥ 90: 846 (82%)
- Score 80-89: 38 (4%)
- Score 60-79: 122 (12%)
- Score < 60: 29 (3%)

**Final volume attribution — all 49,363 junction rows:**

| Tier | Records | Volume (Mgal) | % of total |
|---|---|---|---|
| high (≥90) | 16,320 | 40,405 | 40.5% |
| good (80-89) | 323 | 1,075 | 1.1% |
| fair (60-79) | 1,866 | 6,058 | 6.1% |
| low (<60) | 86 | 365 | 0.4% |
| unmatched | 22,137 | 51,747 | 51.9% |

**Surface / impoundment / SRBC candidates only:**

| Tier | Records | Volume (Mgal) | % of candidate vol |
|---|---|---|---|
| high (≥90) | 16,320 | 40,405 | 61.9% |
| good (80-89) | 323 | 1,075 | 1.6% |
| fair (60-79) | 1,866 | 6,058 | 9.3% |
| low (<60) | 86 | 365 | 0.6% |
| unmatched | 5,766 | 17,325 | 26.6% |

### Step 13 — Analysis notebook (`analysis.ipynb`)

New notebook with nine sections:
1. **Volume by source type** — pie chart; surface_direct 49%, interconnection 15%,
   impoundment 16%, dont_know 11%, reuse 3%
2. **NHD match quality by volume** — tier table for all rows and candidate rows
3. **Top NHD features** — 133 unique features linked; Tunkhannock Creek #1 at 3,898 Mgal,
   Allegheny River #2 at 3,536 Mgal, Meshoppen Creek #3 at 2,932 Mgal
4. **Volume by year** — stacked area chart; joins skinny_df `date` column for completion year
5. **Well map** — scatter plot colored by dominant source type, sized by volume
6. **Reuse trend** — dual-axis: total volume + % recycled by year
7. **Basin rollup** — regex assignment to Susquehanna / Monongahela / Ohio-Allegheny /
   Delaware / Other from NHD feature names
8. **Unmatched inventory** — dont_know and ambiguous top sources by volume; fair/low matches
9. **Export** — `data/nhd_feature_volume_summary.csv` (133 NHD features, high/good only)

### Next priority (top of list)
**Improve ambiguous/unknown/low-confidence planSources.** The remaining coverage gaps:
- `dont_know` (11% vol): SWW, WI-suffix entries may be classifiable as interconnection
- `ambiguous` (4% vol, ~626 unique strings): rule review needed
- Fair/low NHD matches (score 60-79): some fixable with targeted normalization

---

## 2026-04-22 — Session 3

### Step 14 — PA DEP water resource matching (`dep_matcher.ipynb`)

Goal: resolve `dont_know` (10.9% vol) and `ambiguous` (4.2% vol) planSources using PA DEP
water resource point data.

**DEP data:** `data/PA resources/WaterResources2026_01.geojson` — 24,037 water withdrawal
points (surface water, groundwater, interconnection), EPSG:3857 reprojected to WGS84.
Fields: `SUB_FACILI` (source name), `ORGANIZATI` (operator), `ACTIVITY` (withdrawal type).

**Name normalization:**
- `normalize()`: lowercase, strip punctuation, expand common abbreviations
- `norm_operator()`: strip legal suffixes (LLC, Inc, LP, etc.) for fuzzy operator matching
- `extract_key_name()`: strips type-prefix acronyms (SWW, WI, SPWA, Aqua, MAWC) to isolate
  a bare location qualifier — the term that actually appears in the DEP source name

**Scoring:** `max(token_set_ratio(key_name, dep_name), token_sort_ratio(full_name, dep_name))`
using rapidfuzz. `token_set_ratio` was critical: DEP names embed the location qualifier
inside a longer string (e.g. `"SALSMAN"` inside `"SUSQUEHANNA RIVER - SALSMAN FARM"`),
which `token_sort_ratio` alone would score poorly. Using `token_set_ratio` on the extracted
key term fixed scores from ~44 to 100 for these cases.

**Two-pass matching strategy:**
1. **Operator pass**: filter DEP pool to entries with matching operator name, score ≥ 70
2. **Global fallback**: for utility-operated sources (SWPA Water Authority, Aqua Infrastructure,
   MAWC, etc.), try full DEP pool — local operator name rarely appears in DEP records

`get_type_hint()` maps dont_know patterns (SWW → surface/interconnection, WI → interconnection,
Aqua → surface/interconnection, brine → excluded) to restrict the DEP pool to compatible types.

**Results — dont_know + ambiguous (1,030 sources):**
- Score ≥ 80: strong majority of dont_know sources resolved with correct type and coordinates

**Extension to all sources:** Reran matching against all 3,506 source candidates (excluding
reuse and no_source) to validate existing classifications and add DEP coordinates. Saved to
`data/dep_match_results_all.parquet` (3,506 rows).

### Step 15 — Apply DEP matches to junction table

Built application cell (saved to `data/junction_dep_updated.parquet`):
- Join `dep_match_results_all` → junction on `planSource`
- New columns added: `dep_score`, `dep_type`, `dep_stype`, `dep_lat`, `dep_lon`, `dep_src`
- Reclassify `dont_know`/`ambiguous` rows where `dep_score ≥ 80`
- **Brine override**: unconditional — any planSource containing `\bbrine\b` forced to `reuse`
  regardless of DEP match (DEP matched "Brine Water" → "RAIN WATER" at score 86 → wrong type)

**DEP coordinate coverage:** 57.1% of all junction rows (28,198/49,363) now have DEP-sourced
coordinates, supplementing the NHD match coordinate data.

### Step 16 — Rule fixes and date-suffix inheritance

Applied a priority-ordered `RULE_FIXES` list for patterns automation cannot resolve reliably:
- `brine` → `reuse`; quarry/mine/pit → `groundwater`/`impoundment` where appropriate
- `rainwater`/`rain` → `reuse`
- SWW / WI patterns → `interconnection` where DEP matching confirmed the type
- **Date-suffix inheritance**: planSources like `"Newton SWW 20140801"` look up the base
  source (`"Newton SWW"`) in the already-resolved type dict and inherit its type

### Step 17 — Manual curation tool

For residuals that automation cannot resolve (short codes like "B37", operator-specific
site names with no DEP record), built a CSV-based curation workflow:
- Export: 319 residual planSources to `data/manual_curation.csv` with columns:
  `planSource`, `curated_type`, `notes`, `source_type`, `volume_Mgal`, `operator_clean`,
  `dep_score`, `dep_src`, `dep_type`
- User fills `curated_type` in Excel; apply cell re-reads and overrides the parquet
- Auto-mapping: user used `surface_water` (DEP terminology) → remapped to `surface_direct`
  (our vocabulary) before applying

**User curated 231 of 319 entries (844.5 Mgal) covering reuse pond networks, impoundments,
and surface water withdrawals identified by operator knowledge.**

### Final results

**Type distribution (% of total volume):**

| Type | Before | After |
|---|---|---|
| surface_direct | 49.1% | 54.4% |
| interconnection | 15.4% | 21.8% |
| impoundment | 16.0% | 17.5% |
| reuse | 2.6% | 3.1% |
| groundwater | 1.3% | 2.5% |
| dont_know | 10.9% | 0.0% |
| ambiguous | 4.2% | 0.2% |

**Residuals:** `dont_know` 0 rows; `ambiguous` 480 rows at 0.2% of total volume — negligible.

Output: `data/junction_dep_updated.parquet` (49,363 rows, junction table with DEP columns).

---

## 2026-04-23 — Session 4

### Step 18 — Geolocation coverage audit (`analysis.ipynb`, section 10)

Added new section to `analysis.ipynb` to quantify coordinate availability across all planSources:
- 98.7% of unique planSources are geolocated (99.2% of volume); only 49 sources (0.8% vol) have no coord
- By best available coord: DEP 62.9% of sources (76.6% vol), well proxy 32.3% (18.2%), SRBC 2.8% (4.5%), none 1.3% (0.8%)
- Master table has full coordinates (2,882 rows) but only 123 junction planSources link to it via site_ID — negligible contribution

Identified two re-match opportunities:
1. **New candidates** (reclassified surface/impoundment from dont_know/ambiguous, never NHD-matched): 1,053 sources, 23,811 Mgal; 801 have precise DEP coords
2. **Fair/low re-match with DEP coords**: 116 sources, 5,228 Mgal

### Step 19 — NHD Pass 4 (`nhd_matcher.ipynb`, Pass 4 cells; run via `run_pass4.py`)

Built and ran a 4th NHD matching pass targeting the two groups above.

**Search name strategy (in priority order):**
1. `extract_search_name(planSource)` — primary (same as previous passes)
2. `extract_search_name(dep_src)` — fallback for reclassified SWW/WI entries (e.g. `dep_src = "SUSQUEHANNA RIVER - SALSMAN"` → extracts "Susquehanna River")
3. SRBC confirmed source name (for SRBC-tagged sources)

Used combined PA+WV NHD throughout. DEP coordinates used as spatial anchor (actual withdrawal points, more precise than well proxy).

**Results:**
- 1,169 targets processed; 358 returned a match
- 293 applied: 221 new sources matched (score ≥ 60), 72 existing fair/low matches improved
- Score ≥ 90: 234; score ≥ 80: 243

**Volume impact (before → after):**

| Tier | Before (all rows) | After (all rows) | Before (candidates) | After (candidates) |
|---|---|---|---|---|
| high (≥90) | 40.5% | **51.4%** | 56.1% | **71.1%** |
| good (80-89) | 1.1% | **2.5%** | 1.5% | **3.5%** |
| fair (60-79) | 6.1% | 5.2% | 8.4% | 7.1% |
| low (<60) | 0.4% | 0.3% | 0.5% | 0.5% |
| unmatched | 51.9% | **40.6%** | 33.5% | **17.8%** |

High+good for candidates: **74.6%** (was 57.6%).

**Remaining unmatched candidates (12,623 Mgal, 840 sources):**
Dominated by operator-named impoundments (YOUNG, Parys, ZEFFER, KRAUSE, etc.) where `dep_src = "IMPOUNDMENT"` — no stream name available. These are genuinely un-matchable without manual lookup.
Notable exceptions: "Northeast Marcellus Aqua Midstream" (421 Mgal) had bad DEP coord and may warrant manual attention.

Output files updated: `data/junction_dep_updated.parquet`, `data/nhd_match_results.parquet`.

---

## 2026-04-27 — Project evaluation discussion

### Topic: Update workflow readiness and periodic-vs-one-shot assessment

Discussion context: upcoming project meeting to evaluate whether this is a one-shot analysis
or a periodically updating project. Assessed the full pipeline from scraping through analysis.

### Current state of the pipeline

The pipeline has all required *components* but is **not update-ready** as built. Each notebook
was developed iteratively and reads the prior stage's output; there is no incremental update
logic. A naive full rerun would overwrite manual curation decisions.

### Full pipeline stages and update burden

**Stage 1 — Upstream scraping (outside this project)**
An earlier project scrapes PA DEP fracking completion reports to produce `well_junction_table.parquet`.
Key unknowns for the meeting:
- Is the scraper maintained and functional?
- How does PA DEP publish new completions — rolling updates, batch releases, API?
- How much hand-cleaning is required post-scrape before data is usable here?
This stage is the biggest wildcard and could dominate update cost.

**Stage 2 — Reference data freshness**
- NHD: essentially static; USGS updates infrequently — not a concern
- PA DEP water resources geojson: current snapshot Jan 2026; updated pull is a one-time download
- OpenFF (`skinny_df`): has a release cadence (filename dated 2026-04-03); new well coords
  require updated version
- SRBC dockets: new docket numbers from new completion reports require incremental PDF downloads

**Stage 3 — This project's classification pipeline**
- Source classification (regex): fully automated, near-zero effort
- DEP matching: fully automated, near-zero effort
- NHD matching: automated, but the 4-pass structure needs consolidation before it can run
  cleanly as an update — estimated 1–2 sessions of work
- **Manual curation:** the real recurring cost; original run had 319 residuals (231 filled).
  Subsequent runs will have fewer (unusual operator names already resolved), but budget
  ~2–4 hours of human review per update cycle

### What an update-ready pipeline would require

1. **Delta detection** — identify `planSource` values that are genuinely new vs. already classified
2. **Carryover logic** — merge existing `nhd_match_results`, `dep_match_results`, and
   `manual_curation.csv` so previously resolved sources don't get re-litigated
3. **Orchestration** — a single script or clean "update" cell routing only new sources through
   each stage and merging results back into the canonical output
4. **Reference data versioning** — snapshot DEP geojson and OpenFF data at each update cycle

Estimated build effort: **1–2 sessions** once the NHD matching passes are consolidated.

### Decision framework for the meeting

| Factor | One-shot | Periodic |
|---|---|---|
| Research question | Answered by 2026 snapshot | Requires trend analysis over time |
| Scraper maintenance | High cost / fragile | Low cost / reliable |
| Audience | Academic / single report | Advocacy / regulatory / ongoing monitoring |
| Manual curation tolerance | Avoid recurring human time | Can absorb 2–4 hrs/cycle |

### Key questions to resolve at the meeting
1. What is the current state of the upstream scraper — who owns it, when was it last run,
   how much manual cleaning did the last run require?
2. How often does PA DEP publish new completion reports, and how many new records per cycle?
3. Is the research question time-sensitive enough to require periodic updates, or is a 2026
   snapshot sufficient for the planned deliverables?

---

## 2026-04-27 — Client value of NHD integration; reuse volume gap; completion-level fields

### NHD integration: what it delivers to clients

The core value of NHD linkage is converting a business record into a geography — a named
planSource becomes a mappable, watershed-membered stream feature with a persistent ID.

**Immediately available (all data in hand):**
- **Stream-level withdrawal profiles** — per-NHD-feature totals (volume, operators, years).
  `nhd_feature_volume_summary.csv` already has 163 features / 53,724 Mgal at high/good confidence.
  Tunkhannock Creek leads at ~3,900 Mgal.
- **Geographic concentration** — withdrawals mappable at stream level, rollable by HUC or county
- **Reuse trend by operator** — year-by-year shift from surface water to recycled water
- **Operator surface-water ranking** — leaderboard computable directly from `junction_dep_updated`

**Requires modest additional work:**
- **Low-flow season risk flags** — completion dates from `skinny_df` identify Jul–Oct withdrawals;
  PA has no mandatory seasonal restrictions — a concrete regulatory advocacy point
- **Headwater vulnerability** — stream order already in NHD GDB; 1st/2nd order streams with
  material withdrawals are identifiable without new data

**Key framing for clients:** Without NHD linkage you can only report volume by source type.
With it you can state: *"Operators withdrew X Mgal from the Tunkhannock Creek watershed during
low-flow months, representing Y% of average summer flow."* That is evidence usable in permit
hearings. The 74.6% high+good match rate means this covers the substantial majority of the
industry's surface water footprint, not a cherry-picked sample.

### Reuse volume gap: completion-level fields not in current project

The `well_junction_table` only captures the water source rows from each completion file
(the `planSource` / `volume` table). Each completion file also carries three summary fields
that were not brought into this project:

| DEP field | Meaning |
|---|---|
| `totalGallons` | Total water used for the job (fresh + recycled) |
| `baseWaterVolume` | Fresh/new water only — equals sum of planSource volumes |
| `recycledWaterVolume` | Recycled/reuse component — NOT listed as planSource rows |

**Confirmed relationship:** `baseWaterVolume + recycledWaterVolume = totalGallons` (verified
on sample file: 9,280,561 + 6,509,885 = 15,790,446). In that example, recycled water was
**41% of total** — entirely absent from our current analysis.

**FracFocus cross-check:** `TotalBaseWaterVolume` in `disclosures.parquet` (OpenFF) aligns
with `totalGallons` (all water including recycled), NOT with `baseWaterVolume`. It provides
an independent cross-check on total water per job without reading individual DEP files; where
they diverge, it may flag data quality or disclosure revision issues.

**Implication for reuse reporting:** Our current 3% reuse figure captures only planSource
rows explicitly labeled as recycled/flowback. The true reuse fraction — `recycledWaterVolume /
totalGallons` — is almost certainly materially higher and is a more defensible metric for clients.

### Data availability and next steps for completion-level fields

- No consolidated parquet of completion-level summary fields exists yet in this project
- Individual files are at `G:\My Drive\Info_home\Projects\Project_Homes\FPW_FracTracker\Completion_file_data\`
  (named by api10 + timestamp, e.g. `3711722112_2-19-2025-9-00-48-AM.parquet`)
- **Status filter is critical:** the folder contains both "Accepted" and "Submitted" DEP forms;
  only "Accepted" should be used — "Submitted" records may be revised or rejected
- Building the consolidated file will be done in a separate project, then brought here

### managementPlanId clarification

`managementPlanId` in the junction table is a **water management plan**, not a per-completion
identifier. Cardinality check: only 339 unique plan IDs across 5,356 unique wells; 271 of 339
plans cover multiple wells (some 30+). The right join key for completion-level volume fields
is `saved_fn` (the specific completion file per well), not `managementPlanId`.

### Additional companies in completion files

Each completion file also lists service companies beyond the operator: `perfCompany`,
`fracCompany`, `flowBackCompany`. These are not currently in the junction table. Clients
may want to summarize withdrawals by these companies — e.g., which frac companies are most
active in a watershed, or whether certain contractors correlate with specific sourcing patterns.
Should be retained when the consolidated completion file is built.

---

## 2026-04-27 — Streamlit UI spike (`streamlit_app.py`)

### Goal
Spike an interactive map-based explorer to evaluate UI direction before investing in a
full deliverable. Scope: local only, no deployment.

### Stack
Streamlit 1.56 + pydeck 0.9.1 + geopandas. Basemap: Carto Voyager (OSM-like, free, no API
key, renders correctly as a GL style underneath pydeck vector layers).

### Data loading
- `junction_dep_updated.parquet` joined to `skinny_df` for well coordinates and completion year
- NHD features loaded from `NHD_PA_named.gpkg` at startup, cached
- All heavy loads wrapped in `@st.cache_data`

### Map layers

**Frac wells (`PolygonLayer`):** Hollow red squares — transparent fill, solid red outline.
Sized by sqrt(volume_Mgal) scaled to degree half-width. Initial attempt used `ColumnLayer`
with `disk_resolution=4` for squares, but ColumnLayer does not support hollow/stroked
rendering at pitch=0 (stroked applies to column sides, not the top face). Switched to
`PolygonLayer` with `get_fill_color=[0,0,0,0]` and `get_line_color=WELL_OUTLINE`.

Fixed color (red) for all wells regardless of source type — wells draw from multiple source
types so per-type coloring was misleading. Source type color is reserved for the source
point layer.

**Water source points (`ScatterplotLayer`):** Colored circles at `dep_lat/dep_lon`,
colored by source type using the TYPE_COLOR palette.

**NHD stream features (`GeoJsonLayer`):** Blue lines/polygons, `line_width_min_pixels=2`.

### NHD display: two iterations

**First approach (single matched segment):** Loaded NHD features by `permanent_identifier`
(the single matched segment per source). Rendered correctly but showed only tiny slivers of
stream — each NHD segment is short, and the matched segment may be miles from the actual
withdrawal point.

**Second approach (full named stream by gnis_name):** Switched to loading all segments
sharing the matched `gnis_name`. Shows complete river/creek networks. Revealed a new problem:
same-named streams elsewhere in PA (e.g., multiple "Pine Creek" drainages) appeared on the
map with no associated source points.

**Fix — per-name proximity filter:** After loading by `gnis_name`, filter segments to those
within ~50km (0.5°) of an actual source coordinate (dep_lat/dep_lon preferred, well proxy
fallback) that matched to that name. Each NHD segment must be near a dep dot to be shown.
A coarser bounding box over all active sources was tried first but was insufficient because
the box spans most of PA.

### Sidebar filters
- Operator multiselect
- Source type multiselect
- Completion year range slider (default floor: 2005)
- NHD match tier multiselect
- Layer toggles: wells / source points / NHD features

### Tabs
Three tabs below the map: top NHD features by volume, top operators by volume, top sources
by volume — all reactive to the current sidebar filter.

### Observations from spike
- Map clearly shows NE PA Marcellus Shale well concentration with source streams visible
- Hollow well squares allow source dots and NHD lines to show through the dense well cluster
- NHD full-stream display is much more informative than single segments
- Per-name proximity filter is the correct approach; validates that each displayed stream
  segment has a real associated source point
- The reuse volume gap and service company fields (perfCompany, fracCompany, flowBackCompany)
  from completion files are noted as future enhancements once consolidated parquet is built

### Identified future enhancements
- Zoom-to-selection when operator filter is applied
- Volume threshold slider for NHD layer (hide low-volume streams)
- Source type breakdown in well tooltip on hover
- Streamlit Cloud deployment (requires resolving large file hosting for NHD gpkg)
