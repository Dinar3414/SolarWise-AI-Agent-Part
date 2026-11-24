"""LangGraph orchestration (C7): classify → validate → (refuse | clarify | calculate | retrieve) →
estimate → compose → safety_check → review. Routing is conditional; every node is timed and traced."""
from __future__ import annotations
import json
import math
import re
import time
from functools import wraps
from typing import Any, Optional, TypedDict
from langgraph.graph import StateGraph, END
from pydantic import ValidationError
from . import calc, catalog, nlu, safety, solar_data
from .boq import generate_boq
from .llm import GeminiClient, numbers_grounded
from .memory import Memory
from .retriever import Retriever, retrieve_datasheet
from .schemas import ProjectInput, ToolResult, Citation
from . import config

PROJECT_FIELDS = set(ProjectInput.model_fields)
EXTRA_SLOTS = {"battery_voltage_v", "battery_ah", "battery_qty", "battery_dod_pct", "peak_sun_hours", "capex", "tariff_per_kwh"}
LABEL = "_Preliminary result — not an approval. A qualified engineer must verify before design approval._"


def offer_reference(target_kw: float | None) -> dict:
    """Load the closest supplied offer and transcribe it as the reference configuration."""
    if not config.CHUNKS_PATH.exists():
        return {}
    offers = []
    for line in config.CHUNKS_PATH.read_text(encoding="utf-8").splitlines():
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue
        if chunk.get("type") != "offer":
            continue
        text = chunk["text"]
        capacity_match = re.search(
            r"project capacity\s*:?\s*([0-9]+(?:\.[0-9]+)?)[\s]*(?:kw|k watt|watt)",
            text,
            re.I,
        )
        if not capacity_match:
            filename_match = re.search(
                r"(?:^|[^0-9])([0-9]+(?:\.[0-9]+)?)[kK][wW]",
                chunk["source_file"],
                re.I,
            )
            if filename_match:
                capacity_match = filename_match
        if not capacity_match:
            continue
        raw = capacity_match.group(1)
        try:
            capacity = float(raw)
            if "watt" in text[max(0, capacity_match.start()-10):capacity_match.end()].lower() and capacity > 1000:
                capacity /= 1000
        except ValueError:
            continue
        module_match = re.search(r"panel capacity\s*:?\s*([0-9]+)\s*watt", text, re.I)
        inverter_match = re.search(r"inverter capacity\s*:?\s*([0-9]+(?:\.[0-9]+)?)\s*kw", text, re.I)
        offers.append({
            "capacity_kw": capacity,
            "module_wp": int(module_match.group(1)) if module_match else 615,
            "inverter_kw": float(inverter_match.group(1)) if inverter_match else 20,
            "source_file": chunk["source_file"],
        })
    unique = {}
    for offer in offers:
        unique[offer["source_file"]] = offer
    offers = list(unique.values())
    if not offers:
        return {}
    if target_kw is not None:
        return min(offers, key=lambda o: abs(o["capacity_kw"] - target_kw))
    return min(offers, key=lambda o: abs(o["capacity_kw"] - 20.0))


def offer_equipment_items(source_file: str) -> list[str]:
    """Return non-commercial technical BOQ rows from one supplied Word offer."""
    if not config.CHUNKS_PATH.exists():
        return []
    items, seen = [], set()
    for line in config.CHUNKS_PATH.read_text(encoding="utf-8").splitlines():
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue
        if chunk.get("type") != "offer" or chunk.get("source_file") != source_file:
            continue
        for row in chunk["text"].splitlines():
            fields = [part.strip() for part in row.split("|")]
            if len(fields) < 3 or not re.match(r"^\d{1,2}\.?\s*$", fields[0]):
                continue
            if re.search(r"\b(?:commercial offer|unit price|total price|amount|in word)\b", row, re.I):
                continue
            item = " — ".join(field for field in fields[1:] if field)
            if item not in seen:
                seen.add(item)
                items.append(item)
    return items


def design_equipment(target_kw: float, module_wp: float | None) -> dict:
    """Find exact module ratings and exact or nearest-higher inverter specs in the catalogue."""
    modules = catalog.modules()
    inverters = catalog.inverters()
    module_matches = [m for m in modules if module_wp is not None and math.isclose(m.pmax_w, module_wp, abs_tol=0.01)]

    capacities = sorted({i.rated_ac_kw for i in inverters})
    exact_capacity = next((kw for kw in capacities if math.isclose(kw, target_kw, abs_tol=0.01)), None)
    if exact_capacity is not None:
        selected_capacities = {exact_capacity}
        lower = [kw for kw in capacities if kw < target_kw]
        higher = [kw for kw in capacities if kw > target_kw]
        if lower:
            selected_capacities.add(max(lower))
        if higher:
            selected_capacities.add(min(higher))
        inverter_status = "exact"
        suggested_capacity = exact_capacity
    else:
        higher = [kw for kw in capacities if kw > target_kw]
        if higher:
            suggested_capacity = min(higher)
            selected_capacities = {suggested_capacity}
            inverter_status = "nearest_higher"
        else:
            suggested_capacity = max(capacities) if capacities else None
            selected_capacities = {suggested_capacity} if suggested_capacity is not None else set()
            inverter_status = "no_higher"

    inverter_options = [
        i for i in inverters
        if i.rated_ac_kw in selected_capacities
    ]
    return {
        "module_options": module_matches,
        "module_nearest": sorted(modules, key=lambda m: abs(m.pmax_w - module_wp))[:2] if module_wp is not None and not module_matches else [],
        "inverter_status": inverter_status,
        "inverter_options": inverter_options,
        "suggested_inverter_kw": suggested_capacity,
    }


class State(TypedDict, total=False):
    session_id: str
    user_text: str
    intent: str
    slots: dict
    project: dict
    errors: list
    missing: list
    results: list
    citations: list
    evidence_flags: list
    route: str
    answer: str
    answer_source: str
    refusal: str
    clarify: str
    status: str


def build_graph(memory: Memory, retriever: Optional[Retriever], llm: Optional[GeminiClient] = None):
    llm = llm or GeminiClient(memory)

    def timed(name):
        def deco(fn):
            @wraps(fn)
            def wrapped(state: State) -> State:
                t0 = time.perf_counter()
                out = fn(state)
                memory.trace(name, (time.perf_counter() - t0) * 1000, out.get("route", "") if isinstance(out, dict) else "")
                return out
            return wrapped
        return deco

    # ------------------------------------------------------------------ nodes
    @timed("classify")
    def classify(s: State) -> State:
        text = s["user_text"]
        slots = nlu.extract_slots(text)
        proj = memory.get_project()
        intent = nlu.detect_intent(text, slots, proj.get("_last_intent"))
        if llm.available and intent == "other":  # LLM only refines when rules can't decide
            guess = llm.classify(text) or {}
            if guess.get("intent") in {"panel_count", "roof_area", "dc_ac_ratio", "string_check", "mppt_check", "battery",
                                       "generation", "payback", "datasheet", "offer", "boq"}:
                intent = guess["intent"]
        return {"intent": intent, "slots": slots}

    @timed("validate")
    def validate(s: State) -> State:
        risk = safety.classify_risk(s["user_text"])
        if risk["refuse"]:
            return {"route": "refuse", "refusal": risk["reason"]}
        proj = memory.get_project()
        merged = {**proj, **s["slots"]}          # follow-ups reuse confirmed information (C4)
        errors, clean = [], {}
        for k, v in merged.items():
            if k.startswith("_") or k in EXTRA_SLOTS or k in ("module_id", "inverter_id"):
                clean[k] = v
        try:
            ProjectInput(**{k: v for k, v in merged.items() if k in PROJECT_FIELDS})
        except ValidationError as e:
            errors = [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()]
            bad = {x["loc"][0] for x in e.errors()}
            merged = {k: v for k, v in merged.items() if k not in bad}
        saved = memory.update_project(**{**merged, "_last_intent": s["intent"] if s["intent"] != "other" else None})
        if errors:
            return {"route": "clarify", "errors": errors, "project": saved,
                    "clarify": "Some values look invalid, so I did not use them:\n- " + "\n- ".join(errors)}
        if s["intent"] == "other":
            return {"route": "clarify", "project": saved,
                    "clarify": "I can calculate panel count, roof area, DC/AC ratio, string voltage and MPPT current checks, "
                               "battery energy, solar generation and payback, answer datasheet questions with page citations, and "
                               "prepare a BOQ/PDF. Try: *\"I need a 50 kW system using 615 W modules — how many panels?\"*"}
        route = "retrieve" if s["intent"] in ("datasheet", "offer") else "calculate"
        return {"route": route, "project": saved}

    def _equipment(p: dict):
        return catalog.get_module(p.get("module_id")), catalog.get_inverter(p.get("inverter_id"))

    @timed("calculate")
    def calculate(s: State) -> State:
        p, it, res = s["project"], s["intent"], []
        mod, inv = _equipment(p)
        wp = p.get("module_wp") or (mod.pmax_w if mod else None)
        n = None
        if it in ("panel_count", "roof_area", "dc_ac_ratio", "boq", "report", "generation", "payback"):
            pc = calc.calculate_panel_count(p.get("target_kw"), wp)
            if it == "panel_count" or pc.status == "ok":
                res.append(pc)
            n = pc.values.get("module_count")
        kwp = (n * wp / 1000) if n and wp else p.get("target_kw")
        if it == "roof_area":
            res.append(calc.calculate_roof_area(n, mod.length_mm if mod else None, mod.width_mm if mod else None))
        elif it == "dc_ac_ratio":
            ac = p.get("inverter_ac_kw") or (inv.rated_ac_kw if inv else None)
            res.append(calc.calculate_dc_ac_ratio(kwp, ac))
        elif it == "string_check":
            res.append(_string_check(p, mod, inv))
        elif it == "mppt_check":
            res.append(calc.check_mppt_current(mod.isc_a if mod else None, mod.imp_a if mod else None,
                                               p.get("parallel_strings_per_mppt"), inv.max_input_current_per_mppt_a if inv else None,
                                               inv.max_isc_per_mppt_a if inv else None))
        elif it == "battery":
            res.append(calc.calculate_battery_energy(p.get("battery_voltage_v"), p.get("battery_ah"), p.get("battery_qty"), p.get("battery_dod_pct")))
        elif it in ("generation", "payback"):
            g = solar_data.estimate_generation(p.get("latitude"), p.get("longitude"), kwp, p.get("tilt_deg"), p.get("azimuth_deg"),
                                               p.get("system_loss_pct", 14.0), p.get("peak_sun_hours")) if kwp else \
                ToolResult(tool="estimate_solar_generation", status="incomplete", missing=["target kW and module watts"])
            res.append(g)
            if it == "payback":
                res.append(calc.calculate_payback(p.get("capex"), g.values.get("annual_kwh"), p.get("tariff_per_kwh")))
        elif it in ("boq", "report"):
            boq = generate_boq(mod, n, inv, 1, p.get("modules_per_string"))
            res.append(ToolResult(tool="generate_boq", values={"items": len(boq["rows"]), "tbd_items": sum(1 for r in boq["rows"] if r["qty"] == "TBD (engineer)")},
                                  warnings=boq["warnings"], assumptions=[boq["note"]]))
        return {"results": res, "route": "estimate"}

    def _string_check(p, mod, inv) -> ToolResult:
        miss = []
        if not mod: miss.append("module (choose a catalogue model or enter Voc/Vmp/temp. coefficient)")
        if not inv: miss.append("inverter (choose a catalogue model or enter max DC voltage / MPPT range)")
        for k, label in (("modules_per_string", "modules per string"), ("min_temp_c", "minimum site temperature (°C)"), ("max_temp_c", "maximum site temperature (°C)")):
            if p.get(k) is None: miss.append(label)
        if miss:
            return ToolResult(tool="check_string_voltage", status="incomplete", missing=miss,
                              warnings=["Check incomplete: no pass/fail without every module and inverter parameter plus a temperature assumption."])
        return calc.check_string_voltage(**calc.spec_to_string_inputs(mod, inv, p["modules_per_string"], p["min_temp_c"], p["max_temp_c"]))

    @timed("retrieve")
    def retrieve(s: State) -> State:
        p, q = s["project"], s["user_text"]
        out = retrieve_datasheet(retriever, q) if retriever else {"citations": [], "weak": True, "model_filter": [], "exact_model_named": False}
        if s["intent"] == "offer":
            offers = [c for c in out["citations"] if c.source_file.lower().endswith(".docx")]
            target_kw = p.get("target_kw")
            reference = {}
            res = []
            if target_kw is not None:
                reference = offer_reference(target_kw)
                if not reference:
                    return {"results": [ToolResult(tool="technical_offer", status="incomplete",
                                                   missing=["supplied offer/capacity reference"],
                                                   warnings=["No Word technical offer is available in the corpus."])],
                            "citations": offers, "evidence_flags": [], "route": "estimate"}
                module_wp = p.get("module_wp") or reference["module_wp"]
                equipment = design_equipment(target_kw, module_wp)
                module_matches = equipment["module_options"]
                module_count = math.ceil(target_kw * 1000 / module_wp)
                module_specs = [
                    {
                        "manufacturer": m.manufacturer, "model": m.model, "rating_w": m.pmax_w,
                        "voc_v": m.voc_v, "vmp_v": m.vmp_v, "isc_a": m.isc_a, "imp_a": m.imp_a,
                        "source": f"{m.source_file} p.{m.source_page}",
                    }
                    for m in (module_matches or equipment["module_nearest"])
                ]
                inverter_specs = [
                    {
                        "manufacturer": i.manufacturer, "model": i.model, "rated_ac_kw": i.rated_ac_kw,
                        "max_dc_voltage_v": i.max_dc_voltage_v,
                        "mppt_range_v": f"{i.mppt_min_v:g}-{i.mppt_max_v:g}",
                        "mppt_count": i.mppt_count,
                        "max_input_current_per_mppt_a": i.max_input_current_per_mppt_a,
                        "max_isc_per_mppt_a": i.max_isc_per_mppt_a,
                        "source": f"{i.source_file} p.{i.source_page}",
                    }
                    for i in equipment["inverter_options"]
                ]
                values = {
                    "capacity_kw": target_kw,
                    "module_capacity_w": module_wp,
                    "module_quantity": module_count,
                    "installed_kwp": round(module_count * module_wp / 1000, 2),
                    "module_options": module_specs,
                    "module_exact_match": bool(module_matches),
                    "inverter_options": inverter_specs,
                    "inverter_status": equipment["inverter_status"],
                    "suggested_inverter_kw": equipment["suggested_inverter_kw"],
                    "source": reference["source_file"],
                    "scaled_from": f"{reference['capacity_kw']:g} kW supplied offer",
                    "reference_items": offer_equipment_items(reference["source_file"]),
                }
                assumptions = [
                    f"Closest supplied on-grid project offer: {reference['source_file']} ({reference['capacity_kw']:g} kW).",
                    "Panel quantity is the ceiling of target DC capacity divided by module rating; final string layout may change it.",
                    "Inverter models and electrical limits are transcribed from the supplied manufacturer datasheets.",
                    "Commercial prices and terms are omitted. Final compatibility, string design, protection and cabling require a qualified engineer.",
                ]
                res.append(ToolResult(tool="technical_offer", status="ok", values=values,
                                      warnings=["Preliminary design only; the listed inverter rating is not a complete compatibility check."],
                                      assumptions=assumptions))
            else:
                res.append(ToolResult(tool="technical_offer", status="incomplete",
                                      missing=["target system capacity in kW"],
                                      warnings=["State the requested PV capacity in kW."]))
            equipment_citations = []
            if target_kw is not None:
                equipment = design_equipment(target_kw, p.get("module_wp") or reference.get("module_wp"))
                for module in equipment["module_options"]:
                    equipment_citations.append(Citation(
                        manufacturer=module.manufacturer, model=module.model, revision=module.revision,
                        source_file=module.source_file, page=module.source_page,
                    ))
                for inverter in equipment["inverter_options"]:
                    equipment_citations.append(Citation(
                        manufacturer=inverter.manufacturer, model=inverter.model, revision=inverter.revision,
                        source_file=inverter.source_file, page=inverter.source_page,
                    ))
            return {"results": res, "citations": equipment_citations or offers or out["citations"],
                    "evidence_flags": [], "route": "estimate"}
        param = nlu.datasheet_param(q)
        cur = s["slots"]
        if cur.get("module_id") or cur.get("inverter_id"):
            mod, inv = catalog.get_module(cur.get("module_id")), catalog.get_inverter(cur.get("inverter_id"))
        elif out["exact_model_named"]:          # a different family was named: ignore remembered equipment
            mod = inv = None
        else:
            mod, inv = _equipment(p)
        res, evidence_flags, cites = [], [], out["citations"]
        for c in cites:
            c.text, flagged = safety.sanitize_evidence(c.text)
            if flagged: evidence_flags.append(f"{c.source_file} p.{c.page}")
        info = nlu.DATASHEET_PARAMS.get(param) if param else None
        if not param:
            return {"results": [], "citations": cites, "evidence_flags": evidence_flags, "route": "estimate",
                    "clarify": "Which value do you need (Voc, Isc, Vmp, Imp, Pmax, temperature coefficient, weight, fuse rating, max DC voltage, MPPT range)?"}
        if out["weak"] or not out["exact_model_named"] and not (mod or inv):
            return {"results": [], "citations": cites if out["exact_model_named"] else [], "evidence_flags": evidence_flags, "route": "estimate",
                    "refusal": safety.REFUSAL if out["weak"] or not cites else "",
                    "clarify": "" if out["weak"] else "Please name the exact module or inverter model so I can cite the right datasheet."}
        spec = mod if info and info[3] == "module" else inv
        if spec is None and info[3] == "module" and out["model_filter"]:
            fam = [m for m in catalog.modules() if m.source_file in out["model_filter"]]
            if fam:
                opts = ", ".join(m.model for m in fam)
                return {"results": [], "citations": cites, "evidence_flags": evidence_flags, "route": "estimate",
                        "clarify": f"{info[1].capitalize()} depends on the exact power class. Which one: {opts}?"}
        if spec is None and out["exact_model_named"] and cites:
            kw = {"weight": "weight", "coefficient": "coefficient", "fuse": "fuse", "max dc voltage": r"max\.? (dc|pv|input)[a-z ]*voltage",
                  "mppt": r"mppt voltage range", "startup": "start", "voc": "open circuit|voc", "isc": "short|isc", "vmp": "maximum power|vmp", "imp": "maximum power|imp", "pmax": "maximum power|pmax"}.get(param, param)
            lines = []
            for c in cites:
                L = c.text.splitlines()
                for i, ln in enumerate(L):
                    if re.search(kw, ln, re.I):
                        lines.append(" ".join(x.strip() for x in L[i:i + 3]))
            lines = lines[:3]
            res.append(ToolResult(tool="retrieve_excerpt", values={"model": f"{cites[0].manufacturer} {cites[0].model}",
                                  "source": f"{cites[0].source_file} p.{cites[0].page}", "excerpt": " | ".join(lines) or cites[0].text[:300]},
                                  warnings=["This model is not in the verified catalogue; the excerpt is a raw extract and must be checked on the cited page."]))
            return {"results": res, "citations": cites, "evidence_flags": evidence_flags, "route": "estimate"}
        if spec is not None:
            key = info[0]
            val = f"{spec.mppt_min_v:g}–{spec.mppt_max_v:g}" if key == "mppt_range" else getattr(spec, key, None)
            if val is not None:
                res.append(ToolResult(tool="retrieve_datasheet", values={"parameter": info[1], "value": val, "unit": info[2],
                                      "model": f"{spec.manufacturer} {spec.model}", "revision": spec.revision or "n/a",
                                      "source": f"{spec.source_file} p.{spec.source_page}"},
                                      assumptions=["Value transcribed from the cited datasheet page; engineer must verify the exact revision."]))
                cites = [Citation(manufacturer=spec.manufacturer, model=spec.model, revision=spec.revision, source_file=spec.source_file,
                                  page=spec.source_page, score=1.0, text="")] + [c for c in cites if (c.source_file, c.page) != (spec.source_file, spec.source_page)]
            else:
                res.append(ToolResult(
                    tool="retrieve_datasheet",
                    status="incomplete",
                    missing=[f"{info[1]} in the supplied catalogue data"],
                    warnings=["The selected model was found, but this revision does not contain the requested parameter."],
                ))
        return {"results": res, "citations": cites, "evidence_flags": evidence_flags, "route": "estimate"}

    @timed("estimate")
    def estimate(s: State) -> State:   # generation already handled in calculate; node kept for explicit routing/trace
        return {"route": "compose"}

    @timed("compose")
    def compose(s: State) -> State:
        if s.get("refusal"):
            return {"answer": s["refusal"], "answer_source": "policy"}
        if s.get("clarify") and not s.get("results"):
            return {"answer": s["clarify"], "answer_source": "template"}
        tmpl = _template(s)
        tool_text = "\n".join(json.dumps(r.model_dump(exclude={"inputs"}), ensure_ascii=False, default=str) for r in s.get("results", []))
        evidence = "\n".join(safety.wrap_untrusted(f"{c.manufacturer} {c.model} p.{c.page}: {c.text[:500]}") for c in s.get("citations", [])[:3] if c.text)
        if llm.available and tool_text:
            text = llm.compose(s["user_text"], tool_text, evidence)
            if text:
                ok, bad = numbers_grounded(text, tool_text + " " + evidence + " " + s["user_text"])
                if ok:
                    return {"answer": text, "answer_source": "gemini"}
                memory.trace("safety:ungrounded_numbers", 0, ",".join(bad[:8]))
        return {"answer": tmpl, "answer_source": "template"}

    def _template(s: State) -> str:
        parts = []
        for r in s.get("results", []):
            parts.append(_fmt(r, s))
        txt = "\n\n".join(p for p in parts if p) or s.get("clarify", "")
        return txt

    def _fmt(r: ToolResult, s: State) -> str:
        v, name = r.values, r.tool
        if r.status == "incomplete":
            return f"**{name.replace('_', ' ').title()} — incomplete.** I won't guess. Missing: " + "; ".join(r.missing) + "." + ("\n\n" + " ".join(r.warnings) if r.warnings else "")
        warn = ("\n\n⚠ " + " ".join(r.warnings)) if r.warnings else ""
        if r.status == "fail":
            return f"**{name.replace('_', ' ').title()} — failed.**" + warn
        if name == "calculate_panel_count":
            p = s["project"]; wp = p.get("module_wp") or (catalog.get_module(p.get("module_id")).pmax_w if p.get("module_id") else 0)
            return (f"You need at least **{v['module_count']} modules** ({v['module_count']} × {wp:g} W = **{v['installed_kwp']} kWp**), treating "
                    f"{p['target_kw']:g} kW as the minimum DC target. This is PV capacity only — inverter selection and string configuration "
                    f"need the actual module and inverter specifications." + warn)
        if name == "calculate_roof_area":
            return f"{v['total_module_surface_m2']} m² of module surface; about **{v['estimated_roof_area_m2']} m²** of roof with the stated layout factor." + warn + "\n\n_" + r.assumptions[0] + "_"
        if name == "calculate_dc_ac_ratio":
            return f"DC/AC ratio = **{v['dc_ac_ratio']}**." + warn
        if name == "check_string_voltage":
            head = "✅ String check **passes** for the stated assumptions." if r.status == "ok" else "❌ String check **fails**."
            return (f"{head}\n\n- Cold string Voc: {v['string_voc_cold_v']} V (limit {v['voltage_limit_v']} V)\n- Hot string Vmp: {v['string_vmp_hot_v']} V\n"
                    f"- Cold string Vmp: {v['string_vmp_cold_v']} V\n- Allowed modules per string for these limits: {v['allowed_modules_per_string']}" + warn +
                    "\n\n_Assumptions: " + " ".join(r.assumptions) + "_")
        if name == "check_mppt_current":
            head = "✅ MPPT current within limits." if r.status == "ok" else "❌ MPPT current check **fails**."
            return f"{head} Isc per MPPT {v['isc_per_mppt_a']} A (max {v['max_isc_a']} A); operating current {v['imp_per_mppt_a']} A (max {v['max_input_a']} A)." + warn
        if name == "calculate_battery_energy":
            return f"Nominal energy **{v['nominal_kwh']} kWh**, usable **{v['usable_kwh']} kWh**." + warn
        if name == "estimate_solar_generation":
            return (f"Estimated generation ≈ **{v['annual_kwh']:,.0f} kWh/year** (~{v['daily_kwh']} kWh/day). Source: {v['source']}." + warn +
                    "\n\n_" + " ".join(r.assumptions) + "_")
        if name == "calculate_payback":
            return f"Simple payback ≈ **{v['simple_payback_years']} years** (net annual savings {v['annual_savings']:,.0f})." + warn
        if name == "retrieve_datasheet":
            val = int(v["value"]) if isinstance(v["value"], float) and float(v["value"]).is_integer() else v["value"]
            lab = v["parameter"][0].upper() + v["parameter"][1:]
            return (f"**{lab}** of {v['model']}: **{val} {v['unit']}**\n\n"
                    f"📄 Source: {v['source']} (revision {v['revision']}). Please verify against the exact datasheet before relying on it.")
        if name == "retrieve_excerpt":
            return f"Closest datasheet excerpt for {v['model']} (📄 {v['source']}):\n\n> {v['excerpt']}" + warn
        if name == "technical_offer":
            lines = [f"**Preliminary on-grid PV design: {v['capacity_kw']:g} kW DC**"]
            if v.get("module_exact_match"):
                lines.append(
                    f"- PV modules: **{v['module_quantity']} × {v['module_capacity_w']:g} W** "
                    f"(about **{v['installed_kwp']:g} kWp DC** installed)"
                )
                lines.append("- Matching datasheet module options:")
            else:
                lines.append(
                    f"- No exact {v['module_capacity_w']:g} W module rating was found in the supplied datasheets. "
                    f"The {v['module_quantity']} panel arithmetic is based only on the requested rating; choose a listed module:"
                )
            for module in v.get("module_options", []):
                lines.append(
                    f"  - {module['manufacturer']} **{module['model']}** ({module['rating_w']:g} W; "
                    f"Voc {module['voc_v']:g} V, Vmp {module['vmp_v']:g} V, "
                    f"Isc {module['isc_a']:g} A, Imp {module['imp_a']:g} A; source: {module['source']})"
                )
            if v["inverter_status"] == "exact":
                lines.append(f"- Exact {v['capacity_kw']:g} kW inverter rating is available in the supplied datasheets:")
            elif v["inverter_status"] == "nearest_higher":
                lines.append(
                    f"- No exact {v['capacity_kw']:g} kW inverter specification is in the supplied datasheets. "
                    f"The nearest larger listed rating is {v['suggested_inverter_kw']:g} kW. Would you like to use this option?"
                )
            else:
                lines.append(
                    f"- No inverter at or above {v['capacity_kw']:g} kW is listed. "
                    f"The largest rated model in the supplied inverter datasheets is {v['suggested_inverter_kw']:g} kW."
                )
            for inverter in v.get("inverter_options", []):
                lines.append(
                    f"  - {inverter['manufacturer']} **{inverter['model']}** ({inverter['rated_ac_kw']:g} kW AC; "
                    f"max DC {inverter['max_dc_voltage_v']:g} V; MPPT {inverter['mppt_range_v']} V; "
                    f"{inverter['mppt_count']} MPPTs; source: {inverter['source']})"
                )
            if v.get("reference_items"):
                lines.append(
                    f"- Other equipment in the referenced offer ({v['source']}); these are source-offer quantities/specifications, not scaled for this project:"
                )
                lines.extend(f"  - {item}" for item in v["reference_items"])
            lines.extend([
                f"- Closest supplied project-offer reference: **{v['source']}** ({v['scaled_from']}).",
                "- Commercial prices are omitted. String count, voltage/current compatibility, protections and cabling still need engineering verification.",
            ])
            return "\n".join(lines) + warn
        if name == "generate_boq":
            return f"Preliminary BOQ prepared: {v['items']} line items, {v['tbd_items']} need engineer sizing. Open the **BOQ & Report** tab to export." + warn
        return r.summary()

    @timed("safety_check")
    def safety_check(s: State) -> State:
        ans = s.get("answer", "")
        if safety.contains_injection(ans):
            ans = safety.REFUSAL
        return {"answer": ans}

    @timed("review")
    def review(s: State) -> State:
        ans = s.get("answer", "")
        stat = memory.get_review()["status"]
        if s.get("results") and not s.get("refusal") and LABEL not in ans:
            ans += "\n\n" + LABEL
        flags = s.get("evidence_flags") or []
        if flags:
            ans += "\n\n🛡️ Instruction-like text was found and ignored in: " + ", ".join(flags)
        memory.add_message("assistant", ans)
        return {"answer": ans, "status": stat}

    # ----------------------------------------------------------------- wiring
    g = StateGraph(State)
    for name, fn in [("classify", classify), ("validate", validate), ("calculate", calculate), ("retrieve", retrieve),
                     ("estimate", estimate), ("compose", compose), ("safety_check", safety_check), ("review", review)]:
        g.add_node(name, fn)
    g.set_entry_point("classify")
    g.add_edge("classify", "validate")
    g.add_conditional_edges("validate", lambda s: s["route"], {"refuse": "compose", "clarify": "compose", "calculate": "calculate", "retrieve": "retrieve"})
    g.add_edge("calculate", "estimate")
    g.add_edge("retrieve", "estimate")
    g.add_edge("estimate", "compose")
    g.add_edge("compose", "safety_check")
    g.add_edge("safety_check", "review")
    g.add_edge("review", END)
    return g.compile()


def run_turn(graph, memory: Memory, text: str):
    clean_text = text.strip() if isinstance(text, str) else ""
    if not clean_text:
        return {
            "session_id": memory.sid,
            "answer": "Please enter a system requirement or solar question.",
            "answer_source": "template",
            "status": memory.get_review()["status"],
        }
    memory.add_message("user", clean_text)
    return graph.invoke({"session_id": memory.sid, "user_text": clean_text})
