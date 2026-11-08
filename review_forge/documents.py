from __future__ import annotations

import io
import re
from pathlib import Path

from .http import HTTP
from .sources import arxiv_id, parse_arxiv
from .util import ReviewError, digest, normalized, now, read_json, write_bytes, write_json


def source_signature(paper: dict) -> str:
    descriptor = {k: paper.get(k) for k in ("arxiv_id", "updated", "pdf_url", "local_path", "abstract")}
    if paper.get("local_path"):
        path = Path(paper["local_path"])
        if not path.is_file():
            raise ReviewError(f"Local source is missing for {paper['id']}.")
        descriptor["local_sha256"] = digest(path.read_bytes())
    return digest(descriptor)


def processing_signature(config: dict) -> str:
    return digest({k: config["documents"][k] for k in ("chunk_chars", "min_fulltext_chars")})


def blocks(text: str, prefix: str, locator: str) -> list[dict]:
    result = []
    for index, block in enumerate(re.split(r"\n\s*\n", text), 1):
        block = normalized(block)
        if block:
            result.append({"id": f"{prefix}-b{index:04d}", "locator": locator, "text": block})
    return result


def extract_pdf(raw: bytes) -> list[dict]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ReviewError("PDF extraction needs pypdf. Install with: python -m pip install -e '.[pdf]'.") from exc
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ReviewError("Encrypted PDF cannot be read without its password.")
        segments = []
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            segments.extend(blocks(text, f"p{number:04d}", f"PDF page {number}"))
        return segments
    except ReviewError:
        raise
    except Exception as exc:
        raise ReviewError("PDF parser failed. Provide a corrected local text/PDF source; OCR is not implemented.") from exc


def ingest_one(root: Path, paper: dict, config: dict, http: HTTP, refresh=False) -> dict:
    folder = root / "corpus" / paper["id"]
    signature = source_signature(paper)
    cached = read_json(folder / "document.json")
    if cached and cached.get("source_signature") == signature and cached.get("processing_signature") == processing_signature(config) and not refresh:
        if cached["coverage"] == "abstract_only" and not config["documents"]["allow_abstract_only"]:
            raise ReviewError("Cached source is abstract-only; full text is required by current config.")
        return cached
    warnings, segments, raw = [], [], b""
    url = paper.get("pdf_url") or ""
    resolved_arxiv = paper.get("arxiv_id") or ""
    if paper.get("local_path"):
        path = Path(paper["local_path"])
        raw = path.read_bytes()
        suffix = path.suffix.lower()
        segments = extract_pdf(raw) if suffix == ".pdf" else blocks(raw.decode("utf-8"), "text", "Local text paragraph")
        source = {"kind": "local", "path": str(path), "format": suffix}
    else:
        source = {"kind": "remote", "url": url, "format": ".pdf"}
        if resolved_arxiv:
            # Resolve the exact version even if Semantic Scholar only supplied the base ID.
            try:
                aid = arxiv_id(resolved_arxiv)
                metadata = http.get("https://export.arxiv.org/api/query?id_list=" + aid, delay=3.1)
                records, _ = parse_arxiv(metadata)
                if not records:
                    raise ReviewError("arXiv version lookup returned no paper.")
                candidate_arxiv = records[0]["arxiv_id"]
                if re.sub(r"v\d+$", "", candidate_arxiv) != re.sub(r"v\d+$", "", aid) or (re.search(r"v\d+$", aid) and aid != candidate_arxiv):
                    raise ReviewError("arXiv metadata returned a different identifier/version.")
                resolved_arxiv = candidate_arxiv
                folder.mkdir(parents=True, exist_ok=True)
                write_bytes(folder / "metadata" / f"{digest(metadata)}.xml", metadata)
                url = f"https://arxiv.org/pdf/{resolved_arxiv}"
                source["url"] = url
            except ReviewError as exc:
                # Versioned URLs remain usable when the metadata API is temporarily unavailable.
                if not re.search(r"v\d+$", paper.get("arxiv_id", "")):
                    raise ReviewError("Cannot resolve an exact arXiv version; retry metadata lookup before ingesting.") from exc
                resolved_arxiv = arxiv_id(paper["arxiv_id"])
                url = f"https://arxiv.org/pdf/{resolved_arxiv}"
                source["url"] = url
                warnings.append(str(exc))
        if url:
            try:
                raw = http.get(url)
                if not raw.startswith(b"%PDF-"):
                    raw = b""
                    raise ReviewError("Source returned a non-PDF body (possibly a login or challenge page).")
                segments = extract_pdf(raw)
            except ReviewError as exc:
                warnings.append(str(exc))
        else:
            warnings.append("No full-text URL is available; add an explicit local_path.")
    coverage = "full_text"
    if sum(len(s["text"]) for s in segments) < config["documents"]["min_fulltext_chars"]:
        if not config["documents"]["allow_abstract_only"] or not paper.get("abstract"):
            raise ReviewError("Insufficient readable full text. " + " ".join(warnings) + " Supply a local text file or explicitly enable abstract-only mode.")
        coverage = "abstract_only"
        segments = blocks(paper["abstract"], "abstract", "Bibliographic abstract only")
        warnings.append("Only an abstract was read; methods, numerical results and limitations may be missing.")
    else:
        warnings.append("PDF/text extraction may lose equations, tables and reading order; figures and OCR are not interpreted.")
    # Split very long paragraphs so every source byte represented in text reaches a model chunk.
    bounded = []
    bound = config["documents"]["chunk_chars"]
    for segment in segments:
        text = segment["text"]
        pieces = [text[i:i + bound] for i in range(0, len(text), bound)]
        for index, piece in enumerate(pieces, 1):
            bounded.append({**segment, "id": segment["id"] + (f"-s{index:03d}" if len(pieces) > 1 else ""), "text": piece})
    document = {"paper_id": paper["id"], "source_signature": signature, "at": now(),
                "processing_signature": processing_signature(config),
                "coverage": coverage, "resolved_arxiv_id": resolved_arxiv, "source": source,
                "source_sha256": digest(raw) if raw else None, "segments_sha256": digest(bounded),
                "license": paper.get("license", "unknown"), "warnings": warnings, "segments": bounded}
    folder.mkdir(parents=True, exist_ok=True)
    if raw:
        document["source_file"] = "sources/" + digest(raw) + source["format"]
        target = folder / document["source_file"]
        if not target.exists():
            write_bytes(target, raw)
        elif digest(target.read_bytes()) != document["source_sha256"]:
            raise ReviewError("Preserved source checksum mismatch; inspect the corpus before proceeding.")
    write_json(folder / "document.json", document)
    return document


def chunk_document(document: dict, max_chars: int) -> list[list[dict]]:
    chunks, current, size = [], [], 0
    for segment in document["segments"]:
        length = len(segment["text"])
        if current and size + length > max_chars:
            chunks.append(current)
            current, size = [], 0
        current.append(segment)
        size += length
    if current:
        chunks.append(current)
    return chunks
