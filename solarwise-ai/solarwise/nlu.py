"""Deterministic slot extraction + intent routing. Works with no LLM (calculator-only mode).
When Gemini is available it can refine the intent, but slots are always re-validated here."""
from __future__ import annotations
import re
from . import catalog

NUM = r"(-?\d+(?:\.\d+)?)"
SLOT_PATTERNS = {
    "target_kw": rf"{NUM}\s*(?:kwp|kw)\b(?!\s*h)",
    "module_wp": rf"\b(\d{{3,4}})\s*(?:wp|watts?|w)\b(?!\s*h)",
    "inverter_ac_kw": rf"inverter[^.\d]{{0,25}}{NUM}\s*kw|{NUM}\s*kw\s*(?:ac\s*)?inverter",
    "modules_per_string": rf"{NUM}\s*(?:modules?|panels?)\s*(?:per|/|in a|in each)\s*string",
    "parallel_strings_per_mppt": rf"{NUM}\s*(?:parallel\s*)?strings?\s*(?:per|/)\s*mppt",
    "min_temp_c": rf"min(?:imum)?(?:\s*(?:ambient\s*)?temp(?:erature)?)?\s*(?:of|is|=|:)?\s*{NUM}\s*°?\s*c",
    "max_temp_c": rf"max(?:imum)?(?:\s*(?:ambient\s*)?temp(?:erature)?)?\s*(?:of|is|=|:)?\s*{NUM}\s*°?\s*c",
    "latitude": rf"lat(?:itude)?\s*[:=]?\s*{NUM}",
    "longitude": rf"lon(?:g|gitude)?\s*[:=]?\s*{NUM}",
    "battery_voltage_v": rf"{NUM}\s*v\b(?!\w)(?=[^.]*ah)",
    "battery_ah": rf"{NUM}\s*ah\b",
    "battery_qty": rf"\b(\d+)\s*(?:x|pcs|units|batter(?:y|ies))\b",
    "battery_dod_pct": rf"{NUM}\s*%\s*dod|dod\s*(?:of|=|:)?\s*{NUM}\s*%?",
    "peak_sun_hours": rf"{NUM}\s*(?:psh|peak sun hours?|sun hours)",
    "capex": rf"(?:capex|cost|budget)\D{{0,12}}{NUM}",
    "tariff_per_kwh": rf"tariff\D{{0,12}}{NUM}",
}
INT_SLOTS = {"modules_per_string", "parallel_strings_per_mppt", "battery_qty"}


def _norm(s: str) -> str:
    return re.sub(r"[\s_/]+", "", s.lower())


def extract_slots(text: str) -> dict:
    t, out = text.lower(), {}
    for k, pat in SLOT_PATTERNS.items():
        m = re.search(pat, t)
        if m:
            val = next((g for g in m.groups() if g is not None), None)
            if val is not None:
                out[k] = int(float(val)) if k in INT_SLOTS else float(val)
    if "target_kw" in out and "inverter" in t:  # "50kw" next to "inverter" is inverter size, not PV target
        m = re.search(r"inverter[^.\d]{0,25}\d", t)
        if m and "inverter_ac_kw" in out and out["inverter_ac_kw"] == out["target_kw"]:
            out.pop("target_kw")
    # explicit catalogue models by name
    nt = _norm(text)
    for m in catalog.modules():
        if _norm(m.model) in nt or _norm(m.model.replace("-LB", "/LB")) in nt:
            out["module_id"] = m.id
    for i in catalog.inverters():
        if _norm(i.model) in nt:
            out["inverter_id"] = i.id
    return out


INTENT_RULES = [
    ("report", r"\b(export|pdf|report)\b"),
    ("boq", r"\b(boq|bill of quantit|equipment list)\b"),
    ("offer", r"\b(offer|proposal|technical offer)\b|^\s*(?:design|create|prepare)\b.*\b(?:solar|pv|on[- ]?grid|system)\b"),
    ("payback", r"\b(payback|pay back|roi)\b"),
    ("battery", r"\b(batter(y|ies)|\d+\s*ah\b|dod)\b"),
    ("generation", r"\b(generation|generate|energy|kwh|yield|production|produce|pvgis|nasa)\b"),
    ("roof_area", r"\b(roof|area|space|m2|sqm|square)\b"),
    ("dc_ac_ratio", r"(dc\s*/?\s*ac|dc-ac|loading ratio|oversiz)"),
    ("mppt_check", r"(mppt current|input current|isc.*mppt|parallel strings)"),
    ("string_check", r"(string.*(check|ok|pass|compat|voltage|length|size)|compat|how many modules per string|modules per string)"),
    ("datasheet", r"\b(voc|isc|vmp|imp|pmax|datasheet|specification|specs?|coefficient|weight|dimension|fuse|mppt (voltage )?range|max(imum)?\.? (dc )?(input )?voltage|start-?up|efficien|connector|warranty)\b"),
    ("panel_count", r"(how many (panels|modules)|panel count|module count|number of (panels|modules)|panels? (needed|required)|modules? (needed|required)|\bneed\b.*\b(panels?|modules?)\b)"),
]


def detect_intent(text: str, slots: dict, last_intent: str | None = None) -> str:
    t = text.lower()
    for name, pat in INTENT_RULES:
        if re.search(pat, t):
            return name
    if "target_kw" in slots and "module_wp" in slots:
        return "panel_count"
    if slots and last_intent:   # follow-up such as "what about 620 W?"
        return last_intent
    return "other"


DATASHEET_PARAMS = {
    "voc": ("voc_v", "open-circuit voltage (Voc)", "V", "module"),
    "isc": ("isc_a", "short-circuit current (Isc)", "A", "module"),
    "vmp": ("vmp_v", "voltage at maximum power (Vmp)", "V", "module"),
    "imp": ("imp_a", "current at maximum power (Imp)", "A", "module"),
    "pmax": ("pmax_w", "maximum power (Pmax)", "W", "module"),
    "weight": ("weight_kg", "weight", "kg", "module"),
    "coefficient": ("temp_coeff_voc_pct_per_c", "temperature coefficient of Voc", "%/°C", "module"),
    "fuse": ("max_series_fuse_a", "maximum series fuse rating", "A", "module"),
    "max dc voltage": ("max_dc_voltage_v", "maximum DC input voltage", "V", "inverter"),
    "mppt": ("mppt_range", "MPPT voltage range", "V", "inverter"),
    "startup": ("startup_voltage_v", "start-up voltage", "V", "inverter"),
}


def datasheet_param(text: str) -> str | None:
    t = text.lower()
    if re.search(r"max(imum)?\.? (dc )?(input )?(pv )?voltage|max dc|maximum dc", t):
        return "max dc voltage"
    if re.search(r"mppt (voltage )?range|mppt voltage", t):
        return "mppt"
    if re.search(r"start-?up", t):
        return "startup"
    if re.search(r"coefficient", t):
        return "coefficient"
    for k in ("voc", "isc", "vmp", "imp", "pmax", "weight", "fuse"):
        if re.search(rf"\b{k}\b", t):
            return k
    if re.search(r"open[- ]circuit", t):
        return "voc"
    if re.search(r"short[- ]circuit", t):
        return "isc"
    return None
