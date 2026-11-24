"""Location-based solar resource: PVGIS (primary) and NASA POWER (secondary). Estimates, not guarantees."""
from __future__ import annotations
import requests
from .schemas import ToolResult
from .calc import estimate_generation_manual, R

PVGIS_URL = "https://re.jrc.ec.europa.eu/api/v5_2/PVcalc"
NASA_URL = "https://power.larc.nasa.gov/api/temporal/climatology/point"
TIMEOUT = 20


def pvgis(lat: float, lon: float, pv_kwp: float, tilt: float | None, azimuth: float | None, loss_pct: float) -> ToolResult:
    params = {"lat": lat, "lon": lon, "peakpower": pv_kwp, "loss": loss_pct, "outputformat": "json", "pvtechchoice": "crystSi"}
    if tilt is None or azimuth is None:
        params["optimalangles"] = 1
    else:
        params.update(angle=tilt, aspect=azimuth)
    r = requests.get(PVGIS_URL, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    js = r.json()
    tot = js["outputs"]["totals"]["fixed"]
    monthly = [round(m["E_m"], 1) for m in js["outputs"]["monthly"]["fixed"]]
    return ToolResult(
        tool="estimate_solar_generation", inputs=dict(lat=lat, lon=lon, pv_kwp=pv_kwp, tilt=tilt, azimuth=azimuth, loss_pct=loss_pct),
        values={"annual_kwh": R(tot["E_y"], 0), "daily_kwh": R(tot["E_d"], 1), "monthly_kwh": monthly,
                "source": "PVGIS v5.2 (JRC) PVcalc"},
        warnings=["Estimate only — not guaranteed output."],
        assumptions=[f"Location {lat}, {lon}; system loss {loss_pct}%; "
                     + ("optimal tilt/azimuth chosen by PVGIS." if tilt is None or azimuth is None else f"tilt {tilt}°, azimuth {azimuth}° (0=south).")])


def nasa_power(lat: float, lon: float, pv_kwp: float, loss_pct: float) -> ToolResult:
    params = {"parameters": "ALLSKY_SFC_SW_DWN", "community": "RE", "longitude": lon, "latitude": lat, "format": "JSON"}
    r = requests.get(NASA_URL, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    ann = r.json()["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]["ANN"]  # kWh/m²/day on horizontal
    res = estimate_generation_manual(pv_kwp, ann, loss_pct)
    res.values["source"] = "NASA POWER climatology (horizontal irradiation, kWh/m²/day = peak sun hours)"
    res.assumptions.append("Horizontal-plane irradiation used; tilt/orientation gains/losses are not modelled.")
    return res


def estimate_generation(lat, lon, pv_kwp, tilt=None, azimuth=None, loss_pct=14.0, manual_psh=None) -> ToolResult:
    """PVGIS -> NASA POWER -> manual PSH (only if supplied). Service errors are shown, never hidden."""
    errors = []
    if lat is not None and lon is not None and pv_kwp:
        for fn, args in ((pvgis, (lat, lon, pv_kwp, tilt, azimuth, loss_pct)), (nasa_power, (lat, lon, pv_kwp, loss_pct))):
            try:
                return fn(*args)
            except Exception as e:  # network / schema
                errors.append(f"{fn.__name__}: {type(e).__name__}")
    if manual_psh:
        res = estimate_generation_manual(pv_kwp, manual_psh, loss_pct)
        if errors:
            res.warnings.append("Online solar-resource services unavailable (" + "; ".join(errors) + "); used your manual assumption.")
        return res
    return ToolResult(tool="estimate_solar_generation", status="incomplete",
                      missing=["latitude/longitude (or manual peak sun hours)"],
                      warnings=(["Solar-resource services unavailable: " + "; ".join(errors)] if errors else []),
                      inputs=dict(lat=lat, lon=lon, pv_kwp=pv_kwp))
