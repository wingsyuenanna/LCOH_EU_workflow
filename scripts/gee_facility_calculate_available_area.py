from __future__ import annotations

import argparse
from pathlib import Path

import ee


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Script version of GEE_facility_calculate_available_area.ipynb (exports land-cover area within a buffer for each facility point)."
    )
    p.add_argument(
        "--asset",
        required=True,
        help="Earth Engine FeatureCollection asset id containing facility points (must include source_id/source_name).",
    )
    p.add_argument("--buffer-m", type=int, default=15000, help="Buffer radius in meters (e.g., 5000 or 15000).")
    p.add_argument("--slope-max-deg", type=float, default=5.0, help="Maximum slope for suitability mask.")
    p.add_argument("--wdpa-padding-m", type=int, default=100, help="Padding applied to protected areas mask.")
    p.add_argument("--scale", type=int, default=30, help="Scale in meters for reduceRegion.")
    p.add_argument(
        "--drive-description",
        default=None,
        help="GEE Drive export task description. Default: LandCover_Area_Categorized_<km>km",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    ee.Initialize()

    wdpa_statuses = ["Designated", "Inscribed", "Established"]

    points = ee.FeatureCollection(args.asset)
    wc = ee.Image("ESA/WorldCover/v200/2021").select("Map")
    elev = ee.Image("USGS/SRTMGL1_003")
    wdpa = ee.FeatureCollection("WCMC/WDPA/current/polygons").filter(
        ee.Filter.inList("STATUS", wdpa_statuses)
    )

    slope_mask = ee.Terrain.slope(elev).lte(args.slope_max_deg)
    wdpa_mask = (
        ee.Image()
        .paint(wdpa, 1)
        .unmask(0)
        .focal_max(radius=args.wdpa_padding_m, units="meters")
    )
    protected_mask = wdpa_mask.eq(0)
    environmental_mask = slope_mask.And(protected_mask)

    class_names = {
        "10": "tree_cover_km2",
        "20": "shrubland_km2",
        "30": "grassland_km2",
        "40": "cropland_km2",
        "50": "built_up_km2",
        "60": "bare_sparse_km2",
        "70": "snow_ice_km2",
        "80": "water_km2",
        "90": "wetland_km2",
        "95": "mangroves_km2",
        "100": "moss_lichen_km2",
    }
    ee_class_names = ee.Dictionary(class_names)

    area_by_lc_image = (
        ee.Image.pixelArea()
        .updateMask(environmental_mask)
        .addBands(wc)
        .rename(["area", "lc"])
    )

    def calculate_grouped_area(feature):
        buffer = feature.geometry().buffer(args.buffer_m)
        stats = area_by_lc_image.reduceRegion(
            reducer=ee.Reducer.sum().group(groupField=1, groupName="class"),
            geometry=buffer,
            scale=args.scale,
            maxPixels=1e13,
        )
        stats_list = ee.List(stats.get("groups"))

        def rename_columns(group, d):
            g = ee.Dictionary(group)
            class_val = g.getNumber("class").format()
            area_km2 = g.getNumber("sum").divide(1e6)
            default_name = ee.String("class_").cat(class_val)
            name = ee.String(ee_class_names.get(class_val, default_name))
            return ee.Dictionary(d).set(name, area_km2)

        land_cover_results = ee.Dictionary(stats_list.iterate(rename_columns, ee.Dictionary({})))
        return feature.set(land_cover_results)

    results = points.map(calculate_grouped_area)

    id_columns = ["source_name", "source_id"]
    selector_list = id_columns + ["system:index"] + list(class_names.values())

    km = args.buffer_m // 1000
    description = args.drive_description or f"LandCover_Area_Categorized_{km}km"

    task = ee.batch.Export.table.toDrive(
        collection=results,
        description=description,
        fileFormat="CSV",
        selectors=selector_list,
    )
    task.start()
    print(f"Started GEE Drive export task: {description}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

