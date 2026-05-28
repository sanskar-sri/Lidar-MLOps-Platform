def classify_building_heights(gdf):
    gdf = gdf.copy()

    def _height_cat(h):
        if h < 6:
            return "Single storey"
        if h < 12:
            return "Low rise"
        if h < 25:
            return "Mid rise"
        return "High rise"

    gdf["height_category"] = gdf["height_range_m"].apply(_height_cat)
    if "estimated_floors" not in gdf.columns:
        gdf["estimated_floors"] = gdf["height_range_m"].apply(
            lambda h: max(1, int(h / 3.0))
        )
    return gdf


def compute_flood_exposure(gdf, flood_depths_m=None, terrain_z=None):
    if flood_depths_m is None:
        flood_depths_m = [0.5, 1.0, 2.0]
    gdf = gdf.copy()
    if terrain_z is None:
        terrain_z = float(gdf["z_min"].quantile(0.10))
    for depth in flood_depths_m:
        col = f"flood_exposed_{str(depth).replace('.', '_')}m"
        gdf[col] = gdf["z_min"] < (terrain_z + depth)
    gdf["terrain_z_used"] = terrain_z
    return gdf


def classify_detection_confidence(gdf):
    gdf = gdf.copy()

    def _conf_status(c):
        if c < 0:
            return "No confidence data"
        if c < 0.70:
            return "Field check required"
        if c < 0.85:
            return "Moderate confidence"
        return "High confidence"

    gdf["verification_status"] = gdf["confidence_mean"].apply(_conf_status)
    return gdf


def compute_rgb_proxy(gdf):
    gdf = gdf.copy()
    has_rgb = (
        "mean_r" in gdf.columns
        and "mean_g" in gdf.columns
        and "mean_b" in gdf.columns
        and not (gdf["mean_r"] == -1).all()
    )
    if not has_rgb:
        gdf["mean_brightness"] = None
        gdf["construction_era_proxy"] = None
        return gdf

    gdf["mean_brightness"] = (gdf["mean_r"] + gdf["mean_g"] + gdf["mean_b"]) / 3.0

    def _era_proxy(brightness):
        if brightness < 80:
            return "Older / darker material"
        if brightness < 150:
            return "Mixed"
        return "Modern / lighter material"

    gdf["construction_era_proxy"] = gdf["mean_brightness"].apply(_era_proxy)
    return gdf


def run_risk_assessment(gdf, flood_depths_m=None, terrain_z=None):
    if flood_depths_m is None:
        flood_depths_m = [0.5, 1.0, 2.0]

    gdf = classify_building_heights(gdf)
    gdf = compute_flood_exposure(gdf, flood_depths_m=flood_depths_m, terrain_z=terrain_z)
    gdf = classify_detection_confidence(gdf)
    gdf = compute_rgb_proxy(gdf)

    total = len(gdf)
    terrain_z_used = float(gdf["terrain_z_used"].iloc[0]) if total > 0 else 0.0

    height_dist = {
        cat: int((gdf["height_category"] == cat).sum())
        for cat in ["Single storey", "Low rise", "Mid rise", "High rise"]
    }

    flood_exposure = {}
    for depth in flood_depths_m:
        col = f"flood_exposed_{str(depth).replace('.', '_')}m"
        key = f"{str(depth).replace('.', '_')}m"
        exposed_n = int(gdf[col].sum()) if col in gdf.columns else 0
        flood_exposure[key] = {
            "exposed": exposed_n,
            "pct": round(exposed_n / total * 100, 1) if total > 0 else 0.0,
        }

    conf_dist = {
        cat: int((gdf["verification_status"] == cat).sum())
        for cat in [
            "High confidence",
            "Moderate confidence",
            "Field check required",
            "No confidence data",
        ]
    }

    has_rgb = (
        "mean_r" in gdf.columns
        and not (gdf["mean_r"] == -1).all()
    )

    return {
        "gdf": gdf,
        "summary": {
            "total_buildings": total,
            "height_distribution": height_dist,
            "flood_exposure": flood_exposure,
            "confidence_distribution": conf_dist,
            "terrain_z_used": terrain_z_used,
            "has_rgb": bool(has_rgb),
        },
    }
