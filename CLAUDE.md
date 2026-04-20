# FPW Source Classifier

## Project overview
Connect Pennsylvania fracking completion reports to water feature data. Completion reports
list the water sources used for each fracking job (free-text name + volume). The goal is to
classify those sources by type and link named surface-water sources to features in the USGS
National Hydrography Dataset (NHD) for geographic/watershed analysis.

## Background
An earlier project scraped PA DEP fracking completion reports. Attempts to link source names
to PA DEP water databases (PASDA, WMPDU) were abandoned due to unreliable/incomplete DEP data
and inconsistent naming. This project pivots to USGS NHD as the reference dataset instead.

## Data files (`data/`)

| File | Description |
|---|---|
| `well_junction_table.parquet` | ~49K rows linking fracking wells (`api10`) to water sources (`planSource`, `volume`, `site_ID`) |
| `FPW_master_water_source.parquet` | 2,882 unique water sources with coordinates (partially manually curated) and PA DEP flags |
| `NHD_H_Pennsylvania_State_GDB.zip` | Raw USGS NHD download for Pennsylvania (247 MB) |
| `NHD_PA_named.gpkg` | Extracted NHD named features only: NHDFlowline (127,509) + NHDWaterbody (2,863), WGS84 |
| `nhd_match_results.parquet` | NHD match results per unique source: search_name, nhd_name, score, dist_km, nhd_id |

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

Coverage by reported volume: `surface_direct` 49%, `impoundment` 16%, `interconnection` 15%,
`dont_know` 11%, `ambiguous` 4%, `reuse` 3%, `groundwater` 1%.

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

Match quality (1,035 candidates with extractable name):
- Score ≥ 90: 724 (70%)
- Score ≥ 80: 797 (77%)
- Score < 60:  29 (<3%)

## Key design decisions
- **Well coordinates as proxy**: source locations are largely unknown; well lat/lon from
  `skinny_df` (grouped by `api10`) serve as spatial constraint for NHD matching.
- **Median well coord per source**: for sources used by multiple wells, the median of all
  well coordinates is used as the proxy location.
- **Source coord preferred**: where the master table has a manually verified lat/lon,
  that takes precedence over the well proxy.
- **50 km search radius**: generous enough to accommodate the proxy coordinate uncertainty
  while still filtering out same-named streams in other parts of PA.

## Coordinate sources (priority)
1. `master.Latitude/Longitude` — manually curated source coordinates (15 sources)
2. Median `bgLatitude/bgLongitude` from `skinny_df` grouped by `planSource` — well proxy

## Output files (`data/`)

| File | Description |
|---|---|
| `nhd_match_results.parquet` | NHD match per unique candidate source: search_name, nhd_id, nhd_name, score, dist_km |
| `junction_nhd_matched.parquet` | Full junction table (49,363 rows) with NHD match columns and `match_tier` added |

Volume by match tier (all junction rows): high ≥90: 37%, good 80-89: 1.3%, fair 60-79: 9.3%, low <60: 0.5%, unmatched: 51.9%.
Of surface/impoundment/SRBC candidates: high+good covers 58.5% of candidate volume.

## Known gaps / future work
- ~570 NHD candidates had no extractable feature name (mostly impoundments); SRBC docket
  lookup could fill some coordinate/name gaps
- WV NHD data not yet downloaded; sources near PA/WV border (Fish Creek WV, Monongahela)
  may need it
- `dont_know` bucket (11% of volume) is correctly flagged as unresolvable with current data;
  some entries (SWW, WI) might be resolvable with additional reference data
- `ambiguous` bucket (4% of volume) contains ~626 unique unrecognized strings worth future review
