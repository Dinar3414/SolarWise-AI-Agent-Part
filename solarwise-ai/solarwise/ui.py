"""Design-system components for Streamlit. All styling lives in design/*.css (token-driven)."""
from __future__ import annotations
import html
import streamlit as st
from . import config
from .schemas import ToolResult

ICON = {"ok": "✔", "warn": "⚠", "fail": "✖", "incomplete": "…"}
LABEL = {"ok": "Pass", "warn": "Check", "fail": "Fail", "incomplete": "Incomplete"}
e = html.escape


def inject_css():
    css = (config.ROOT / "design" / "tokens.css").read_text() + (config.ROOT / "design" / "components.css").read_text()
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def header():
    st.markdown('<div class="sw-header"><div class="sw-logo" aria-label="SolarWise">SW</div><div><h1>SolarWise</h1>'
                '<p>Rooftop solar engineering workspace</p></div></div>',
                unsafe_allow_html=True)


def banner(text: str):
    st.markdown(f'<div class="sw-banner">{text}</div>', unsafe_allow_html=True)


def badge(status: str) -> str:
    return f'<span class="sw-badge sw-badge--{status}">{ICON.get(status,"")} {LABEL.get(status, status)}</span>'


def metrics(items: dict) -> str:
    cells = "".join(f'<div class="sw-metric"><div class="v">{e(str(v))}</div><div class="l">{e(str(k))}</div></div>' for k, v in items.items())
    return f'<div class="sw-metrics">{cells}</div>'


def result_card(r: ToolResult, title: str | None = None):
    simple = {k: v for k, v in r.values.items() if isinstance(v, (int, float, str)) and k not in ("source",)}
    body = [f'<div class="sw-card"><h4>{e(title or r.tool.replace("_", " ").title())} &nbsp;{badge(r.status)}</h4>']
    if simple:
        body.append(metrics(simple))
    if r.values.get("source"):
        body.append(f'<div class="sw-note">Source: {e(str(r.values["source"]))}</div>')
    if r.missing:
        body.append('<div class="sw-warn"><b>Missing:</b> ' + e("; ".join(r.missing)) + "</div>")
    body += [f'<div class="sw-warn">⚠ {e(w)}</div>' for w in r.warnings]
    body += [f'<div class="sw-note">Assumption: {e(a)}</div>' for a in r.assumptions]
    body.append("</div>")
    st.markdown("".join(body), unsafe_allow_html=True)


def citation(c):
    rev = f" · rev {e(c.revision)}" if c.revision else ""
    ex = f"\n{e(c.text[:420])}" if c.text else ""
    st.markdown(f'<span class="sw-cite"><strong>Source</strong> · {e(c.manufacturer)} {e(c.model)}{rev} — {e(c.source_file)}, page {c.page}{ex}</span>', unsafe_allow_html=True)
