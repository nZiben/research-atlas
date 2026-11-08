from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlencode

from .http import HTTP
from .util import ReviewError, digest, normalized, now, read_json, write_json, write_text

ATOM = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", re.I)


def arxiv_id(value: str) -> str:
    value = re.sub(r"^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/", "", value).removesuffix(".pdf")
    if not ARXIV_ID.fullmatch(value):
        raise ReviewError("Invalid arXiv identifier.")
    return value


def identifiers(paper: dict) -> set[str]:
    ids = set(paper.get("aliases", []))
    if paper.get("arxiv_id"):
        ids.add("arxiv:" + re.sub(r"v\d+$", "", paper["arxiv_id"]).lower())
    if paper.get("doi"):
        ids.add("doi:" + paper["doi"].lower().removeprefix("https://doi.org/"))
    ids.add("title:" + re.sub(r"\W+", "", normalized(paper["title"]).lower()))
    return ids


def paper_fingerprint(paper: dict) -> str:
    return digest({k: paper.get(k) for k in ("title", "authors", "year", "abstract", "arxiv_id", "doi", "url", "local_path", "updated", "pdf_url")})


def active_queries(root: Path, config: dict) -> list[str]:
    if config["search"]["queries"]:
        return config["search"]["queries"]
    planned = read_json(root / "query_plan.json", {})
    if planned.get("review_hash") == digest(config["review"]):
        return planned["content"]["queries"]
    return [config["review"]["topic"]]


def merge_papers(existing: list[dict], incoming: list[dict]) -> list[dict]:
    merged = [dict(p) for p in existing]
    for paper in incoming:
        aliases = identifiers(paper)
        matches = [p for p in merged if identifiers(p) & aliases]
        if matches:
            target = matches[0]
            # A DOI/arXiv bridge can reveal two previously separate records.
            for duplicate in matches[1:]:
                aliases |= identifiers(duplicate)
                merged.remove(duplicate)
            for key, value in paper.items():
                if key not in {"id", "aliases", "origins"} and value not in (None, "", []):
                    # Do not replace a pinned arXiv version with an unversioned search record.
                    if key == "arxiv_id" and re.search(r"v\d+$", target.get(key, "")) and not re.search(r"v\d+$", value):
                        continue
                    target[key] = value
            target["aliases"] = sorted(identifiers(target) | aliases)
            target["origins"] = sorted(set(target.get("origins", [])) | set(paper.get("origins", [])))
        else:
            entry = dict(paper)
            entry["id"] = "p-" + digest(sorted(aliases))[:16]
            entry["aliases"] = sorted(aliases)
            merged.append(entry)
    return merged


def parse_arxiv(raw: bytes) -> tuple[list[dict], int]:
    try:
        tree = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ReviewError("Invalid arXiv XML response.") from exc
    total = int(tree.findtext("{http://a9.com/-/spec/opensearch/1.1/}totalResults", "0"))
    papers = []
    for entry in tree.findall("a:entry", ATOM):
        link = entry.findtext("a:id", "", ATOM)
        if "/errors" in link:
            raise ReviewError("arXiv rejected the query; check its search syntax.")
        aid = arxiv_id(link)
        published = entry.findtext("a:published", "", ATOM)
        papers.append({"title": normalized(entry.findtext("a:title", "", ATOM)),
                       "authors": [a.findtext("a:name", "", ATOM) for a in entry.findall("a:author", ATOM)],
                       "abstract": normalized(entry.findtext("a:summary", "", ATOM)),
                       "arxiv_id": aid, "doi": entry.findtext("x:doi", "", ATOM),
                       "year": int(published[:4]) if published else None,
                       "published": published, "updated": entry.findtext("a:updated", "", ATOM),
                       "url": f"https://arxiv.org/abs/{aid}", "pdf_url": f"https://arxiv.org/pdf/{aid}",
                       "license": "unknown", "origins": ["arxiv"], "retrieved_at": now()})
    return papers, total


def parse_scholar(raw: bytes) -> tuple[list[dict], int]:
    data = json.loads(raw)
    if not isinstance(data.get("data"), list):
        raise ReviewError("Semantic Scholar response has no paper list.")
    papers = []
    for item in data["data"]:
        ext, oa = item.get("externalIds") or {}, item.get("openAccessPdf") or {}
        papers.append({"title": item["title"], "authors": [a["name"] for a in item.get("authors", [])],
                       "abstract": item.get("abstract") or "", "year": item.get("year"),
                       "published": item.get("publicationDate") or "", "updated": "",
                       "arxiv_id": ext.get("ArXiv") or "", "doi": ext.get("DOI") or "",
                       "url": item.get("url") or "", "pdf_url": oa.get("url") or "",
                       "license": oa.get("license") or "unknown", "venue": item.get("venue") or "",
                       "citation_count": item.get("citationCount"), "origins": ["semantic_scholar"],
                       "scholar_id": item.get("paperId"), "retrieved_at": now()})
    return papers, data.get("total", 0)


def discover(root: Path, config: dict, http: HTTP) -> dict:
    search = config["search"]
    queries = active_queries(root, config)
    stamp = now().replace(":", "-") + "-" + os.urandom(3).hex()
    folder = root / "searches" / stamp
    folder.mkdir(parents=True)
    incoming, attempts = [], []
    for provider in search["providers"]:
        for query in queries:
            sorts = search["arxiv_sorts"] if provider == "arxiv" else ["relevance"]
            for sort in sorts:
                params = {"query": query, "sort": sort}
                key = digest([provider, params])[:16]
                try:
                    if provider == "arxiv":
                        arxiv_query = query if re.search(r"\b(?:all|ti|abs|au|cat|id):", query) else "all:" + query
                        url = "https://export.arxiv.org/api/query?" + urlencode({"search_query": arxiv_query,
                              "start": 0, "max_results": search["per_query"], "sortBy": sort, "sortOrder": "descending"})
                        raw = http.get(url, delay=3.1)
                        write_text(folder / f"{key}.xml", raw.decode("utf-8"))
                        papers, total = parse_arxiv(raw)
                    else:
                        url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urlencode({
                              "query": query, "limit": search["per_query"], "offset": 0,
                              "fields": "title,authors,abstract,year,url,externalIds,openAccessPdf,citationCount,publicationDate,venue"})
                        token = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
                        raw = http.get(url, headers={"x-api-key": token} if token else {}, delay=1.1)
                        write_text(folder / f"{key}.json", raw.decode("utf-8"))
                        papers, total = parse_scholar(raw)
                    incoming.extend(papers)
                    attempts.append({"provider": provider, **params, "returned": len(papers), "total": total,
                                     "truncated": total > len(papers), "raw_file": f"{key}.{'xml' if provider == 'arxiv' else 'json'}"})
                except (ReviewError, ValueError, KeyError) as exc:
                    attempts.append({"provider": provider, **params, "error": str(exc)})
    report = {"at": now(), "queries": queries, "attempts": attempts,
              "note": "Bounded discovery, not an exhaustive systematic search. Inspect truncation and provider failures."}
    write_json(folder / "report.json", report)
    if not any("error" not in row for row in attempts):
        raise ReviewError(f"All searches failed or no providers enabled. Inspect {folder / 'report.json'}; manual import remains available.")
    previous = read_json(root / "candidates.json", [])
    merged = merge_papers(previous, incoming)
    write_json(root / "candidates.json", merged)
    old = {p["id"]: p for p in previous}
    screened = read_json(root / "selection.json", {}).get("candidate_fingerprints", {})
    pending = [p["id"] for p in merged if screened.get(p["id"]) != paper_fingerprint(p)]
    delta = {"at": now(), "search_report": str(folder.relative_to(root) / "report.json"),
             "new": [p["id"] for p in merged if p["id"] not in old],
             "changed": [p["id"] for p in merged if p["id"] in old and
                         any(p.get(k) != old[p["id"]].get(k) for k in ("abstract", "arxiv_id", "updated", "pdf_url"))],
             "provider_errors": [row for row in attempts if "error" in row],
             "pending_screening": pending, "requires_screening": bool(pending),
             "note": "New/changed compare against the previous search; pending_screening persists until screening. Search hits still require relevance assessment."}
    write_json(root / "discovery_delta.json", delta)
    return delta


def import_papers(root: Path, path: Path):
    data = read_json(path)
    if not isinstance(data, list):
        raise ReviewError("Import must be a JSON array of bibliographic records.")
    for paper in data:
        if not isinstance(paper, dict) or not isinstance(paper.get("title"), str) or not paper["title"].strip():
            raise ReviewError("Each imported paper requires a nonempty title.")
        if not paper.get("url") and not paper.get("local_path"):
            raise ReviewError("Each imported paper needs a source URL or an explicit local_path.")
        if paper.get("arxiv_id"):
            paper["arxiv_id"] = arxiv_id(paper["arxiv_id"])
        if paper.get("local_path"):
            local = (path.parent / paper["local_path"]).resolve()
            if not local.is_file() or local.suffix.lower() not in {".pdf", ".txt", ".md"}:
                raise ReviewError("local_path must point to an existing .pdf, .txt or .md file.")
            paper["local_path"] = str(local)
        if paper.get("year") is not None and type(paper["year"]) is not int:
            raise ReviewError("Imported year must be an integer or null.")
        if not isinstance(paper.get("authors", []), list) or not all(isinstance(a, str) for a in paper.get("authors", [])):
            raise ReviewError("Imported authors must be an array of names.")
        paper["origins"] = sorted(set(paper.get("origins", [])) | {"manual"})
        paper.setdefault("license", "unknown")
        paper["retrieved_at"] = now()
    merged = merge_papers(read_json(root / "candidates.json", []), data)
    write_json(root / "candidates.json", merged)
    return {"imported": len(data), "candidates": len(merged)}
