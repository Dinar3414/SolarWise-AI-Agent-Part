from solarwise.evaluate import run
from solarwise.retriever import LexicalRetriever, detect_files


def test_model_filter_detection():
    assert detect_files("Voc of LR8-66HGD-615M") == ["longi_lr8-66hgd_595-625m.pdf"]
    assert "solis_s5-gc_50-60k.pdf" in detect_files("max voltage S5-GC50K")


def test_eval_suite_passes():
    res = run(LexicalRetriever())
    failed = [r["id"] for r in res["rows"] if not r["passed"]]
    assert not failed, failed
    assert res["n_datasheet"] >= 15
