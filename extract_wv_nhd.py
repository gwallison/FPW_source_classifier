"""
Extract named NHD features from the WV state GDB, filtered to the area
near the PA border (lon > -82, lat > 38.5), and save to NHD_WV_named.gpkg.
Then rebuild NHD_combined_named.gpkg = PA + WV named features.
"""
import geopandas as gpd
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

WV_ZIP  = 'data/NHD_H_West_Virginia_State_GDB.zip'
PA_GPK  = 'data/NHD_PA_named.gpkg'
WV_GPK  = 'data/NHD_WV_named.gpkg'
CMB_GPK = 'data/NHD_combined_named.gpkg'

# Bounding box to keep: eastern WV only (within ~100 km of PA border)
# PA/WV border lon ~= -80.5; keep lon > -82.0 to be generous
LON_MIN, LAT_MIN = -82.5, 38.5

print('Reading WV NHD flowlines...')
fl = gpd.read_file(f'zip://{WV_ZIP}!NHD_H_West_Virginia_State_GDB.gdb',
                   layer='NHDFlowline')
print(f'  Total flowlines: {len(fl):,}')

print('Reading WV NHD waterbodies...')
wb = gpd.read_file(f'zip://{WV_ZIP}!NHD_H_West_Virginia_State_GDB.gdb',
                   layer='NHDWaterbody')
print(f'  Total waterbodies: {len(wb):,}')

# Keep only named features in the eastern WV region
def filter_named_east(gdf, label):
    named = gdf[gdf['gnis_name'].notna() & (gdf['gnis_name'].str.strip() != '')].copy()
    named = named.to_crs('EPSG:4326')
    cx = named.geometry.centroid.x
    cy = named.geometry.centroid.y
    east = named[(cx > LON_MIN) & (cy > LAT_MIN)].copy()
    east['layer'] = label
    print(f'  {label}: {len(named):,} named -> {len(east):,} in eastern WV')
    return east[['permanent_identifier', 'gnis_name', 'ftype', 'layer', 'geometry']]

fl_named = filter_named_east(fl, 'NHDFlowline')
wb_named = filter_named_east(wb, 'NHDWaterbody')

wv_named = pd.concat([fl_named, wb_named], ignore_index=True)
print(f'\nTotal WV named features (eastern): {len(wv_named):,}')

wv_named.to_file(WV_GPK, driver='GPKG')
print(f'Saved: {WV_GPK}')

# Combine with PA
print('\nLoading PA NHD...')
pa_fl = gpd.read_file(PA_GPK, layer='NHDFlowline')
pa_wb = gpd.read_file(PA_GPK, layer='NHDWaterbody')
pa_named = pd.concat([pa_fl, pa_wb], ignore_index=True)
pa_named['layer'] = pa_named.get('layer', pd.Series(['NHDFlowline']*len(pa_fl) + ['NHDWaterbody']*len(pa_wb)))
print(f'PA named features: {len(pa_named):,}')

# For combined, drop geometry column name conflicts and concat
wv_save = wv_named[['permanent_identifier', 'gnis_name', 'ftype', 'layer', 'geometry']].copy()
pa_save = pa_named[['permanent_identifier', 'gnis_name', 'ftype', 'layer', 'geometry']].copy()

combined = pd.concat([pa_save, wv_save], ignore_index=True)
combined = gpd.GeoDataFrame(combined, crs='EPSG:4326')
print(f'Combined PA+WV: {len(combined):,} named features')

combined.to_file(CMB_GPK, driver='GPKG')
print(f'Saved: {CMB_GPK}')
