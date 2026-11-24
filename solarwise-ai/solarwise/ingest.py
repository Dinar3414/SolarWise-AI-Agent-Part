"""Corpus ingestion (C6): PDF -> page-level text (OCR fallback) -> metadata-rich chunks.

Run:  python -m solarwise.ingest
Writes data/chunks.jsonl and data/corpus_manifest.json. Every chunk keeps manufacturer,
exact model family, revision, source file and page so answers can cite a receipt.
"""
from __future__ import annotations
import json, re, shutil, subprocess, tempfile
from pathlib import Path
import pypdf
from docx import Document
from . import config

# Hand-written manifest metadata (issuing manufacturer, exact model family, revision/date).
DOC_META = {
    "longi_lr8-66hgd_595-625m.pdf": ("LONGi", "LR8-66HGD 595-625M", "20240705V03", "module"),
    "longi_lr7-72hgd_585-620m.pdf": ("LONGi", "LR7-72HGD 585-620M", "20230901 Preliminary V05", "module"),
    "longi_lr7-72hvh_630-650m.pdf": ("LONGi", "LR7-72HVH 630-650M", "Draft 20241118", "module"),
    "ja_jam66d45_605-630_lb_20250625.pdf": ("JA Solar", "JAM66D45 605-630/LB", "Global-EN-20250625A", "module"),
    "ja_jam66d45_610-630_lb_20240529.pdf": ("JA Solar", "JAM66D45 610-630/LB", "Global-EN-20240529A", "module"),
    "solis_s5-gc_25-40k.pdf": ("Solis", "S5-GC 25K-40K", "", "inverter"),
    "solis_s5-gc_50-60k.pdf": ("Solis", "S5-GC 50K-60K", "", "inverter"),
    "solis_s6-gc_80-125k.pdf": ("Solis", "S6-GC 80K-125K", "V3.9", "inverter"),
    "deye_sun-18-25k-g05.pdf": ("Deye", "SUN-18K-25K-G05", "2024-04-16", "inverter"),
    "growatt_mod-10-15ktl3-x.pdf": ("Growatt", "MOD 10000-15000TL3-X", "", "inverter"),
    "schneider_c60h-dc_a9n61515.pdf": ("Schneider Electric", "Acti9 C60H-DC A9N61515", "", "protection"),
}
MIN_TEXT_CHARS = 150  # below this a page is treated as an image and OCR'd
CHUNK_CHARS, OVERLAP = 900, 120


def ocr_page(pdf: Path, page_no: int) -> str:
    if not (shutil.which("pdftoppm") and shutil.which("tesseract")):
        return ""
    with tempfile.TemporaryDirectory() as td:
        stem = Path(td) / "p"
        subprocess.run(["pdftoppm", "-r", "300", "-f", str(page_no), "-l", str(page_no), "-png", str(pdf), str(stem)],
                       check=True, capture_output=True)
        img = next(Path(td).glob("p*.png"))
        # Two layout modes: psm 6 (uniform block) keeps row order; psm 11 (sparse text) recovers table cells psm 6 garbles.
        texts = [subprocess.run(["tesseract", str(img), "-", "--psm", m], capture_output=True, text=True).stdout for m in ("6", "11")]
        return "\n".join(texts)


def page_texts(pdf: Path) -> list[tuple[str, str]]:
    """[(text, method)] per page."""
    reader, res = pypdf.PdfReader(str(pdf)), []
    for i, p in enumerate(reader.pages, 1):
        txt = (p.extract_text() or "").strip()
        if len(txt) >= MIN_TEXT_CHARS:
            res.append((txt, "text-layer"))
        else:
            ocr = ocr_page(pdf, i).strip()
            res.append(((txt + "\n" + ocr).strip(), "ocr" if ocr else "text-layer"))
    return res


def clean(t: str) -> str:
    t = t.replace("\u2103", "°C").replace("ﬁ", "fi").replace("ﬀ", "ff")
    return re.sub(r"[ \t]+", " ", t)


def catalog_fallback(source_file: str) -> str:
    """Reviewed catalogue transcription fallback when PDF text/OCR is unavailable."""
    catalog_path = config.CATALOG_PATH
    if not catalog_path.exists():
        return ""
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    lines = []
    for module in catalog.get("modules", []):
        if module.get("source_file") == source_file:
            lines.append(
                f"{module['manufacturer']} {module['model']} "
                f"Maximum Power Pmax {module['pmax_w']} W "
                f"Open Circuit Voltage Voc {module['voc_v']} V "
                f"Short Circuit Current Isc {module['isc_a']} A "
                f"Voltage at Maximum Power Vmp {module['vmp_v']} V "
                f"Current at Maximum Power Imp {module['imp_a']} A "
                f"Temperature Coefficient of Voc {abs(module['temp_coeff_voc_pct_per_c'])}%/°C"
            )
    for inverter in catalog.get("inverters", []):
        if inverter.get("source_file") == source_file:
            lines.append(
                f"{inverter['manufacturer']} {inverter['model']} "
                f"Max DC Voltage {inverter['max_dc_voltage_v']} V "
                f"MPPT Voltage Range {inverter['mppt_min_v']}-{inverter['mppt_max_v']} V "
                f"Start-up Voltage {inverter.get('startup_voltage_v', '')}"
            )
    return "\n".join(lines)


def chunk(text: str) -> list[str]:
    lines, out, cur = text.splitlines(), [], ""
    for ln in lines:
        if len(cur) + len(ln) + 1 > CHUNK_CHARS and cur:
            out.append(cur.strip())
            cur = cur[-OVERLAP:] + "\n"
        cur += ln + "\n"
    if cur.strip():
        out.append(cur.strip())
    return out


def docx_text(path: Path) -> str:
    """Extract paragraph and table text from a Word offer."""
    document = Document(str(path))
    lines = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if text:
            lines.append(text)
    for table in document.tables:
        for row in table.rows:
            row_text = " | ".join(cell.text.strip().replace("\n", " ") for cell in row.cells if cell.text.strip())
            if row_text:
                lines.append(row_text)
    text = "\n".join(lines)
    keep = []
    for line in text.splitlines():
        if re.search(r"\b(?:Commercial Offer|Unit Price|Total Price|Total Amount|In Word)\b", line, re.I):
            continue
        if re.search(r"(?<![A-Za-z])(?:=|Rs\.?|Tk\.?|Taka)\s*[0-9][0-9,.]*", line, re.I):
            continue
        keep.append(line)
    text = "\n".join(keep)
    return clean(text)


def main() -> None:
    chunks, manifest = [], []
    for pdf in sorted(config.CORPUS_DIR.glob("*.pdf")):
        maker, model, rev, kind = DOC_META.get(pdf.name, ("Unknown", pdf.stem, "", "other"))
        pages = page_texts(pdf)
        pdf_chunks_before = len(chunks)
        manifest.append({"file": pdf.name, "manufacturer": maker, "model": model, "revision": rev,
                         "type": kind, "pages": len(pages),
                         "extraction": sorted({m for _, m in pages}),
                         "source_url": "(public manufacturer datasheet supplied by project team)"})
        for pno, (txt, method) in enumerate(pages, 1):
            for j, c in enumerate(chunk(clean(txt))):
                chunks.append({"id": f"{pdf.stem}:p{pno}:c{j}", "text": c, "manufacturer": maker, "model": model,
                               "revision": rev, "type": kind, "source_file": pdf.name, "page": pno,
                               "extraction": method})
        if len(chunks) == pdf_chunks_before:
            fallback = catalog_fallback(pdf.name)
            if fallback:
                for j, c in enumerate(chunk(fallback)):
                    fallback_page = next(
                        (m.get("source_page", 2) for m in json.loads(config.CATALOG_PATH.read_text(encoding="utf-8")).get("modules", [])
                         if m.get("source_file") == pdf.name),
                        2,
                    )
                    chunks.append({"id": f"{pdf.stem}:catalog:c{j}", "text": c, "manufacturer": maker,
                                   "model": model, "revision": rev, "type": kind, "source_file": pdf.name,
                                   "page": fallback_page, "extraction": "catalog-fallback"})
    for offer in sorted(config.CORPUS_DIR.glob("*.docx")):
        text = docx_text(offer)
        manifest.append({"file": offer.name, "manufacturer": "Labiba Engineering & Trading Company",
                         "model": "ON-Grid Solar Power Offer", "revision": "project offer",
                         "type": "offer", "pages": 1,
                         "extraction": ["docx"],
                         "source_url": "(supplied by project team)"})
        for j, c in enumerate(chunk(text)):
            chunks.append({"id": f"{offer.stem}:offer:c{j}", "text": c,
                           "manufacturer": "Labiba Engineering & Trading Company",
                           "model": "ON-Grid Solar Power Offer", "revision": "project offer",
                           "type": "offer", "source_file": offer.name, "page": 1,
                           "extraction": "docx"})
    config.CHUNKS_PATH.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in chunks), encoding="utf-8")
    config.MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(manifest)} documents, {len(chunks)} chunks -> {config.CHUNKS_PATH.name}")


if __name__ == "__main__":
    main()
