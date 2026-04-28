"""
FPW Completion Data Explorer — local spike
Run with: streamlit run streamlit_app.py
"""

import streamlit as st
import pandas as pd
import geopandas as gpd
import pydeck as pdk
import numpy as np
import json
from shapely.geometry import mapping

# ── Paths ────────────────────────────────────────────────────────────────────
JUNCTION_PATH = "data/junction_dep_updated.parquet"
SKINNY_PATH   = r"G:\My Drive\production\repos\openFF_data_2026_04_03\skinny_df.parquet"
NHD_PATH      = "data/NHD_PA_named.gpkg"

# ── Colors ───────────────────────────────────────────────────────────────────
TYPE_COLOR = {
    "surface_direct":  [30,  120, 210, 180],
    "impoundment":     [170, 100,  40, 180],
    "interconnection": [220, 130,   0, 180],
    "reuse":           [40,  170,  80, 180],
    "groundwater":     [180, 180,   0, 180],
    "srbc_only":       [80,  200, 200, 180],
    "ambiguous":       [160, 160, 160, 180],
    "no_source":       [200, 200, 200,  80],
}
DEFAULT_COLOR = [200, 200, 200, 120]
WELL_OUTLINE    = [210, 50, 50, 240]   # frac wells: solid red outline

# ── Data loading ─────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Loading completion data…")
def load_junction():
    jt = pd.read_parquet(JUNCTION_PATH)
    skinny = pd.read_parquet(
        SKINNY_PATH,
        columns=["api10", "bgLatitude", "bgLongitude", "date"]
    )
    well_info = (
        skinny.groupby("api10")
        .agg(well_lat=("bgLatitude", "median"),
             well_lon=("bgLongitude", "median"),
             date=("date", "first"))
        .reset_index()
    )
    well_info["year"] = pd.to_datetime(well_info["date"], errors="coerce").dt.year
    jt = jt.merge(well_info, on="api10", how="left")
    jt["volume_Mgal"] = jt["volume"] / 1_000_000
    return jt


@st.cache_data(show_spinner="Loading NHD features…")
def load_nhd_features(gnis_names: tuple):
    """Load all NHD segments whose gnis_name is in the matched set.
    Using gnis_name (not permanent_identifier) shows the full named stream,
    not just the single matched segment."""
    name_set = set(gnis_names)
    frames = []
    for layer in ("NHDFlowline", "NHDWaterbody"):
        gdf = gpd.read_file(NHD_PATH, layer=layer)
        subset = gdf[gdf["gnis_name"].isin(name_set)].copy()
        if not subset.empty:
            subset = subset.to_crs(epsg=4326)
            frames.append(subset[["gnis_name", "geometry"]])
    if frames:
        return pd.concat(frames, ignore_index=True)
    return gpd.GeoDataFrame(columns=["gnis_name", "geometry"])


def to_geojson_features(gdf, volume_lookup: dict) -> list:
    """Convert GeoDataFrame rows to GeoJSON feature dicts for pydeck.
    volume_lookup is keyed by gnis_name."""
    features = []
    for _, row in gdf.iterrows():
        vol = volume_lookup.get(row["gnis_name"], 0)
        features.append({
            "type": "Feature",
            "geometry": mapping(row["geometry"].simplify(0.001)),
            "properties": {
                "name": row["gnis_name"],
                "volume_Mgal": round(vol, 1),
            }
        })
    return features


# ── App ───────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="FPW Explorer", layout="wide")
st.title("PA Fracking Water Source Explorer")

jt = load_junction()

# ── Sidebar filters ───────────────────────────────────────────────────────────
st.sidebar.header("Filters")

operators = sorted(jt["operator_clean"].dropna().unique())
sel_operators = st.sidebar.multiselect("Operator", operators, placeholder="All operators")

source_types = sorted(jt["source_type"].dropna().unique())
sel_types = st.sidebar.multiselect("Source type", source_types, default=source_types,
                                   placeholder="All types")

year_min = int(jt["year"].dropna().min())
year_max = int(jt["year"].dropna().max())
# Fracking in PA started ~2005; clip the lower default
default_min = max(year_min, 2005)
sel_years = st.sidebar.slider("Completion year", year_min, year_max,
                               (default_min, year_max))

tiers = ["high (>=90)", "good (80-89)", "fair (60-79)", "low (<60)", "unmatched"]
sel_tiers = st.sidebar.multiselect("NHD match tier", tiers, default=tiers,
                                   placeholder="All tiers")

st.sidebar.markdown("---")
show_wells    = st.sidebar.checkbox("Show frac wells", value=True)
show_sources  = st.sidebar.checkbox("Show water source points", value=True)
show_nhd      = st.sidebar.checkbox("Show NHD stream features", value=True)

# ── Filter data ───────────────────────────────────────────────────────────────
mask = pd.Series(True, index=jt.index)
if sel_operators:
    mask &= jt["operator_clean"].isin(sel_operators)
if sel_types:
    mask &= jt["source_type"].isin(sel_types)
if sel_tiers:
    mask &= jt["match_tier"].isin(sel_tiers)
mask &= jt["year"].between(sel_years[0], sel_years[1], inclusive="both") | jt["year"].isna()

df = jt[mask].copy()

# ── Summary metrics ───────────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total volume (Mgal)", f"{df['volume_Mgal'].sum():,.0f}")
col2.metric("Fracking wells", f"{df['api10'].nunique():,}")
col3.metric("Unique water sources", f"{df['planSource'].nunique():,}")
col4.metric("Operators", f"{df['operator_clean'].nunique():,}")

st.markdown("---")

# ── Build map layers ──────────────────────────────────────────────────────────
layers = []

# Well layer — one point per api10, sized by total volume
if show_wells:
    well_agg = (
        df.dropna(subset=["well_lat", "well_lon"])
        .groupby(["api10", "well_lat", "well_lon", "operator_clean"])
        .agg(volume_Mgal=("volume_Mgal", "sum"))
        .reset_index()
    )
    # Build square polygons (size in degrees, ~500m half-width scaled by volume)
    def make_square(lon, lat, half_deg):
        return [[lon - half_deg, lat - half_deg],
                [lon + half_deg, lat - half_deg],
                [lon + half_deg, lat + half_deg],
                [lon - half_deg, lat + half_deg]]

    well_agg["half_deg"] = (
        np.sqrt(well_agg["volume_Mgal"].clip(lower=1)) * 0.0011
    )
    well_agg["polygon"] = well_agg.apply(
        lambda r: make_square(r.well_lon, r.well_lat, r.half_deg), axis=1
    )

    layers.append(pdk.Layer(
        "PolygonLayer",
        data=well_agg,
        get_polygon="polygon",
        get_fill_color=[0, 0, 0, 0],      # fully transparent fill
        get_line_color=WELL_OUTLINE,
        stroked=True,
        filled=True,
        line_width_min_pixels=1,
        pickable=True,
        id="wells",
    ))

# Source point layer — dep_lat/dep_lon where available
if show_sources:
    src = (
        df.dropna(subset=["dep_lat", "dep_lon"])
        .groupby(["planSource", "dep_lat", "dep_lon", "source_type"])
        .agg(volume_Mgal=("volume_Mgal", "sum"))
        .reset_index()
    )
    src["color"] = src["source_type"].map(TYPE_COLOR).apply(
        lambda c: c if isinstance(c, list) else DEFAULT_COLOR
    )
    layers.append(pdk.Layer(
        "ScatterplotLayer",
        data=src,
        get_position=["dep_lon", "dep_lat"],
        get_radius=600,
        get_fill_color="color",
        pickable=True,
        opacity=0.85,
        id="sources",
    ))

# NHD line/polygon layer — matched features colored by volume
if show_nhd:
    matched = df[df["match_tier"].isin(["high (>=90)", "good (80-89)"])]
    gnis_names = tuple(matched["nhd_name"].dropna().unique())
    if gnis_names:
        nhd_gdf = load_nhd_features(gnis_names)
        if not nhd_gdf.empty:
            # Build per-name coordinate lookup from dep coords (well proxy fallback)
            # Only keep NHD segments within ~50km of a source that matched that name
            name_coords: dict[str, list] = {}
            for _, row in matched.iterrows():
                name = row["nhd_name"]
                if pd.notna(row.get("dep_lat")) and pd.notna(row.get("dep_lon")):
                    name_coords.setdefault(name, []).append((row["dep_lon"], row["dep_lat"]))
                elif pd.notna(row.get("well_lon")) and pd.notna(row.get("well_lat")):
                    name_coords.setdefault(name, []).append((row["well_lon"], row["well_lat"]))

            MAX_DEG = 0.5  # ~50 km

            def near_source(row):
                coords = name_coords.get(row["gnis_name"], [])
                if not coords:
                    return False
                cx = row["geometry"].centroid.x
                cy = row["geometry"].centroid.y
                return any(
                    abs(cx - lon) < MAX_DEG and abs(cy - lat) < MAX_DEG
                    for lon, lat in coords
                )

            nhd_gdf = nhd_gdf[nhd_gdf.apply(near_source, axis=1)]

            # Volume per named stream within current filter
            nhd_vol = (
                matched.groupby("nhd_name")["volume_Mgal"]
                .sum()
                .to_dict()
            )
            geojson_features = to_geojson_features(nhd_gdf, nhd_vol)
            geojson = {"type": "FeatureCollection", "features": geojson_features}

            layers.append(pdk.Layer(
                "GeoJsonLayer",
                data=geojson,
                get_line_color=[0, 60, 180, 220],
                get_fill_color=[0, 60, 180, 60],
                line_width_min_pixels=2,
                pickable=True,
                id="nhd",
            ))

# ── Render map ────────────────────────────────────────────────────────────────
view = pdk.ViewState(latitude=41.2, longitude=-77.5, zoom=7, pitch=0)

tooltip = {
    "html": """
        <b>{name}</b><br/>
        {volume_Mgal} Mgal<br/>
        {operator_clean}<br/>
        {planSource}
    """,
    "style": {"backgroundColor": "#1a1a2e", "color": "white", "fontSize": "12px"}
}

deck = pdk.Deck(
    layers=layers,
    initial_view_state=view,
    tooltip=tooltip,
    map_style="https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
)
st.pydeck_chart(deck, use_container_width=True)

# ── Legend ────────────────────────────────────────────────────────────────────
with st.expander("Color legend — source type"):
    legend_cols = st.columns(4)
    for i, (stype, color) in enumerate(TYPE_COLOR.items()):
        r, g, b = color[:3]
        legend_cols[i % 4].markdown(
            f'<span style="background:rgb({r},{g},{b});padding:2px 10px;'
            f'border-radius:4px;color:white;font-size:12px">{stype}</span>',
            unsafe_allow_html=True,
        )

st.markdown("---")

# ── Tables ────────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["Top NHD features", "Top operators", "Top sources"])

with tab1:
    nhd_summary = (
        df[df["match_tier"].isin(["high (>=90)", "good (80-89)"])]
        .groupby(["nhd_name", "nhd_layer"])
        .agg(volume_Mgal=("volume_Mgal", "sum"), wells=("api10", "nunique"))
        .sort_values("volume_Mgal", ascending=False)
        .reset_index()
        .head(30)
    )
    nhd_summary["volume_Mgal"] = nhd_summary["volume_Mgal"].round(1)
    st.dataframe(nhd_summary, use_container_width=True, hide_index=True)

with tab2:
    op_summary = (
        df.groupby("operator_clean")
        .agg(volume_Mgal=("volume_Mgal", "sum"),
             wells=("api10", "nunique"),
             sources=("planSource", "nunique"))
        .sort_values("volume_Mgal", ascending=False)
        .reset_index()
        .head(30)
    )
    op_summary["volume_Mgal"] = op_summary["volume_Mgal"].round(1)
    st.dataframe(op_summary, use_container_width=True, hide_index=True)

with tab3:
    src_summary = (
        df.groupby(["planSource", "source_type", "nhd_name", "match_tier"])
        .agg(volume_Mgal=("volume_Mgal", "sum"), wells=("api10", "nunique"))
        .sort_values("volume_Mgal", ascending=False)
        .reset_index()
        .head(50)
    )
    src_summary["volume_Mgal"] = src_summary["volume_Mgal"].round(1)
    st.dataframe(src_summary, use_container_width=True, hide_index=True)
