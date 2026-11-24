"""RAG evaluation (Elective 2): hand-labelled questions → retrieval hit rate, citation correctness,
answer correctness and refusal behaviour. Run: python -m solarwise.evaluate"""
from __future__ import annotations
import json, tempfile
from pathlib import Path
from . import config
from .graph import build_graph, run_turn
from .memory import Memory
from .retriever import Retriever, get_retriever, retrieve_datasheet

QUESTIONS = config.ROOT / "eval" / "questions.json"


def run(retriever: Retriever | None = None) -> dict:
    retriever = retriever or get_retriever()
    qs = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    tmp = Path(tempfile.mkdtemp()) / "eval.sqlite"
    rows = []
    for item in qs:
        mem = Memory(f"eval-{item['id']}", tmp)
        out = run_turn(build_graph(mem, retriever), mem, item["question"])
        ans = out.get("answer", "")
        if item["type"] == "datasheet":
            hits = retrieve_datasheet(retriever, item["question"])["citations"]
            hit = any(h.source_file == item["expected_file"] and h.page == item["expected_page"] and item["expected_value"] in h.text for h in hits)
            cites = out.get("citations", [])
            cite_ok = bool(cites) and cites[0].source_file == item["expected_file"] and cites[0].page == item["expected_page"]
            val_ok = item["expected_value"] in ans.replace(",", "")
            rows.append(dict(id=item["id"], type="datasheet", question=item["question"], retrieval_hit=hit, citation_ok=cite_ok, answer_ok=val_ok,
                             passed=hit and cite_ok and val_ok))
        else:
            exp, low = item["expect"], ans.lower()
            ok = {"refuse": "can't approve" in low or "can't advise" in low or "can't verify" in low,
                  "clarify": "which one" in low or "exact" in low,
                  "incomplete": "incomplete" in low and "won't guess" in low,
                  "no_answer": "can't verify" in low or "exact" in low}[exp]
            rows.append(dict(id=item["id"], type="refusal", question=item["question"], expect=exp, passed=ok))
    ds = [r for r in rows if r["type"] == "datasheet"]
    rf = [r for r in rows if r["type"] == "refusal"]
    pct = lambda xs: round(100 * sum(xs) / len(xs), 1) if xs else 0.0  # noqa: E731
    return {"retriever": retriever.name, "rows": rows, "n_datasheet": len(ds), "n_refusal": len(rf),
            "retrieval_hit_rate": pct([r["retrieval_hit"] for r in ds]), "citation_correctness": pct([r["citation_ok"] for r in ds]),
            "answer_correctness": pct([r["answer_ok"] for r in ds]), "refusal_pass_rate": pct([r["passed"] for r in rf]),
            "all_passed": all(r["passed"] for r in rows)}


if __name__ == "__main__":
    res = run()
    for r in res["rows"]:
        print(("PASS" if r["passed"] else "FAIL"), r["id"], r["question"][:70], {k: r[k] for k in ("retrieval_hit", "citation_ok", "answer_ok") if k in r})
    print({k: v for k, v in res.items() if k != "rows"})
