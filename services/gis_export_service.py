import json
from pathlib import Path

from services.b2_paths import b2_prefix as _b2_prefix


def get_epsg_for_dataset(dataset_id: str) -> int:
    if dataset_id == "paris-lille-id-1":
        return 32631
    if dataset_id == "torronto-id-1":
        return 32617
    raise ValueError(
        f"Unknown dataset_id '{dataset_id}': no EPSG CRS mapping defined. "
        "Expected 'paris-lille-id-1' (EPSG:32631) or "
        "'torronto-id-1' (EPSG:32617)."
    )


def get_coord_offset(dataset_id: str, prep_version: str) -> list:
    label_map_path = (
        Path("data/local_staging/gold_outputs")
        / dataset_id / prep_version / "artifacts" / "meta" / "label_map.json"
    )
    try:
        data = json.loads(label_map_path.read_text(encoding="utf-8"))
        offset = data.get("coord_offset_subtracted")
        if offset and len(offset) >= 3:
            return [float(offset[0]), float(offset[1]), float(offset[2])]
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"[GIS EXPORT WARN] Could not read coord_offset from {label_map_path}: {exc}")
    return [0.0, 0.0, 0.0]


def load_prediction_ply(ply_path: str) -> dict:
    from plyfile import PlyData
    import numpy as np

    pdata = PlyData.read(ply_path)
    el = pdata["vertex"]
    names = el.data.dtype.names

    xyz = np.stack(
        [el["x"].astype(np.float64), el["y"].astype(np.float64), el["z"].astype(np.float64)],
        axis=1,
    )
    predicted_label = el["predicted_label"].astype(np.int8)

    confidence = None
    if "confidence" in names:
        confidence = el["confidence"].astype(np.float32)

    rgb = None
    if all(c in names for c in ("red", "green", "blue")):
        rgb = np.stack(
            [el["red"].astype(np.uint8), el["green"].astype(np.uint8), el["blue"].astype(np.uint8)],
            axis=1,
        )

    return {
        "xyz": xyz,
        "predicted_label": predicted_label,
        "confidence": confidence,
        "rgb": rgb,
        "n_points": len(xyz),
    }


def cluster_building_points(xyz, predicted_label, eps_m: float = 1.5, min_samples: int = 50):
    import numpy as np
    from sklearn.cluster import DBSCAN

    mask = predicted_label == 1
    building_xyz = xyz[mask]
    if len(building_xyz) == 0:
        return building_xyz, np.array([], dtype=np.int64)

    cluster_labels = DBSCAN(
        eps=eps_m, min_samples=min_samples, algorithm="ball_tree", metric="euclidean"
    ).fit_predict(building_xyz[:, :2])
    return building_xyz, cluster_labels


def build_building_geodataframe(
    building_xyz,
    cluster_labels,
    confidence_pts,
    rgb_pts,
    coord_offset: list,
    epsg_source: int,
    dataset_id: str,
    prep_version: str,
    model: str,
    run_id: str,
):
    import numpy as np
    import geopandas as gpd
    from shapely.geometry import MultiPoint, Polygon
    from pyproj import Transformer

    transformer = Transformer.from_crs(f"EPSG:{epsg_source}", "EPSG:4326", always_xy=True)
    unique_labels = [lbl for lbl in np.unique(cluster_labels) if lbl != -1]
    rows = []

    for label in unique_labels:
        mask = cluster_labels == label
        pts = building_xyz[mask]

        centroid_x = float(np.mean(pts[:, 0]))
        centroid_y = float(np.mean(pts[:, 1]))
        z_min = float(np.min(pts[:, 2]))
        z_max = float(np.max(pts[:, 2]))
        height_range_m = z_max - z_min
        estimated_floors = max(1, int(height_range_m / 3.0))
        point_count = len(pts)

        footprint_area_m2 = 0.0
        try:
            hull_local = MultiPoint(pts[:, :2]).convex_hull
            if hull_local.geom_type == "Polygon":
                footprint_area_m2 = float(hull_local.area)
        except Exception as exc:
            print(f"[GIS EXPORT WARN] Footprint area failed for cluster {label}: {exc}")

        confidence_mean = (
            float(np.mean(confidence_pts[mask])) if confidence_pts is not None else -1.0
        )

        if rgb_pts is not None:
            cluster_rgb = rgb_pts[mask]
            mean_r = int(np.mean(cluster_rgb[:, 0]))
            mean_g = int(np.mean(cluster_rgb[:, 1]))
            mean_b = int(np.mean(cluster_rgb[:, 2]))
        else:
            mean_r, mean_g, mean_b = -1, -1, -1

        real_x = pts[:, 0] + coord_offset[0]
        real_y = pts[:, 1] + coord_offset[1]
        try:
            hull_real = MultiPoint(np.column_stack([real_x, real_y])).convex_hull
            if hull_real.geom_type != "Polygon":
                print(f"[GIS EXPORT WARN] Cluster {label} hull is {hull_real.geom_type}, skipping")
                continue
            coords = list(hull_real.exterior.coords)
            lon_arr, lat_arr = transformer.transform(
                [c[0] for c in coords], [c[1] for c in coords]
            )
            geom = Polygon(zip(lon_arr, lat_arr))
        except Exception as exc:
            print(f"[GIS EXPORT WARN] Geometry build failed for cluster {label}: {exc}")
            continue

        rows.append({
            "cluster_id": int(label),
            "dataset_id": dataset_id,
            "prep_version": prep_version,
            "model": model,
            "run_id": run_id,
            "point_count": point_count,
            "footprint_area_m2": footprint_area_m2,
            "z_min": z_min,
            "z_max": z_max,
            "height_range_m": height_range_m,
            "estimated_floors": estimated_floors,
            "centroid_x_utm": centroid_x,
            "centroid_y_utm": centroid_y,
            "confidence_mean": confidence_mean,
            "mean_r": mean_r,
            "mean_g": mean_g,
            "mean_b": mean_b,
            "geometry": geom,
        })

    _COLS = [
        "cluster_id", "dataset_id", "prep_version", "model", "run_id",
        "point_count", "footprint_area_m2", "z_min", "z_max", "height_range_m",
        "estimated_floors", "centroid_x_utm", "centroid_y_utm",
        "confidence_mean", "mean_r", "mean_g", "mean_b", "geometry",
    ]
    if not rows:
        return gpd.GeoDataFrame(columns=_COLS, geometry="geometry", crs="EPSG:4326")
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def export_geojson(gdf, output_path: str) -> str:
    gdf.to_file(output_path, driver="GeoJSON")
    return output_path


def export_geoparquet(gdf, output_path: str) -> str:
    gdf.to_parquet(
        output_path,
        geometry_encoding="WKB",
        schema_version="1.1.0",
        write_covering_bbox=True,
    )
    return output_path


def upload_gis_exports_to_b2(
    dataset_id: str,
    prep_version: str,
    model: str,
    run_id: str,
    local_paths: list,
) -> list:
    from services.b2_service import get_b2_bucket

    bucket = get_b2_bucket()
    prefix = f"{_b2_prefix('gis_exports')}/{dataset_id}/{prep_version}/{model}/{run_id}/"
    results = []
    for local_path in local_paths:
        fname = Path(local_path).name
        b2_path = prefix + fname
        try:
            bucket.upload_local_file(local_file=local_path, file_name=b2_path)
            results.append({"local": local_path, "b2_path": b2_path, "ok": True})
        except Exception as exc:
            print(f"[GIS EXPORT B2 WARN] Upload failed for {local_path}: {exc}")
            results.append({"local": local_path, "b2_path": b2_path, "ok": False})
    return results


def run_gis_export_pipeline(
    dataset_id: str,
    prep_version: str,
    model: str,
    run_id: str,
    prediction_ply_path: str,
    output_dir: str,
    upload_to_b2: bool = True,
) -> dict:
    import numpy as np

    result = {
        "ok": False,
        "dataset_id": dataset_id,
        "buildings_detected": 0,
        "noise_points": 0,
        "geojson_path": "",
        "geoparquet_path": "",
        "b2_uploads": [],
        "error": None,
    }

    try:
        if not Path(prediction_ply_path).exists():
            result["error"] = f"PLY not found at path: {prediction_ply_path}"
            return result

        coord_offset = get_coord_offset(dataset_id, prep_version)
        if coord_offset == [0.0, 0.0, 0.0] and dataset_id == "paris-lille-id-1":
            print(
                "[GIS EXPORT WARN] coord_offset is [0,0,0] for paris-lille-id-1 "
                "— real-world coordinates may be wrong"
            )

        epsg_source = get_epsg_for_dataset(dataset_id)
        data = load_prediction_ply(prediction_ply_path)
        building_xyz, cluster_labels = cluster_building_points(
            data["xyz"], data["predicted_label"]
        )

        if len(building_xyz) < 10:
            result["error"] = "Too few building points"
            return result

        unique_labels = np.unique(cluster_labels)
        if len(unique_labels[unique_labels != -1]) == 0:
            result["error"] = "No clusters found — try lower eps or min_samples"
            return result

        noise_count = int(np.sum(cluster_labels == -1))
        building_mask = data["predicted_label"] == 1
        confidence_pts = data["confidence"][building_mask] if data["confidence"] is not None else None
        rgb_pts = data["rgb"][building_mask] if data["rgb"] is not None else None

        gdf = build_building_geodataframe(
            building_xyz, cluster_labels,
            confidence_pts, rgb_pts,
            coord_offset, epsg_source,
            dataset_id, prep_version, model, run_id,
        )

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        geojson_path = str(out_dir / "buildings.geojson")
        geoparquet_path = str(out_dir / "buildings.parquet")

        export_geojson(gdf, geojson_path)
        export_geoparquet(gdf, geoparquet_path)

        b2_uploads = []
        if upload_to_b2:
            b2_uploads = upload_gis_exports_to_b2(
                dataset_id, prep_version, model, run_id,
                [geojson_path, geoparquet_path],
            )

        result.update({
            "ok": True,
            "buildings_detected": len(gdf),
            "noise_points": noise_count,
            "geojson_path": geojson_path,
            "geoparquet_path": geoparquet_path,
            "b2_uploads": b2_uploads,
        })

    except Exception as exc:
        result["error"] = str(exc)

    return result
