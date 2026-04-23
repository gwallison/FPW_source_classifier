# FPW Source Classifier

## Project overview
Connect Pennsylvania fracking completion reports to water feature data. Completion reports
list the water sources used for each fracking job (free-text name + volume). The goal is to
classify those sources by type and link named surface-water sources to features in the USGS
National Hydrography Dataset (NHD) for geographic/watershed analysis.

## Background
An earlier project scraped PA DEP fracking completion reports. Attempts to link source names
to PA DEP water databases (PASDA, WMPDU) were set aside due to unreliable/incomplete DEP data
and inconsistent naming. This project pivots to USGS NHD as the reference dataset instead.

## Data files (`data/`)

| File | Description |
|---|---|
| `well_junction_table.parquet` | ~49K rows linking fracking wells (`api10`) to water sources (`planSource`, `volume`, `site_ID`) |
| `FPW_master_water_source.parquet` | 2,882 unique water sources with coordinates (partially manually curated) and PA DEP flags |
| `NHD_H_Pennsylvania_State_GDB.zip` | Raw USGS NHD download for Pennsylvania (247 MB) |
| `NHD_PA_named.gpkg` | Extracted NHD named features only: NHDFlowline (127,509) + NHDWaterbody (2,863), WGS84 |
| `nhd_match_results.parquet` | NHD match results per unique source: search_name, nhd_name, score, dist_km, nhd_id |
| `srbc_docket_info.parquet` | 72 SRBC dockets: approved_source, lat, lon, county, subbasin, approval_type |
| `srbc_coords_lookup.parquet` | planSource → srbc_lat, srbc_lon, srbc_source_name (397 rows) |
| `nhd_feature_volume_summary.csv` | Per-NHD-feature withdrawal totals (high/good matches only) |
| `junction_dep_updated.parquet` | **Canonical output**: full junction table with DEP columns + reclassified types (49,363 rows) |
| `dep_match_results_all.parquet` | DEP match results for all 3,506 source candidates |
| `manual_curation.csv` | 319 residual planSources; 231/319 filled by user (844.5 Mgal) |

## External data
- **`skinny_df.parquet`** at `G:\My Drive\production\repos\openFF_data_2026_04_03\skinny_df.parquet`
  — large OpenFF dataset; used here only for well coordinates (`api10`, `bgLatitude`, `bgLongitude`)

## Notebooks

### `source_classifier.ipynb`
Classifies each `planSource` string into a type bucket using regex rules (priority order):

| Type | Key signals |
|---|---|
| `reuse` | recycled, flowback, rainwater |
| `interconnection` | intc, tap, vending, vault, hydrant, authority, municipal, meter |
| `groundwater` | well, spring, aquifer |
| `impoundment` | impoundment, pit |
| `surface_direct` | creek, river, run, stream, lake, pond, reservoir, brook, branch, fork, hollow, dam, hatchery |
| `srbc_only` | SRBC docket number present but no other type keyword |
| `dont_know` | recognized pattern but type unresolvable (SWW, SPWA, Aqua, WI, brine, quarry, Clermont, AWS, NKWA, MAWC, MANK) |
| `ambiguous` | no pattern matched |

Also extracts water feature names from SRBC permit strings (e.g.
`"Cabot, Tunkhannock Creek [SRBC Docket Number 20180605]"` → `"Tunkhannock Creek"`) and
joins well coordinates from `skinny_df` via `api10`.

Initial coverage by reported volume (before DEP reclassification): `surface_direct` 49%,
`impoundment` 16%, `interconnection` 15%, `dont_know` 11%, `ambiguous` 4%, `reuse` 3%,
`groundwater` 1%.

Final coverage after DEP matching + manual curation (see `dep_matcher.ipynb`):
`surface_direct` 54%, `interconnection` 22%, `impoundment` 18%, `reuse` 3%, `groundwater` 3%,
`ambiguous` 0.2%, `dont_know` 0%.

### `nhd_matcher.ipynb`
Matches NHD candidate sources (surface_direct + impoundment + srbc_only) to NHD features.

Steps:
1. Extract a clean `search_name` from each `planSource` (strips SRBC brackets, operator
   prefixes, state abbreviations, parentheticals; splits on comma / ` - ` / ` @ `)
2. Normalize names (expand abbreviations: N→North, Br→Branch, Unt→Unnamed Tributary, etc.)
3. Spatial filter: NHD features within 50 km of the coordinate (well proxy or source coord)
4. Fuzzy score: `rapidfuzz.fuzz.token_sort_ratio` on normalized names
5. Return best match per source

Results saved to `data/nhd_match_results.parquet`.

Four matching passes (see journal for detail):
1. **Main pass** — PA NHD only, well-proxy coords
2. **SRBC re-match** — precise docket PDF coords + SRBC-confirmed source name (3 runs per source)
3. **WV border re-match** — combined PA+WV NHD, Mon→Monongahela expansion
4. **DEP-assisted pass** — targets sources reclassified by `dep_matcher.ipynb`; uses DEP withdrawal-point coords and `dep_src` field as fallback search name (resolves SWW/WI entries e.g. "SUSQUEHANNA RIVER - SALSMAN" → Susquehanna River); 293 new/improved matches

## Key design decisions
- **Well coordinates as proxy**: source locations are largely unknown; well lat/lon from
  `skinny_df` (grouped by `api10`) serve as spatial constraint for NHD matching.
- **Median well coord per source**: for sources used by multiple wells, the median of all
  well coordinates is used as the proxy location.
- **Source coord preferred**: where the master table has a manually verified lat/lon,
  that takes precedence over the well proxy.
- **50 km search radius**: generous enough to accommodate the proxy coordinate uncertainty
  while still filtering out same-named streams in other parts of PA.

## Coordinate sources (priority, per planSource)
1. `dep_lat / dep_lon` — PA DEP withdrawal-point coordinates (62.9% of sources, 76.6% of volume)
2. `srbc_lat / srbc_lon` — SRBC docket PDF coordinates (2.8% of sources)
3. `master.Latitude/Longitude` — site_ID-joined master table coords (3.3% of sources)
4. Median `bgLatitude/bgLongitude` from `skinny_df` grouped by `planSource` — well proxy (32.3%)

Overall: 98.7% of unique planSources are geolocated (99.2% of volume).

## Output files (`data/`)

| File | Description |
|---|---|
| `nhd_match_results.parquet` | NHD match per unique candidate source: search_name, nhd_id, nhd_name, score, dist_km |
| `junction_nhd_matched.parquet` | Junction table with NHD match columns and `match_tier` (intermediate; superseded by `junction_dep_updated`) |
| `junction_dep_updated.parquet` | **Final output**: junction table with DEP match columns + reclassified source types |

Volume by match tier (all junction rows, after Pass 4): high ≥90: 51.4%, good 80-89: 2.5%, fair 60-79: 5.2%, low <60: 0.3%, unmatched: 40.6%.
Of surface/impoundment/SRBC candidates: high+good covers 74.6% of candidate volume (unmatched 17.8%, mostly operator-named impoundments with no stream name).

## Additional notebooks / scripts

### `srbc_docket_lookup.ipynb`
Downloads SRBC approval PDFs for all docket numbers in the junction table (72 unique dockets),
parses for withdrawal coordinates and approved source name. All 72 returned usable data.
Outputs: `srbc_docket_info.parquet`, `srbc_coords_lookup.parquet`.

### `extract_wv_nhd.py`
Downloads WV NHD GDB, extracts eastern WV named features (lon > -82.5), builds
`NHD_WV_named.gpkg` and `NHD_combined_named.gpkg` (PA + eastern WV, 189,708 features).

### `dep_matcher.ipynb`
Resolves `dont_know` and `ambiguous` planSources using PA DEP water resource point data
(`data/PA resources/WaterResources2026_01.geojson`, 24,037 withdrawal points). Two-pass
matching: operator-filtered first, global fallback for utility sources (Aqua, MAWC, etc.).
Applies `RULE_FIXES` list (brine→reuse, quarry→groundwater, date-suffix inheritance).
Exports 319 residuals to `manual_curation.csv` for manual entry (user filled 231/319,
844.5 Mgal). Reads `junction_nhd_matched.parquet`, writes `junction_dep_updated.parquet`.
DEP coordinate coverage: 57.1% of all junction rows (28,198/49,363).

### `analysis.ipynb`
Loads `junction_dep_updated.parquet` and produces:
- Volume by source type (pie chart)
- NHD match quality by volume (tier table)
- Top NHD features by withdrawal volume (bar chart)
- Volume by year and source type (area chart, joins skinny_df for dates)
- Well map colored by dominant source type (scatter, joins skinny_df for coords)
- Reuse fraction trend by year
- Volume by major river basin (Susquehanna, Monongahela, Ohio/Allegheny, etc.)
- DEP matching coverage + residual inventory (ambiguous, fair/low NHD matches)
- Geolocation coverage audit (section 10): coord source breakdown per planSource
- Export: `nhd_feature_volume_summary.csv` (163 NHD features, high/good matches, 53,724 Mgal)

## Next priority
**Classification coverage is essentially complete** — `dont_know` resolved to 0%, `ambiguous`
to 0.2% of volume. Remaining gaps:
- Remaining unmatched candidates (17.8% of candidate volume, ~12,600 Mgal): dominated by
  operator-named impoundments (YOUNG, Parys, ZEFFER, etc.) — no stream name available, likely
  the genuine floor without manual curation
- Downstream deliverables: `watershed_report.ipynb` is the recommended next build —
  stream-level withdrawal profiles, seasonal risk flags, operator ranking, filterable by HUC
