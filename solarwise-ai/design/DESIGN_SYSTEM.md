# SolarWise Design System v1.0

**Principle — engineering calm.** Dark ink for structure, solar amber for the single primary action,
semantic colours only for verdicts. Status is never colour-only: every badge carries an icon and a word.

## Token architecture (3 layers) — `tokens.json` → `tokens.css`
| Layer | Purpose | Example |
|---|---|---|
| Primitive | raw values | `--sw-primitive-color-amber-500` |
| Semantic | purpose aliases | `--sw-semantic-action-primary` → amber-500 |
| Component | per-component | `--sw-component-button-bg` → action-primary |

Regenerate: `python design/build_tokens.py`. Compliance (no raw hex outside tokens):
`python design/build_tokens.py --check app.py solarwise/ui.py design/components.css`.

## Type & space
Display: Space Grotesk (metrics, headings) · UI: Inter · Citations: JetBrains Mono. Fallback stacks included.
Scale 12 / 13.5 / 15 / 18 / 24 / 32 px; spacing 4·8·12·16·24·32·48; radius 6/10/16.

## Components
| Component | Default | Hover | Focus | Disabled |
|---|---|---|---|---|
| Primary button | amber bg, ink text | amber-600 | 3px ink-700 ring | muted (Streamlit) |
| Status badge | tinted bg + coloured text + icon | — | — | — |
| Card | white, 1px slate-200 border, radius 16, shadow sm | — | — | — |
| Metric tile | slate-50 bg, Space Grotesk value, uppercase label | — | — | — |
| Citation chip | slate-100 bg, mono, 📄 manufacturer model rev file page | — | — | — |
| Preliminary banner | amber-100, amber left rule | — | — | — |

## Status semantics
| Status | Colour | Icon | Meaning |
|---|---|---|---|
| ok | teal | ✔ | check passed under stated assumptions |
| warn | orange | ⚠ | passed but needs engineer attention |
| fail | red | ✖ | limit violated |
| incomplete | slate | … | inputs missing — no verdict is given |

## Accessibility & responsiveness
Ink-on-white and ink-on-amber exceed WCAG AA contrast; focus rings on all controls; `prefers-reduced-motion`
respected; header stacks and padding tightens below 640 px; PDF mirrors the palette and always carries the stamp.
