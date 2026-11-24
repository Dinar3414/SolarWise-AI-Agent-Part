"""Deterministic engineering tools (C3). The LLM never does electrical arithmetic.

Every function: explicit units in names, validated bounds, assumptions stated, and
missing inputs -> status="incomplete" (never a guess).
"""
from __future__ import annotations
import math
from typing import Optional
from .schemas import ToolResult, ModuleSpec, InverterSpec

R = lambda x, n=2: round(float(x), n)  # noqa: E731


def _need(**kw) -> list[str]:
    return [k for k, v in kw.items() if v is None]


def _bad(tool: str, msg: str, **inputs) -> ToolResult:
    return ToolResult(tool=tool, status="fail", warnings=[msg], inputs=inputs)


# ---------------------------------------------------------------- panel count
def calculate_panel_count(target_kw: float, module_wp: float) -> ToolResult:
    """Minimum module count so that installed DC kWp >= target (target is a minimum)."""
    inp = dict(target_kw=target_kw, module_wp=module_wp)
    if target_kw is None or module_wp is None:
        return ToolResult(tool="calculate_panel_count", status="incomplete",
                          missing=_need(target_kw=target_kw, module_wp=module_wp), inputs=inp)
    if target_kw <= 0 or module_wp <= 0:
        return _bad("calculate_panel_count", "Target and module rating must be positive.", **inp)
    if module_wp < 100:
        return _bad("calculate_panel_count",
                    "Module rating looks like kW/kWp. Enter watts per module (e.g. 615).", **inp)
    n = math.ceil(round(target_kw * 1000 / module_wp, 9))
    installed = n * module_wp / 1000
    warns = []
    if installed - target_kw > module_wp / 1000:
        warns.append("Installed capacity exceeds the target by more than one module.")
    return ToolResult(
        tool="calculate_panel_count", inputs=inp, warnings=warns,
        values={"module_count": n, "installed_kwp": R(installed, 2),
                "oversize_kwp": R(installed - target_kw, 2)},
        assumptions=["Target kW is treated as the MINIMUM DC capacity."])


def calculate_pv_capacity(module_count: int, module_wp: float) -> ToolResult:
    inp = dict(module_count=module_count, module_wp=module_wp)
    if module_count is None or module_wp is None:
        return ToolResult(tool="calculate_pv_capacity", status="incomplete",
                          missing=_need(module_count=module_count, module_wp=module_wp), inputs=inp)
    if module_count < 1 or module_wp < 100:
        return _bad("calculate_pv_capacity", "Module count must be >=1 and rating in watts.", **inp)
    return ToolResult(tool="calculate_pv_capacity", inputs=inp,
                      values={"installed_kwp": R(module_count * module_wp / 1000, 2)})


# ------------------------------------------------------------------- roof area
def calculate_roof_area(module_count: int, length_mm: float, width_mm: float,
                        layout_factor: float = 1.3) -> ToolResult:
    inp = dict(module_count=module_count, length_mm=length_mm, width_mm=width_mm,
               layout_factor=layout_factor)
    miss = _need(module_count=module_count, length_mm=length_mm, width_mm=width_mm)
    if miss:
        return ToolResult(tool="calculate_roof_area", status="incomplete", missing=miss, inputs=inp)
    if module_count < 1 or length_mm <= 0 or width_mm <= 0 or layout_factor < 1:
        return _bad("calculate_roof_area", "Counts/dimensions must be positive and layout_factor >= 1.", **inp)
    one = length_mm * width_mm / 1e6
    surface = module_count * one
    warns = []
    if layout_factor < 1.2:
        warns.append("Layout factor <1.2 leaves little room for walkways/clearances.")
    return ToolResult(
        tool="calculate_roof_area", inputs=inp, warnings=warns,
        values={"module_area_m2": R(one, 3), "total_module_surface_m2": R(surface, 1),
                "estimated_roof_area_m2": R(surface * layout_factor, 1)},
        assumptions=[f"Layout factor {layout_factor} (walkways, clearances, row spacing) — set by user/engineer; "
                     "tilted flat-roof arrays typically need a much higher factor than flush mounting."])


# ---------------------------------------------------------------------- DC/AC
def calculate_dc_ac_ratio(pv_kwp: float, inverter_ac_kw: float) -> ToolResult:
    inp = dict(pv_kwp=pv_kwp, inverter_ac_kw=inverter_ac_kw)
    miss = _need(pv_kwp=pv_kwp, inverter_ac_kw=inverter_ac_kw)
    if miss:
        return ToolResult(tool="calculate_dc_ac_ratio", status="incomplete", missing=miss, inputs=inp)
    if pv_kwp <= 0 or inverter_ac_kw <= 0:
        return _bad("calculate_dc_ac_ratio", "kWp and kW must be positive.", **inp)
    ratio = pv_kwp / inverter_ac_kw
    warns, status = [], "ok"
    if ratio < 1.0:
        warns.append("DC/AC < 1.0: inverter is larger than the array; check cost-effectiveness.")
        status = "warn"
    elif ratio > 1.5:
        warns.append("DC/AC > 1.5: significant clipping likely; confirm inverter's max PV input power.")
        status = "warn"
    elif ratio > 1.3:
        warns.append("DC/AC between 1.3 and 1.5: some clipping expected; engineer to confirm.")
        status = "warn"
    return ToolResult(tool="calculate_dc_ac_ratio", status=status, inputs=inp, warnings=warns,
                      values={"dc_ac_ratio": R(ratio, 3)},
                      assumptions=["Typical engineering window is roughly 1.0–1.3; interpretation is for the engineer."])


# ---------------------------------------------------------------- string check
def check_string_voltage(
    voc_v: Optional[float], vmp_v: Optional[float], temp_coeff_voc_pct_per_c: Optional[float],
    modules_per_string: Optional[int], min_temp_c: Optional[float], max_temp_c: Optional[float],
    inverter_max_dc_v: Optional[float], mppt_min_v: Optional[float], mppt_max_v: Optional[float],
    module_max_system_v: float = 1500, cell_temp_rise_c: float = 30.0,
) -> ToolResult:
    """Cold Voc <= max DC; hot Vmp >= MPPT min; cold Vmp <= MPPT max. All three needed for PASS."""
    inp = dict(voc_v=voc_v, vmp_v=vmp_v, temp_coeff_voc_pct_per_c=temp_coeff_voc_pct_per_c,
               modules_per_string=modules_per_string, min_temp_c=min_temp_c, max_temp_c=max_temp_c,
               inverter_max_dc_v=inverter_max_dc_v, mppt_min_v=mppt_min_v, mppt_max_v=mppt_max_v,
               module_max_system_v=module_max_system_v, cell_temp_rise_c=cell_temp_rise_c)
    miss = _need(**{k: inp[k] for k in ("voc_v", "vmp_v", "temp_coeff_voc_pct_per_c", "modules_per_string",
                                        "min_temp_c", "max_temp_c", "inverter_max_dc_v", "mppt_min_v", "mppt_max_v")})
    if miss:
        return ToolResult(tool="check_string_voltage", status="incomplete", missing=miss, inputs=inp,
                          warnings=["Check incomplete: no pass/fail is given without every module and inverter "
                                    "parameter plus a temperature assumption."])
    if modules_per_string < 1 or voc_v <= 0 or vmp_v <= 0 or temp_coeff_voc_pct_per_c >= 0:
        return _bad("check_string_voltage", "Invalid inputs: positive voltages, >=1 module, negative Voc coefficient.", **inp)
    if min_temp_c > max_temp_c:
        return _bad("check_string_voltage", "Minimum temperature is above maximum temperature.", **inp)
    n, k = modules_per_string, temp_coeff_voc_pct_per_c / 100.0
    voc_cold = voc_v * (1 + k * (min_temp_c - 25))
    vmp_cold = vmp_v * (1 + k * (min_temp_c - 25))
    t_cell_hot = max_temp_c + cell_temp_rise_c
    vmp_hot = vmp_v * (1 + k * (t_cell_hot - 25))
    string_voc_cold, string_vmp_hot, string_vmp_cold = n * voc_cold, n * vmp_hot, n * vmp_cold
    limit = min(inverter_max_dc_v, module_max_system_v)

    checks = {
        "cold_voc_le_max_dc": string_voc_cold <= limit,
        "hot_vmp_ge_mppt_min": string_vmp_hot >= mppt_min_v,
        "cold_vmp_le_mppt_max": string_vmp_cold <= mppt_max_v,
    }
    n_max = math.floor(min(limit / voc_cold, mppt_max_v / vmp_cold))
    n_min = math.ceil(mppt_min_v / vmp_hot)
    ok = all(checks.values())
    warns = []
    if not checks["cold_voc_le_max_dc"]:
        warns.append(f"Cold open-circuit string voltage {R(string_voc_cold,1)} V exceeds the limit {R(limit,1)} V — unsafe.")
    if not checks["hot_vmp_ge_mppt_min"]:
        warns.append(f"Hot string Vmp {R(string_vmp_hot,1)} V is below MPPT minimum {mppt_min_v} V — inverter may drop out.")
    if not checks["cold_vmp_le_mppt_max"]:
        warns.append(f"Cold string Vmp {R(string_vmp_cold,1)} V is above MPPT maximum {mppt_max_v} V.")
    if ok and string_voc_cold > 0.95 * limit:
        warns.append("Cold Voc is within 5% of the limit; confirm the minimum-temperature assumption.")
    return ToolResult(
        tool="check_string_voltage", status="ok" if ok else "fail", inputs=inp, warnings=warns,
        values={"string_voc_cold_v": R(string_voc_cold, 1), "string_vmp_hot_v": R(string_vmp_hot, 1),
                "string_vmp_cold_v": R(string_vmp_cold, 1), "voltage_limit_v": R(limit, 1),
                "allowed_modules_per_string": f"{n_min}–{n_max}" if n_min <= n_max else "none",
                "checks": checks},
        assumptions=[f"Min ambient {min_temp_c} °C, max ambient {max_temp_c} °C (user-stated).",
                     f"Hot cell temperature = ambient + {cell_temp_rise_c} °C (assumption).",
                     "Vmp temperature behaviour approximated with the Voc coefficient.",
                     "Engineer must confirm before design approval."])


def check_mppt_current(isc_a: Optional[float], imp_a: Optional[float],
                       parallel_strings: Optional[int], inverter_max_input_a: Optional[float],
                       inverter_max_isc_a: Optional[float], bifacial_gain: float = 1.0) -> ToolResult:
    inp = dict(isc_a=isc_a, imp_a=imp_a, parallel_strings=parallel_strings,
               inverter_max_input_a=inverter_max_input_a, inverter_max_isc_a=inverter_max_isc_a,
               bifacial_gain=bifacial_gain)
    miss = _need(isc_a=isc_a, imp_a=imp_a, parallel_strings=parallel_strings,
                 inverter_max_input_a=inverter_max_input_a, inverter_max_isc_a=inverter_max_isc_a)
    if miss:
        return ToolResult(tool="check_mppt_current", status="incomplete", missing=miss, inputs=inp,
                          warnings=["Check incomplete: inverter current limits and module currents are all required."])
    if parallel_strings < 1 or bifacial_gain < 1:
        return _bad("check_mppt_current", "Parallel strings >= 1 and bifacial gain >= 1.0 required.", **inp)
    isc_t = isc_a * parallel_strings * bifacial_gain
    imp_t = imp_a * parallel_strings * bifacial_gain
    ok_isc, ok_imp = isc_t <= inverter_max_isc_a, imp_t <= inverter_max_input_a
    warns = []
    if not ok_isc:
        warns.append(f"Total Isc per MPPT {R(isc_t)} A exceeds inverter limit {inverter_max_isc_a} A.")
    if not ok_imp:
        warns.append(f"Operating current per MPPT {R(imp_t)} A exceeds inverter limit {inverter_max_input_a} A (clipping/overload).")
    return ToolResult(
        tool="check_mppt_current", status="ok" if ok_isc and ok_imp else "fail", inputs=inp, warnings=warns,
        values={"isc_per_mppt_a": R(isc_t), "imp_per_mppt_a": R(imp_t),
                "max_isc_a": inverter_max_isc_a, "max_input_a": inverter_max_input_a},
        assumptions=[f"Bifacial rear-side current gain factor {bifacial_gain} (1.0 = ignored)."])


# ---------------------------------------------------------------- battery / money
def calculate_battery_energy(voltage_v: Optional[float], capacity_ah: Optional[float],
                             quantity: Optional[int], usable_dod_pct: Optional[float]) -> ToolResult:
    inp = dict(voltage_v=voltage_v, capacity_ah=capacity_ah, quantity=quantity, usable_dod_pct=usable_dod_pct)
    miss = _need(**inp)
    if miss:
        return ToolResult(tool="calculate_battery_energy", status="incomplete", missing=miss, inputs=inp)
    if voltage_v <= 0 or capacity_ah <= 0 or quantity < 1 or not (0 < usable_dod_pct <= 100):
        return _bad("calculate_battery_energy", "Voltage, Ah > 0, quantity >= 1, DoD in (0,100].", **inp)
    nominal = voltage_v * capacity_ah * quantity / 1000
    return ToolResult(tool="calculate_battery_energy", inputs=inp,
                      values={"nominal_kwh": R(nominal, 2), "usable_kwh": R(nominal * usable_dod_pct / 100, 2)},
                      assumptions=["Nominal V × Ah; usable = nominal × DoD. Take DoD from the exact battery datasheet."])


def calculate_payback(capex: Optional[float], annual_kwh: Optional[float],
                      tariff_per_kwh: Optional[float], om_pct_of_capex_per_year: float = 1.0) -> ToolResult:
    inp = dict(capex=capex, annual_kwh=annual_kwh, tariff_per_kwh=tariff_per_kwh,
               om_pct_of_capex_per_year=om_pct_of_capex_per_year)
    miss = _need(capex=capex, annual_kwh=annual_kwh, tariff_per_kwh=tariff_per_kwh)
    if miss:
        return ToolResult(tool="calculate_payback", status="incomplete", missing=miss, inputs=inp)
    if capex <= 0 or annual_kwh <= 0 or tariff_per_kwh <= 0 or om_pct_of_capex_per_year < 0:
        return _bad("calculate_payback", "Capex, energy and tariff must be positive.", **inp)
    net = annual_kwh * tariff_per_kwh - capex * om_pct_of_capex_per_year / 100
    if net <= 0:
        return ToolResult(tool="calculate_payback", status="fail", inputs=inp,
                          warnings=["Net annual savings are not positive; no payback."])
    return ToolResult(tool="calculate_payback", inputs=inp,
                      values={"annual_savings": R(net, 0), "simple_payback_years": R(capex / net, 1)},
                      assumptions=["Simple payback; no tariff escalation, degradation or financing. Currency is the user's.",
                                   f"O&M = {om_pct_of_capex_per_year}% of capex per year (editable)."])


# ------------------------------------------------------------------ generation
def estimate_generation_manual(pv_kwp: Optional[float], peak_sun_hours_per_day: Optional[float],
                               system_loss_pct: float = 14.0) -> ToolResult:
    """Offline estimate from user-supplied peak sun hours (used when PVGIS/NASA POWER are unreachable)."""
    inp = dict(pv_kwp=pv_kwp, peak_sun_hours_per_day=peak_sun_hours_per_day, system_loss_pct=system_loss_pct)
    miss = _need(pv_kwp=pv_kwp, peak_sun_hours_per_day=peak_sun_hours_per_day)
    if miss:
        return ToolResult(tool="estimate_solar_generation", status="incomplete", missing=miss, inputs=inp)
    if pv_kwp <= 0 or not (0 < peak_sun_hours_per_day <= 12) or not (0 <= system_loss_pct < 100):
        return _bad("estimate_solar_generation", "kWp>0, 0<PSH<=12 h/day and 0<=loss<100%.", **inp)
    daily = pv_kwp * peak_sun_hours_per_day * (1 - system_loss_pct / 100)
    return ToolResult(
        tool="estimate_solar_generation", inputs=inp,
        values={"daily_kwh": R(daily, 1), "monthly_kwh": R(daily * 30.4, 0), "annual_kwh": R(daily * 365, 0),
                "source": "user-supplied peak sun hours"},
        warnings=["Estimate only — not guaranteed output."],
        assumptions=[f"Peak sun hours {peak_sun_hours_per_day} h/day (user-supplied, on the array plane).",
                     f"System losses {system_loss_pct}%."])


def spec_to_string_inputs(module: ModuleSpec, inverter: InverterSpec, modules_per_string: int,
                          min_temp_c: float, max_temp_c: float) -> dict:
    """Helper: build check_string_voltage kwargs from catalogue entries (units preserved)."""
    return dict(voc_v=module.voc_v, vmp_v=module.vmp_v, temp_coeff_voc_pct_per_c=module.temp_coeff_voc_pct_per_c,
                modules_per_string=modules_per_string, min_temp_c=min_temp_c, max_temp_c=max_temp_c,
                inverter_max_dc_v=inverter.max_dc_voltage_v, mppt_min_v=inverter.mppt_min_v,
                mppt_max_v=inverter.mppt_max_v, module_max_system_v=module.max_system_voltage_v)
