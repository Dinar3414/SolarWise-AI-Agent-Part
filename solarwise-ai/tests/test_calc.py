import math, pytest
from solarwise import calc, catalog
from solarwise.schemas import ProjectInput
from pydantic import ValidationError


def test_proposal_example_50kw_615w():
    r = calc.calculate_panel_count(50, 615)
    assert r.values["module_count"] == 82 and r.values["installed_kwp"] == 50.43


def test_exact_multiple_does_not_round_up():
    assert calc.calculate_panel_count(61.5, 615).values["module_count"] == 100


def test_boundaries_and_deterministic():
    assert calc.calculate_panel_count(0.1, 1000).values["module_count"] == 1
    assert calc.calculate_panel_count(50, 615).values == calc.calculate_panel_count(50, 615).values


def test_unit_confusion_rejected():
    assert calc.calculate_panel_count(50, 0.615).status == "fail"
    with pytest.raises(ValidationError):
        ProjectInput(module_wp=0.6)


def test_missing_inputs_incomplete_not_guess():
    assert calc.calculate_panel_count(50, None).status == "incomplete"
    r = calc.check_string_voltage(48.3, 39.9, -0.25, None, None, 40, 1100, 180, 1000)
    assert r.status == "incomplete" and "modules_per_string" in r.missing and "min_temp_c" in r.missing


def test_string_voltage_pass_and_fail():
    ok = calc.check_string_voltage(48.30, 39.96, -0.25, 20, 5, 40, 1100, 180, 1000)
    assert ok.status == "ok" and ok.values["string_voc_cold_v"] == 1014.3
    bad = calc.check_string_voltage(48.30, 39.96, -0.25, 24, 5, 40, 1100, 180, 1000)  # cold Voc ~1217 V
    assert bad.status == "fail" and any("unsafe" in w for w in bad.warnings)
    low = calc.check_string_voltage(48.30, 39.96, -0.25, 4, 5, 40, 1100, 180, 1000)
    assert low.status == "fail"


def test_string_voltage_hand_calculation():
    # Voc_cold = 48.30*(1-0.0025*(5-25)) = 48.30*1.05 = 50.715 ; x20 = 1014.3
    r = calc.check_string_voltage(48.30, 39.96, -0.25, 20, 5, 40, 1100, 180, 1000)
    assert math.isclose(r.values["string_voc_cold_v"], 1014.3, abs_tol=0.1)


def test_module_system_voltage_limits_string():
    r = calc.check_string_voltage(48.3, 40, -0.25, 25, 5, 40, 1100, 180, 1000, module_max_system_v=1000)
    assert r.values["voltage_limit_v"] == 1000


def test_mppt_current():
    assert calc.check_mppt_current(16.1, 15.4, 2, 32, 40).status == "ok"
    assert calc.check_mppt_current(16.1, 15.4, 3, 32, 40).status == "fail"   # 48.3 A Isc > 40 A
    assert calc.check_mppt_current(16.1, 15.4, 2, None, 40).status == "incomplete"


def test_dc_ac_ratio_warnings():
    assert calc.calculate_dc_ac_ratio(50.43, 50).values["dc_ac_ratio"] == 1.009
    assert calc.calculate_dc_ac_ratio(80, 50).status == "warn"
    assert calc.calculate_dc_ac_ratio(40, 50).status == "warn"


def test_battery_payback_generation():
    assert calc.calculate_battery_energy(51.2, 100, 2, 90).values == {"nominal_kwh": 10.24, "usable_kwh": 9.22}
    assert calc.calculate_battery_energy(51.2, 100, 2, 120).status == "fail"
    assert calc.calculate_payback(3_000_000, 60_000, 10, 1).values["simple_payback_years"] == 5.3
    assert calc.calculate_payback(1_000_000, 10, None).status == "incomplete"
    assert calc.calculate_payback(1_000_000, 10, 1).status == "fail"
    g = calc.estimate_generation_manual(50.43, 4.5, 14)
    assert g.values["annual_kwh"] == round(50.43 * 4.5 * 0.86 * 365, 0)


def test_roof_area():
    r = calc.calculate_roof_area(82, 2382, 1134, 1.3)
    assert r.values["module_area_m2"] == 2.701 and r.values["estimated_roof_area_m2"] == 287.9


def test_catalogue_consistency():
    for m in catalog.modules():     # Vmp x Imp must be close to Pmax (guards OCR/transcription slips)
        assert abs(m.vmp_v * m.imp_a - m.pmax_w) / m.pmax_w < 0.01, m.id
        assert m.voc_v > m.vmp_v and m.isc_a > m.imp_a
    for i in catalog.inverters():
        assert i.mppt_min_v < i.mppt_max_v < i.max_dc_voltage_v
