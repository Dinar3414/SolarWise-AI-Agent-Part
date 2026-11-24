# SolarWise AI

Preliminary rooftop-solar design assistant (CSE Industrial Training 2026 · Grow with Data). Python tools do all electrical
arithmetic; RAG retrieves **cited** manufacturer specs; the LLM (Gemini) only explains. Output is always
*Preliminary — not for installation* until a human reviewer signs off.

## Run
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # add GEMINI_API_KEY (optional) and REVIEWER_PIN
python -m solarwise.ingest                             # rebuild chunks (needs poppler + tesseract for scanned PDFs)
streamlit run app.py
pytest -q                                              # 27 tests
python -m solarwise.evaluate                           # RAG evaluation (22 questions)
```
No API key → calculator-only mode. `RETRIEVER=lexical` needs no model download; `auto` tries Qdrant + MiniLM first.

## Concept coverage
| ID | Where |
|---|---|
| C1 prompts/structured output | `prompts/solarwise_assistant.yaml`, `solarwise/schemas.py` |
| C2 model layer & tracing | `llm.py` (model ID in .env), node/LLM latency in SQLite → *Evaluation & Trace* tab |
| C3 tools & agent loop | `calc.py`, `solar_data.py`, `boq.py` |
| C4 memory | `memory.py` (SQLite; follow-ups reuse confirmed inputs) |
| C5 safety rails | `safety.py`, Pydantic bounds, numeric-grounding check, call limits, refusal policy, review gate |
| C6 RAG with receipts | `ingest.py`, `retriever.py`; chunks keep manufacturer/model/revision/file/page |
| C7 graph | `graph.py` (LangGraph, conditional routing) |
| E1 streaming | workflow-node status + word-streamed reply in the chat tab |
| E2 RAG evaluation | `evaluate.py`, `eval/questions.json` |

## Honest status
- Verified in this build: 27 unit/integration tests pass; evaluation 22/22 with the **lexical** retriever (16 datasheet Qs, 6 refusal tests; 100% retrieval hit@3, citation and answer correctness).
- **Not exercised here (no network/model access):** Gemini calls, PVGIS/NASA POWER requests, and the Qdrant + MiniLM retriever. Code paths exist and fall back safely; test them on your machine and re-run the eval with `RETRIEVER=qdrant`.
- Catalogue: 12 modules, 8 inverters transcribed from your datasheets (page cited). Scanned-PDF values were checked visually; an engineer must still re-verify each before approval. Only 11 of ~97 supplied files are indexed; battery/cable/price lists are roadmap.
- Eval questions are hand-written by the builder from the same documents — extend with independently written questions before the demo.

See `design/DESIGN_SYSTEM.md` and `docs/COUNCIL_REVIEW.md`.
