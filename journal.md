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
