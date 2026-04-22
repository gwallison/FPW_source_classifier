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
