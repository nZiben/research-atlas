"""Only explicitly released snapshots become public static files."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import date
from pathlib import Path

from .cli import topics
from .editorial import load_editorial
from .export import bibliography, load_snapshot, write_html
from .util import ReviewError, digest, locked, now, read_json, write_json, write_text

WEB = Path(__file__).parent / "web"


def load_bibliography(path: Path) -> dict:
    data = read_json(path, {"schema_version": 1, "checked_at": None, "topics": {}})
    if data.get("schema_version") != 1 or not isinstance(data.get("topics"), dict):
        raise ReviewError("Invalid public bibliography schema.")
    if set(data["topics"]) - {topic["id"] for topic in topics()}:
        raise ReviewError("Unknown topic in public bibliography.")
    for rows in data["topics"].values():
        if not isinstance(rows, list):
            raise ReviewError("Bibliography topics must contain arrays of records.")
        seen = set()
        for paper in rows:
            if (not isinstance(paper, dict) or not isinstance(paper.get("title"), str) or not paper["title"].strip()
                    or not isinstance(paper.get("authors"), list) or not paper["authors"]
                    or not all(isinstance(author, str) and author.strip() for author in paper["authors"])
                    or not re.fullmatch(r"arxiv-[0-9-]+", paper.get("id", ""))
                    or not re.fullmatch(r"\d{4}\.\d{4,5}v\d+", paper.get("arxiv_id", ""))
                    or paper.get("url") != "https://arxiv.org/abs/" + paper["arxiv_id"]
                    or paper.get("coverage") != "metadata_only" or paper.get("verification") != "arxiv_metadata"):
                raise ReviewError("Public reading list requires verified arXiv metadata and metadata_only status.")
            published = date.fromisoformat(paper["published_at"])
            checked = date.fromisoformat(paper["checked_at"])
            updated = date.fromisoformat(paper["updated_at"])
            if not published <= updated <= checked or checked.isoformat() > data["checked_at"] or paper["year"] != published.year or paper["id"] in seen:
                raise ReviewError("Inconsistent dates, year or duplicate ID in public bibliography.")
            if paper.get('added_at') and not published <= date.fromisoformat(paper['added_at']) <= date.fromisoformat(data['checked_at']):
                raise ReviewError('Invalid discovery date.')
            seen.add(paper["id"])
            if "discovery_groups" in paper and (not isinstance(paper["discovery_groups"], list)
                    or set(paper["discovery_groups"]) - {"seed", "relevance", "recent", "earlier", "evaluation"}):
                raise ReviewError("Unknown discovery group in bibliography.")
    return data


def snapshot_path(library: Path, topic_id: str, snapshot_id: str) -> Path:
    if topic_id not in {topic["id"] for topic in topics()}:
        raise ReviewError("Unknown topic ID.")
    if not isinstance(snapshot_id, str) or not re.fullmatch(r"[A-Za-z0-9.+-]{1,160}", snapshot_id) or snapshot_id in {".", ".."}:
        raise ReviewError("Invalid snapshot ID.")
    base = library / topic_id / "snapshots"
    target = base / snapshot_id
    if not target.resolve().is_relative_to(base.resolve()):
        raise ReviewError("Snapshot escaped its topic directory.")
    return target


def release(library: Path, topic_id: str, snapshot_id: str, note: str, reviewed: bool) -> dict:
    if not reviewed or not note.strip():
        raise ReviewError("Release requires --reviewed and a concrete --note after checking the draft and publication rights.")
    payload = load_snapshot(snapshot_path(library, topic_id, snapshot_id))
    if payload["quality"]["hard_errors"]:
        raise ReviewError("Cannot release a snapshot with structural quality errors.")
    with locked(library / ".publishing"):
        manifest = read_json(library / "releases.json", {"schema_version": 1, "topics": {}})
        entries = manifest["topics"].setdefault(topic_id, [])
        if any(entry["snapshot_id"] == snapshot_id for entry in entries):
            raise ReviewError("This snapshot was already released; no duplicate publication record was created.")
        entry = {"snapshot_id": snapshot_id, "released_at": now(), "note": note, "editor_reviewed": True,
                 "snapshot_checksum": payload["integrity_sha256"]}
        entries.append(entry)
        write_json(library / "releases.json", manifest)
    return {"topic": topic_id, **entry, "next": "Run build-site to prepare public files. No upload or deployment has happened."}


def public_review(payload: dict, entry: dict) -> dict:
    # No config, source texts, local paths, raw API replies or search logs.
    papers = [{key: p.get(key) for key in ("id", "title", "authors", "year", "doi", "arxiv_id", "url")} for p in payload["papers"]]
    return {"schema_version": 1, "snapshot_id": payload["snapshot_id"], "at": payload["at"],
            "review": payload["review"], "papers": papers,
            "cards": [{key: card[key] for key in ("paper_id", "coverage", "claims")} for card in payload["cards"]],
            "synthesis": payload["synthesis"], "quality": {"warnings": [
                "Обзор собран с помощью модели и выпущен редактором. Проверьте первоисточники перед использованием научных выводов.",
                "Охват ограничен выбранным корпусом; отсутствие работы в обзоре не означает её отсутствия в литературе."
            ]}, "changes": payload["changes"], "publication": entry}


def build_site(library: Path, destination: Path) -> Path:
    library, destination = library.resolve(), destination.resolve()
    if destination == library or destination.is_relative_to(library) or library.is_relative_to(destination) or WEB.resolve().is_relative_to(destination) or destination.is_relative_to(WEB.resolve()):
        raise ReviewError("Public output must be a separate directory, outside private library and source assets.")
    if destination.exists() and read_json(destination / "site-manifest.json", {}).get("kind") != "review-forge-public-site":
        raise ReviewError("Output directory is not a generated site; refusing to overwrite it.")
    manifest = read_json(library / "releases.json", {"schema_version": 1, "topics": {}})
    reading = load_bibliography(library.parent / "bibliography" / "papers.json")
    if manifest.get("schema_version") != 1:
        raise ReviewError("Unsupported release manifest.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".site-", dir=destination.parent))
    backup = None
    try:
        for name in ("index.html", "app.js", "style.css", "mark.svg", "related.html"):
            shutil.copyfile(WEB / name, temporary / name)
        summaries = []
        for topic in topics():
            topic_id = topic["id"]
            entries = manifest["topics"].get(topic_id, [])
            versions, current, roles = [], None, {}
            for entry in entries:
                payload = load_snapshot(snapshot_path(library, topic_id, entry["snapshot_id"]))
                if not entry.get("editor_reviewed") or payload["integrity_sha256"] != entry["snapshot_checksum"]:
                    raise ReviewError("Released snapshot changed or has no editorial approval.")
                current = public_review(payload, entry)
                roles = {row["paper_id"]: row["decision"] for row in payload["selection"]["decisions"]}
                write_json(temporary / "data" / "reviews" / topic_id / f"{entry['snapshot_id']}.json", current)
                write_text(temporary / "exports" / topic_id / entry["snapshot_id"] / "references.bib", bibliography(current["papers"]))
                write_html(temporary / "exports" / topic_id / entry["snapshot_id"], {**payload, "quality": current["quality"]},
                           {claim["id"]: claim for card in payload["cards"] for claim in card["claims"]},
                           {paper["id"]: paper for paper in payload["papers"]}, public=True)
                versions.append({"id": entry["snapshot_id"], "released_at": entry["released_at"], "note": entry["note"]})
            # Tracked editorial publications are not model-run snapshots or private drafts.
            if not current:
                current = load_editorial(library.parent / 'reviews' / f'{topic_id}.json')
                if current:
                    base = temporary / 'exports' / topic_id / current['snapshot_id']
                    write_json(temporary / 'data' / 'reviews' / topic_id / f"{current['snapshot_id']}.json", current)
                    write_text(base / 'references.bib', bibliography(current['papers']))
                    write_html(base, {**current, 'config': {'limits': {'export_quote_words_per_paper': 25}}},
                               {c['id']: c for card in current['cards'] for c in card['claims']},
                               {p['id']: p for p in current['papers']}, public=True)
                    versions.append({'id': current['snapshot_id'], **{key: current['publication'][key] for key in ('released_at', 'note')}})
            papers = [{**paper, "decision": roles.get(paper["id"], "core"), "reason": "Источник опубликованного обзора."} for paper in current["papers"]] if current else []
            included = {re.sub(r"v\d+$", "", paper.get("arxiv_id") or "") for paper in papers}
            for paper in reading["topics"].get(topic_id, []):
                if re.sub(r"v\d+$", "", paper["arxiv_id"]) not in included:
                    fields = ("id", "title", "authors", "year", "doi", "arxiv_id", "url", "published_at", "updated_at", "checked_at", "coverage", "verification", "discovery_groups", "screening_status", "added_at")
                    papers.append({**{key: paper.get(key) for key in fields}, "decision": "reading_list",
                                   "reason": "Проверена библиографическая запись arXiv. Полный текст и научные выводы ещё не разобраны; работа не является доказательством выводов обзора."})
                else:
                    reviewed = next(p for p in papers if re.sub(r'v\d+$', '', p.get('arxiv_id') or '') == re.sub(r'v\d+$', '', paper['arxiv_id']))
                    if int(paper['arxiv_id'].split('v')[1]) > int(reviewed['arxiv_id'].split('v')[1]):
                        reviewed['available_arxiv_id'] = paper['arxiv_id']
            reading_papers = [paper for paper in papers if paper["decision"] == "reading_list"]
            years = sorted({paper["year"] for paper in reading_papers})
            coverage = {"count": len(reading_papers), "first_year": min(years) if years else None,
                        "last_year": max(years) if years else None,
                        "years": {str(year): sum(p["year"] == year for p in reading_papers) for year in years},
                        "groups": {key: sum(key in (p.get("discovery_groups") or []) for p in reading_papers)
                                   for key in ("relevance", "recent", "earlier", "evaluation")},
                        "full_text_reviewed": 0}
            searches = [row for row in reading.get("searches", []) if row["topic"] == topic_id]
            coverage.update({"searches": len(searches), "truncated_searches": sum(bool(row["truncated"]) for row in searches)})
            if papers:
                write_text(temporary / "exports" / "reading-lists" / topic_id / "references.bib", bibliography(papers))
            summary = {**topic, "topic": current["review"]["topic"] if current else topic["starter_topic"],
                       "questions": current["review"]["questions"] if current else topic["questions"],
                       "has_review": current is not None, "snapshot_id": current["snapshot_id"] if current else None,
                       "review_papers": len(current['papers']) if current else 0,
                       "papers": len(papers), "bibliography_checked_at": reading["checked_at"] if reading["topics"].get(topic_id) else None,
                       "bibliography_coverage": coverage,
                       "discovery": reading.get('last_update', {}).get('topics', {}).get(topic_id),
                       "updated_at": versions[-1]["released_at"] if versions else None}
            write_json(temporary / "data" / "topics" / f"{topic_id}.json", {"papers": papers, "versions": list(reversed(versions)), "review": current})
            summaries.append(summary)
        write_json(temporary / "data" / "library.json", {"schema_version": 1, "topics": summaries})
        search_fields = ("topic", "strategy", "label", "url", "returned", "eligible_returned", "total_matches", "truncated", "scope_excluded", "scope_excluded_ids", "response_sha256", "incremental", "complete", "window_start", "page")
        write_json(temporary / "data" / "search-method.json", {"checked_at": reading["checked_at"],
                   "method": reading.get("method"), "plan_sha256": reading.get("plan_sha256"),
                   "searches": [{key: row.get(key) for key in search_fields} for row in reading.get("searches", [])]})
        if reading.get('last_update'):
            report = reading['last_update']
            write_json(temporary / 'data' / 'discovery.json', {'checked_at': report['checked_at'], 'window_start': report['window_start'],
                'topics': {key: {field: value[field] for field in ('added', 'updated', 'added_ids', 'updated_ids')} for key, value in report['topics'].items()}})
        files = {str(p.relative_to(temporary)): digest(p.read_bytes()) for p in sorted(temporary.rglob("*")) if p.is_file()}
        write_json(temporary / "site-manifest.json", {"kind": "review-forge-public-site", "files": files})
        # These files are explicitly public; a separate static-server user must be able to read them.
        temporary.chmod(0o755)
        for path in temporary.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)
        if destination.exists():
            backup = Path(tempfile.mkdtemp(prefix=".previous-site-", dir=destination.parent))
            backup.rmdir()
            os.rename(destination, backup)
        try:
            os.rename(temporary, destination)
        except OSError:
            if backup:
                os.rename(backup, destination)
                backup = None
            raise
        if backup:
            shutil.rmtree(backup)
        return destination
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
