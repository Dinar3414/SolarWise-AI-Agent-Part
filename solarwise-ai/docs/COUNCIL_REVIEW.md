# LLM Council review of the SolarWise AI proposal

Five advisors (Contrarian, First-Principles, Expansionist, Outsider, Executor) reviewed the proposal and the supplied
datasheet pack. Note: run as a single-model simulation (no sub-agent runner was available), not five independent calls.

## Where the advisors agreed
- The architecture is right: Python does arithmetic, RAG supplies cited specs, the LLM only explains.
- "Incomplete, not a guess" and the human-approval gate are the strongest parts — they are the product.

## Challenges and what was done
| Advisor | Challenge | Resolution in the build |
|---|---|---|
| Contrarian | PDF tables are lossy; JA and one LONGi datasheet have **no text layer** (scanned) | OCR fallback (2-pass Tesseract) + a human-reviewed catalogue with page citations; Vmp×Imp≈Pmax consistency test |
| Contrarian | "Voc of LONGi module" is ambiguous (depends on power class) | System asks which variant instead of answering |
| Contrarian | LLM can restate a wrong number | Numeric-grounding check: any number not in tool results/evidence → template fallback |
| First-Principles | Is a chatbot needed at all? | Calculator tabs work with no LLM; chat is a convenience layer; calculator-only mode is the default fallback |
| Outsider | Terms like MPPT/DC-AC are opaque | Assumptions and plain-language warnings on every result card |
| Expansionist | Pack also contains batteries, cables, Schneider DC breakers, price lists, a 180 kW net-metering spec | Roadmap: ingest them; BOQ priced from price lists; protection sizing with C60H-DC data |
| Executor | 3 weeks of scope | Cut: advanced battery modelling, deployment. Kept: panel count, DC/AC, string/MPPT, cited Q&A, BOQ, PDF, review |

## Proposal issues worth fixing before submission
1. Section 7 says **Python 3.18** (does not exist) — use 3.11/3.12.
2. Concept matrix rows "E?" need IDs (E1 Streaming, E2 RAG Evaluation).
3. The cost table (model calls/day) is empty.
4. Page 3 mislabels some items; confirm the demo script matches the app (string check needs temperatures + inverter limits).
5. Datasheet pack contains price lists and a possibly-confidential offer (Technical Specification 180KW…): the proposal promises no employer-confidential files — confirm permission before indexing those.
