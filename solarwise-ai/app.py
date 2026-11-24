"""SolarWise — Streamlit app. Run: streamlit run app.py"""
from __future__ import annotations
import time, uuid
import pandas as pd
import streamlit as st
from solarwise import calc, catalog, config, solar_data, ui
from solarwise.boq import generate_boq
from solarwise.evaluate import run as run_eval
from solarwise.graph import build_graph
from solarwise.llm import GeminiClient
from solarwise.memory import Memory
from solarwise.report import build_report
from solarwise.retriever import get_retriever, retrieve_datasheet
from solarwise.schemas import Citation, ToolResult

st.set_page_config(page_title="SolarWise", page_icon=":material/solar_power:", layout="wide")
ui.inject_css()


@st.cache_resource(show_spinner="Loading datasheet index…")
def _retriever():
    return get_retriever()


if "sid" not in st.session_state:
    st.session_state.sid = uuid.uuid4().hex[:10]
    st.session_state.results = {}          # tool name -> ToolResult (feeds the report)
sid = st.session_state.sid
mem = Memory(sid)
llm = GeminiClient(mem)
graph = build_graph(mem, _retriever(), llm)
mods, invs = catalog.modules(), catalog.inverters()

ui.header()
review = mem.get_review()
ui.banner("<b>Preliminary tool.</b> Results are not an installation approval. Every value shows its assumptions or datasheet page, "
          "and only a qualified reviewer can mark a design approved.")

with st.sidebar:
    st.subheader("Project")
    st.caption(f"ID `{sid}`")
    s_calls, d_calls = mem.llm_calls_today()
    mode = "Gemini + tools" if llm.available else ("Calculator-only (limit reached)" if llm.configured else "Calculator-only (no API key)")
    st.markdown(f"**Mode:** {mode}")
    st.caption(f"Model: `{config.GEMINI_MODEL}`")
    st.caption(f"LLM usage: {s_calls}/{config.MAX_LLM_CALLS_PER_SESSION} session · {d_calls}/{config.MAX_LLM_CALLS_PER_DAY} daily")
    st.caption(f"Retriever: `{_retriever().name}`")
    status_label = review["status"].replace("_", " ").title()
    status_color = "green" if review["status"] == "approved" else ("orange" if review["status"] == "pending_review" else "gray")
    st.markdown(f"**Design status**")
    st.badge(status_label, icon=":material/check_circle:" if review["status"] == "approved" else ":material/pending:", color=status_color)
    if st.button("Start new project", icon=":material/add:", width="stretch"):
        mem.clear_project(); st.session_state.results = {}; st.rerun()
    with st.expander("Confirmed project data", expanded=False):
        st.json({k: v for k, v in mem.get_project().items() if not k.startswith("_")} or {"(empty)": ""})
    st.caption(catalog.note())

tab_chat, tab_calc, tab_ds, tab_boq, tab_rev, tab_eval = st.tabs([
    ":material/chat: Assistant",
    ":material/calculate: Calculator",
    ":material/article: Datasheet Q&A",
    ":material/inventory_2: BOQ & report",
    ":material/verified: Review",
    ":material/analytics: Evaluation",
])


def words(text: str):
    for w in text.split(" "):
        yield w + " "
        time.sleep(0.012)


# ------------------------------------------------------------------ chat
with tab_chat:
    hist = mem.history()
    if not hist:
        st.markdown("**Project assistant**")
        st.caption("Ask a sizing, compatibility, generation, or datasheet question.")
        suggestions = [
            "Calculate modules for a 50 kW system",
            "Check Voc for LR8-66HGD-615M",
            "Check a string on Solis S5-GC50K",
        ]
        suggestion = st.pills("Suggested prompts", suggestions, selection_mode="single", label_visibility="collapsed")
        prompt = st.chat_input("Ask a solar design or datasheet question…")
        if suggestion and not prompt:
            prompt = suggestion
    else:
        prompt = st.chat_input("Ask a solar design or datasheet question…")
    for m in hist:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
    if prompt:
        with st.chat_message("user"):
            st.markdown(prompt)
        mem.add_message("user", prompt)
        final: dict = {}
        with st.chat_message("assistant"):
            with st.status("Working…", expanded=False) as status:          # Elective 1: live node progress
                for upd in graph.stream({"session_id": sid, "user_text": prompt}, stream_mode="updates"):
                    for node, out in upd.items():
                        final.update(out or {})
                        status.update(label=f"✔ {node}")
                status.update(label="Done", state="complete")
            st.write_stream(words(final.get("answer", "")))
            for c in (final.get("citations") or [])[:2]:
                ui.citation(c)
            for r in final.get("results") or []:
                st.session_state.results[r.tool] = r

# ------------------------------------------------------------------ calculator
with tab_calc:
    sub = st.tabs(["Panel count", "Roof area", "DC/AC", "String voltage", "MPPT current", "Battery", "Generation", "Payback"])
    mod_opts = {f"{m.manufacturer} {m.model}": m for m in mods}
    inv_opts = {f"{i.manufacturer} {i.model}": i for i in invs}

    with sub[0]:
        c1, c2 = st.columns(2)
        tkw = c1.number_input("Target PV capacity (kW, minimum DC)", 0.1, 10000.0, 50.0, 0.5)
        wp = c2.number_input("Module rating (W per module)", 100.0, 1000.0, 615.0, 5.0)
        r = calc.calculate_panel_count(tkw, wp); ui.result_card(r, "Panel count")
        st.session_state.results[r.tool] = r
        mem.update_project(target_kw=tkw, module_wp=wp)

    with sub[1]:
        m_sel = st.selectbox("Module", list(mod_opts), key="roof_mod")
        n = st.number_input("Module quantity", 1, 100000, 82)
        lf = st.slider("Layout factor (clearances, row spacing)", 1.0, 3.0, 1.3, 0.05)
        m = mod_opts[m_sel]
        ui.result_card(calc.calculate_roof_area(n, m.length_mm, m.width_mm, lf), "Roof area")

    with sub[2]:
        c1, c2 = st.columns(2)
        kwp = c1.number_input("PV DC capacity (kWp)", 0.1, 10000.0, 50.43)
        ac = c2.number_input("Inverter AC rating (kW)", 0.1, 10000.0, 50.0)
        r = calc.calculate_dc_ac_ratio(kwp, ac); ui.result_card(r, "DC/AC ratio"); st.session_state.results[r.tool] = r

    with sub[3]:
        manual = st.checkbox("Enter module / inverter values manually (model not in catalogue)")
        c1, c2 = st.columns(2)
        if manual:
            voc = c1.number_input("Module Voc (V)", 0.0, 100.0, 0.0); vmp = c1.number_input("Module Vmp (V)", 0.0, 100.0, 0.0)
            tc = c1.number_input("Voc temp. coefficient (%/°C, negative)", -1.0, 0.0, 0.0, 0.001, format="%.3f")
            dcmax = c2.number_input("Inverter max DC voltage (V)", 0.0, 1500.0, 0.0)
            mmin = c2.number_input("MPPT min (V)", 0.0, 1500.0, 0.0); mmax = c2.number_input("MPPT max (V)", 0.0, 1500.0, 0.0)
            vals = dict(voc_v=voc or None, vmp_v=vmp or None, temp_coeff_voc_pct_per_c=tc or None, inverter_max_dc_v=dcmax or None, mppt_min_v=mmin or None, mppt_max_v=mmax or None)
        else:
            ms = c1.selectbox("Module", list(mod_opts), key="s_mod"); is_ = c2.selectbox("Inverter", list(inv_opts), key="s_inv")
            mm, ii = mod_opts[ms], inv_opts[is_]
            c1.caption(f"Voc {mm.voc_v} V · Vmp {mm.vmp_v} V · βVoc {mm.temp_coeff_voc_pct_per_c} %/°C · Source: {mm.source_file} p.{mm.source_page}")
            c2.caption(f"Max DC {ii.max_dc_voltage_v:g} V · MPPT {ii.mppt_min_v:g}–{ii.mppt_max_v:g} V · Source: {ii.source_file} p.{ii.source_page}")
            vals = dict(voc_v=mm.voc_v, vmp_v=mm.vmp_v, temp_coeff_voc_pct_per_c=mm.temp_coeff_voc_pct_per_c, inverter_max_dc_v=ii.max_dc_voltage_v,
                        mppt_min_v=ii.mppt_min_v, mppt_max_v=ii.mppt_max_v, module_max_system_v=mm.max_system_voltage_v)
            mem.update_project(module_id=mm.id, inverter_id=ii.id)
        c1, c2, c3 = st.columns(3)
        nps = c1.number_input("Modules per string", 1, 60, 20)
        tmin = c2.number_input("Minimum site temperature (°C)", -60.0, 30.0, 5.0)
        tmax = c3.number_input("Maximum ambient temperature (°C)", 0.0, 70.0, 40.0)
        rise = st.slider("Cell temperature rise above ambient (°C, assumption)", 15, 45, 30)
        r = calc.check_string_voltage(modules_per_string=nps, min_temp_c=tmin, max_temp_c=tmax, cell_temp_rise_c=rise, **vals)
        ui.result_card(r, "String voltage check"); st.session_state.results[r.tool] = r
        mem.update_project(modules_per_string=nps, min_temp_c=tmin, max_temp_c=tmax)

    with sub[4]:
        ms = st.selectbox("Module", list(mod_opts), key="m_mod"); is_ = st.selectbox("Inverter", list(inv_opts), key="m_inv")
        mm, ii = mod_opts[ms], inv_opts[is_]
        par = st.number_input("Parallel strings per MPPT", 1, 10, 2)
        bif = st.slider("Bifacial current gain factor (1.0 = ignore)", 1.0, 1.3, 1.0, 0.01)
        r = calc.check_mppt_current(mm.isc_a, mm.imp_a, par, ii.max_input_current_per_mppt_a, ii.max_isc_per_mppt_a, bif)
        ui.result_card(r, "MPPT current check"); st.session_state.results[r.tool] = r

    with sub[5]:
        c1, c2, c3, c4 = st.columns(4)
        r = calc.calculate_battery_energy(c1.number_input("Voltage (V)", 1.0, 1000.0, 51.2), c2.number_input("Capacity (Ah)", 1.0, 5000.0, 100.0),
                                          c3.number_input("Quantity", 1, 1000, 2), c4.number_input("Usable DoD (%)", 1.0, 100.0, 90.0))
        ui.result_card(r, "Battery energy")

    with sub[6]:
        c1, c2, c3 = st.columns(3)
        lat = c1.number_input("Latitude (°)", -90.0, 90.0, 22.34, format="%.4f"); lon = c2.number_input("Longitude (°)", -180.0, 180.0, 91.83, format="%.4f")
        g_kwp = c3.number_input("PV capacity (kWp)", 0.1, 10000.0, 50.43, key="g_kwp")
        c1, c2, c3, c4 = st.columns(4)
        tilt = c1.number_input("Tilt (°)", 0.0, 90.0, 15.0); az = c2.number_input("Azimuth (° , 0 = south)", -180.0, 180.0, 0.0)
        loss = c3.number_input("System losses (%)", 0.0, 60.0, 14.0); psh = c4.number_input("Manual peak sun hours (fallback)", 0.0, 12.0, 0.0)
        if st.button("Estimate generation", type="primary"):
            with st.spinner("Querying PVGIS / NASA POWER…"):
                st.session_state.gen = solar_data.estimate_generation(lat, lon, g_kwp, tilt, az, loss, psh or None)
            st.session_state.results["estimate_solar_generation"] = st.session_state.gen
        if "gen" in st.session_state:
            ui.result_card(st.session_state.gen, "Solar generation estimate")
            mv = st.session_state.gen.values.get("monthly_kwh")
            if isinstance(mv, list):
                st.bar_chart(pd.DataFrame({"kWh": mv}, index=["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]))

    with sub[7]:
        c1, c2, c3, c4 = st.columns(4)
        r = calc.calculate_payback(c1.number_input("Capex (your currency)", 0.0, 1e10, 3_000_000.0), c2.number_input("Annual energy (kWh)", 0.0, 1e9, 60_000.0),
                                   c3.number_input("Tariff (per kWh)", 0.0, 1000.0, 10.0), c4.number_input("O&M (% capex/yr)", 0.0, 20.0, 1.0))
        ui.result_card(r, "Simple payback")

# ------------------------------------------------------------------ datasheet Q&A
with tab_ds:
    st.markdown("Ask for a value; answers cite manufacturer, model, revision and page. Weak or model-less matches are refused, not guessed.")
    q = st.text_input("Question", "What is the maximum DC input voltage of Solis S5-GC50K?")
    if q:
        out = retrieve_datasheet(_retriever(), q)
        if out["weak"] or not out["citations"]:
            st.warning("I can't verify this value from the supplied documents. Please provide the exact model datasheet or ask a qualified engineer.")
        else:
            if not out["exact_model_named"]:
                st.info("No exact model named — name the module/inverter model for a reliable answer. Closest matches:")
            for c in out["citations"]:
                ui.citation(c)
    st.markdown("**Corpus manifest**")
    import json
    st.dataframe(pd.DataFrame(json.loads(config.MANIFEST_PATH.read_text())).drop(columns=["source_url"]), width='stretch', hide_index=True)

# ------------------------------------------------------------------ BOQ & report
with tab_boq:
    p = mem.get_project()
    c1, c2 = st.columns(2)
    ms = c1.selectbox("Module", ["—"] + list(mod_opts), index=(list(mod_opts).index(next((k for k, v in mod_opts.items() if v.id == p.get("module_id")), "")) + 1) if p.get("module_id") else 0)
    is_ = c2.selectbox("Inverter", ["—"] + list(inv_opts), index=(list(inv_opts).index(next((k for k, v in inv_opts.items() if v.id == p.get("inverter_id")), "")) + 1) if p.get("inverter_id") else 0)
    mm = mod_opts.get(ms); ii = inv_opts.get(is_)
    c1, c2, c3 = st.columns(3)
    target = c1.number_input("Target PV (kW)", 0.0, 10000.0, float(p.get("target_kw") or 0.0))
    wp_ = mm.pmax_w if mm else c2.number_input("Module W (if none selected)", 0.0, 1000.0, float(p.get("module_wp") or 0.0))
    nps_ = c3.number_input("Modules per string", 0, 60, int(p.get("modules_per_string") or 0))
    cable = st.number_input("DC cable length (m, optional)", 0.0, 100000.0, 0.0)
    pc = calc.calculate_panel_count(target or None, wp_ or None)
    n = pc.values.get("module_count")
    boq = generate_boq(mm, n, ii, 1, nps_ or None, cable or None)
    st.dataframe(pd.DataFrame(boq["rows"])[["item", "specification", "qty", "unit", "source"]].astype(str), width='stretch', hide_index=True)
    for w in boq["warnings"]:
        st.warning(w)
    st.caption(boq["note"])
    results = list(st.session_state.results.values()) + ([pc] if n and "calculate_panel_count" not in st.session_state.results else [])
    cites = [Citation(manufacturer=x.manufacturer, model=x.model, revision=x.revision, source_file=x.source_file, page=x.source_page) for x in (mm, ii) if x]
    proj = {k: v for k, v in p.items() if not k.startswith("_")}
    pdf = build_report(proj, results, cites, boq, review)
    st.download_button("Export PDF report", pdf, file_name=f"solarwise_{sid}.pdf", mime="application/pdf", type="primary", icon=":material/download:")

# ------------------------------------------------------------------ human review
with tab_rev:
    st.markdown(f"**Current status:** `{review['status']}`  ·  Reviewer: {review.get('reviewer') or '—'}")
    if st.button("Request engineer review"):
        mem.set_review("pending_review"); st.rerun()
    st.divider()
    st.markdown("**Reviewer sign-off** — only a qualified solar/electrical engineer appointed by the project team may approve.")
    if not config.REVIEWER_PIN:
        st.info("Approval is disabled: set `REVIEWER_PIN` in `.env` to enable sign-off.")
    name = st.text_input("Reviewer name"); pin = st.text_input("Reviewer PIN", type="password"); note = st.text_area("Review note")
    if st.button("Approve preliminary design", disabled=not config.REVIEWER_PIN):
        if name and pin == config.REVIEWER_PIN:
            mem.set_review("approved", name, note); st.success("Marked approved. The PDF stamp updates accordingly."); st.rerun()
        else:
            st.error("Reviewer name and a correct PIN are required.")
    if review["status"] == "approved" and st.button("Revoke approval"):
        mem.set_review("preliminary"); st.rerun()

# ------------------------------------------------------------------ eval & trace
with tab_eval:
    st.markdown("**RAG evaluation** — hand-labelled datasheet questions (value, file, page) plus refusal tests.")
    if st.button("Run evaluation", type="primary"):
        with st.spinner("Running…"):
            st.session_state.eval = run_eval(_retriever())
    if "eval" in st.session_state:
        e = st.session_state.eval
        st.markdown(ui.metrics({"Retrieval hit@3": f"{e['retrieval_hit_rate']}%", "Citation correct": f"{e['citation_correctness']}%",
                                "Answer correct": f"{e['answer_correctness']}%", "Refusal tests": f"{e['refusal_pass_rate']}%"}), unsafe_allow_html=True)
        df = pd.DataFrame(e["rows"]); df["result"] = df["passed"].map({True: "✔ pass", False: "✖ fail"})
        st.dataframe(df.drop(columns=["passed"]), width='stretch', hide_index=True)
    st.markdown("**Request trace (this session)** — node latency")
    tr = mem.traces()
    st.dataframe(pd.DataFrame(tr) if tr else pd.DataFrame({"info": ["No requests yet"]}), width='stretch', hide_index=True)
