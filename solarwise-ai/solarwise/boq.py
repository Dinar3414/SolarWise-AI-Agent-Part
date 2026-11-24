"""Preliminary BOQ from verified inputs only. Unknown quantities are TBD, never guessed."""
from __future__ import annotations
import math
from typing import Optional
from .schemas import ModuleSpec, InverterSpec

TBD = "TBD (engineer)"


def generate_boq(module: Optional[ModuleSpec], module_count: Optional[int], inverter: Optional[InverterSpec],
                 inverter_count: int = 1, modules_per_string: Optional[int] = None, dc_cable_m: Optional[float] = None,
                 unit_prices: Optional[dict] = None) -> dict:
    unit_prices = unit_prices or {}
    rows, warns = [], []

    def add(item, spec, qty, unit, source=""):
        price = unit_prices.get(item)
        rows.append({"item": item, "specification": spec, "qty": qty, "unit": unit,
                     "unit_price": price, "amount": (price * qty) if isinstance(qty, (int, float)) and price else None,
                     "source": source})

    if module and module_count:
        add("PV module", f"{module.manufacturer} {module.model}, {module.pmax_w:g} Wp", module_count, "pcs",
            f"{module.source_file} p.{module.source_page}")
        add("Mounting structure", "To suit roof type/wind load", module_count, "module sets", "")
    elif module_count:
        add("PV module", "Module not selected", module_count, "pcs")
    if inverter:
        add("Inverter", f"{inverter.manufacturer} {inverter.model}, {inverter.rated_ac_kw:g} kW AC", inverter_count, "pcs",
            f"{inverter.source_file} p.{inverter.source_page}")
    if module_count and modules_per_string:
        strings = module_count / modules_per_string
        if strings != int(strings):
            warns.append(f"{module_count} modules do not divide evenly into strings of {modules_per_string}; "
                         f"{math.ceil(strings)} strings would leave one short string — engineer to revise.")
        add("PV strings", f"{modules_per_string} modules per string", math.ceil(strings), "strings")
    add("DC string cable", "TUV EN 50618 solar cable (size by engineer)", dc_cable_m if dc_cable_m else TBD, "m" if dc_cable_m else "", "")
    add("DC protection / isolator", "Rated for string Voc and Isc (size by engineer)", TBD, "", "")
    add("DC and AC surge protection", "Type/ratings by engineer", TBD, "", "")
    add("AC protection and cable", "Rated for inverter output (size by engineer)", TBD, "", "")
    add("Earthing and lightning protection", "Per applicable code (engineer)", TBD, "", "")
    add("Meter / net-metering interface", "Per utility requirement", TBD, "", "")
    total = sum(r["amount"] for r in rows if r["amount"])
    return {"rows": rows, "warnings": warns, "total": total or None,
            "note": "Preliminary BOQ — quantities marked TBD require engineer sizing. Prices appear only if supplied by the user."}
