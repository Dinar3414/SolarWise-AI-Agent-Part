"""RAG with receipts (C6). Replaceable retriever interface:
QdrantRetriever (embedded Qdrant + local sentence-transformers) with a LexicalRetriever (BM25)
fallback that needs no model download — the fallback the proposal's risk table calls for.
Retrieval is model-aware: an exact model/manufacturer filter is applied when the query names one.
"""
from __future__ import annotations
import json, re
from dataclasses import dataclass
from typing import Optional, Protocol
from rank_bm25 import BM25Okapi
from . import config
from .schemas import Citation

# regex aliases -> source_file (so "LR8-66HGD-615M" filters to the right document)
MODEL_ALIASES: dict[str, list[str]] = {
    "longi_lr8-66hgd_595-625m.pdf": [r"lr8-?66hgd"],
    "longi_lr7-72hgd_585-620m.pdf": [r"lr7-?72hgd", r"lr5-?72hgd"],
    "longi_lr7-72hvh_630-650m.pdf": [r"lr7-?72hvh"],
    "ja_jam66d45_605-630_lb_20250625.pdf": [r"jam ?66d45", r"ja ?solar"],
    "solis_s5-gc_25-40k.pdf": [r"s5-?gc ?(25|30|33|40)"],
    "solis_s5-gc_50-60k.pdf": [r"s5-?gc ?(50|60)"],
    "solis_s6-gc_80-125k.pdf": [r"s6-?gc ?(80|100|110|125)"],
    "deye_sun-18-25k-g05.pdf": [r"sun-?(18|20|22|23|25)k", r"deye"],
    "growatt_mod-10-15ktl3-x.pdf": [r"mod ?1\d{4}tl3", r"growatt"],
    "schneider_c60h-dc_a9n61515.pdf": [r"c60h?-?dc", r"a9n61515", r"schneider"],
}
SYNONYMS = {
    "voc": "open circuit voltage", "isc": "short circuit current", "vmp": "voltage at maximum power",
    "imp": "current at maximum power", "pmax": "maximum power", "mppt": "mppt voltage range",
    "weight": "weight kg", "size": "dimension", "dimensions": "dimension", "fuse": "maximum series fuse rating",
    "coefficient": "temperature coefficient", "efficiency": "efficiency", "startup": "start-up voltage",
    "max dc voltage": "max. input voltage", "maximum dc voltage": "max. input voltage",
}
STOP = set("what is the of a an for and to in on does do how much many value which with".split())


def _tok(s: str) -> list[str]:
    """Full tokens plus hyphen-split parts, so 'JAM66D45-615/LB' also matches a table column headed '615'."""
    out = []
    for t in re.findall(r"[a-z0-9][a-z0-9.\-]*", s.lower()):
        t = t.rstrip(".-")
        if t and t not in STOP:
            out.append(t)
            out += [p for p in t.split("-") if len(p) > 1 and p != t and p not in STOP]
    return out


def expand(q: str) -> str:
    out = q.lower()
    for k, v in SYNONYMS.items():
        if re.search(rf"\b{re.escape(k)}\b", out):
            out += " " + v
    return out


def detect_files(query: str) -> list[str]:
    q = query.lower()
    return [f for f, pats in MODEL_ALIASES.items() if any(re.search(p, q) for p in pats)]


class Retriever(Protocol):
    name: str
    def search(self, query: str, k: int = 4, source_files: Optional[list[str]] = None) -> list[Citation]: ...


def load_chunks() -> list[dict]:
    if not config.CHUNKS_PATH.exists():
        raise FileNotFoundError("Run `python -m solarwise.ingest` first.")
    return [json.loads(l) for l in config.CHUNKS_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]


def _cite(c: dict, score: float) -> Citation:
    return Citation(manufacturer=c["manufacturer"], model=c["model"], revision=c.get("revision", ""),
                    source_file=c["source_file"], page=c["page"], score=round(float(score), 3), text=c["text"])


class LexicalRetriever:
    name = "lexical-bm25"

    def __init__(self, chunks: Optional[list[dict]] = None):
        self.chunks = chunks or load_chunks()
        self.bm25 = BM25Okapi([_tok(c["text"] + " " + c["model"]) for c in self.chunks])

    def search(self, query, k=4, source_files=None):
        scores = self.bm25.get_scores(_tok(expand(query)))
        idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        out = []
        for i in idx:
            c = self.chunks[i]
            if source_files and c["source_file"] not in source_files:
                continue
            if scores[i] <= 0:
                break
            out.append(_cite(c, scores[i]))
            if len(out) == k:
                break
        return out


class QdrantRetriever:
    name = "qdrant"
    COLLECTION = "solarwise_datasheets"

    def __init__(self, chunks: Optional[list[dict]] = None):
        from qdrant_client import QdrantClient, models
        from sentence_transformers import SentenceTransformer
        self.models = models
        self.chunks = chunks or load_chunks()
        self.embedder = SentenceTransformer(config.EMBED_MODEL)  # same model for index + query
        self.client = QdrantClient(url=config.QDRANT_URL) if config.QDRANT_URL else QdrantClient(path=str(config.QDRANT_PATH))
        dim = self.embedder.get_sentence_embedding_dimension()
        if not self.client.collection_exists(self.COLLECTION):
            self.client.create_collection(self.COLLECTION, vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE))
            vecs = self.embedder.encode([c["text"] for c in self.chunks], normalize_embeddings=True)
            self.client.upsert(self.COLLECTION, points=[
                models.PointStruct(id=i, vector=v.tolist(), payload=c) for i, (c, v) in enumerate(zip(self.chunks, vecs))])

    def search(self, query, k=4, source_files=None):
        flt = None
        if source_files:
            flt = self.models.Filter(must=[self.models.FieldCondition(key="source_file", match=self.models.MatchAny(any=source_files))])
        vec = self.embedder.encode(expand(query), normalize_embeddings=True).tolist()
        hits = self.client.query_points(self.COLLECTION, query=vec, limit=k, query_filter=flt).points
        return [_cite(h.payload, h.score) for h in hits]


def get_retriever() -> Retriever:
    if config.RETRIEVER in ("auto", "qdrant"):
        try:
            return QdrantRetriever()
        except Exception:  # model not downloadable / qdrant unavailable -> documented fallback
            if config.RETRIEVER == "qdrant":
                raise
    return LexicalRetriever()


def retrieve_datasheet(retriever: Retriever, question: str, k: int = 3, min_score: float = 1.0) -> dict:
    """Model-filtered retrieval. Weak or model-less matches are flagged, never silently answered."""
    files = detect_files(question)
    hits = retriever.search(question, k=k, source_files=files or None)
    # Lexical BM25 scores and cosine scores are on different scales; only gate lexical.
    weak = (not hits) or (retriever.name.startswith("lexical") and hits[0].score < min_score)
    return {"citations": hits, "model_filter": files, "weak": weak, "exact_model_named": bool(files)}
