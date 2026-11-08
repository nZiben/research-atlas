from __future__ import annotations

import os
from pathlib import Path

from . import prompts
from .config import config_hash
from .documents import chunk_document, ingest_one, processing_signature, source_signature
from .evidence import need, validate_audit, validate_claims, validate_pick, validate_screen, validate_synthesis
from .http import HTTP
from .model import Model, PROMPT_VERSION
from .sources import paper_fingerprint
from .util import ReviewError, digest, now, read_json, runtime_inventory, write_json


def metadata(paper: dict) -> dict:
    return {k: paper.get(k) for k in ("id", "title", "authors", "year", "abstract", "arxiv_id", "doi", "url", "local_path")}


def prepare(root: Path, config: dict, model: Model) -> dict:
    def validate(result):
        from .evidence import obj, strings, text
        obj(result, ["queries", "scope_note"])
        strings(result["queries"])
        need(3 <= len(result["queries"]) <= 6 and all(len(q) <= 300 for q in result["queries"]), "Expected 3–6 bounded search queries.")
        text(result["scope_note"])
    result = model.ask("query-plan", prompts.QUERIES, {"review": config["review"], "max_papers": config["search"]["max_papers"]}, validate)
    record = {"at": now(), "review_hash": digest(config["review"]), "content": result}
    write_json(root / "query_plan.json", record)
    return record


def selection_signature(root: Path, config: dict) -> str:
    return digest({"review": config["review"], "search": config["search"],
                   "papers": {p["id"]: paper_fingerprint(p) for p in read_json(root / "candidates.json", [])},
                   "overrides": read_json(root / "decisions.json", {}), "prompt": prompts.SCREEN,
                   "model": config["model"], "prompt_version": PROMPT_VERSION})


def selection(root: Path, config: dict) -> tuple[dict, list[dict]]:
    record = read_json(root / "selection.json")
    if not record or record["input_hash"] != selection_signature(root, config):
        raise ReviewError("Screening is missing or stale. Run screen after changing candidates, criteria, model or decisions.json.")
    papers = {p["id"]: p for p in read_json(root / "candidates.json", [])}
    return record, [papers[row["paper_id"]] for row in record["decisions"] if row["decision"] in {"core", "external"}]


def screen(root: Path, config: dict, model: Model) -> dict:
    papers = read_json(root / "candidates.json", [])
    if not papers:
        raise ReviewError("No candidates. Run discover or import first.")
    overrides = read_json(root / "decisions.json", {})
    need(isinstance(overrides, dict) and set(overrides) <= {p["id"] for p in papers}, "decisions.json contains an unknown paper ID.")
    decisions, eligible = [], []
    for paper in papers:
        pid = paper["id"]
        if pid in overrides:
            override = overrides[pid]
            row = {"paper_id": pid, **override}
            validate_screen({"decisions": [row]}, {pid})
            decisions.append({**row, "by": "human"})
        elif paper.get("year") is None or not config["review"]["year_from"] <= paper["year"] <= config["review"]["year_to"]:
            decisions.append({"paper_id": pid, "decision": "exclude", "reason": "Missing year or outside configured date range; use a manual override if justified.", "priority": 0, "by": "date_filter"})
        else:
            eligible.append(paper)
    if len(eligible) > config["search"]["max_candidates"]:
        raise ReviewError(f"{len(eligible)} candidates exceed max_candidates. Narrow queries/date range, add manual exclusions or explicitly raise the cap; none were silently dropped.")
    for start in range(0, len(eligible), 10):
        batch = eligible[start:start + 10]
        ids = {p["id"] for p in batch}
        answer = model.ask("screen", prompts.SCREEN, {"review": config["review"], "candidates": [metadata(p) for p in batch]},
                           lambda value: validate_screen(value, ids))
        decisions.extend({**row, "by": "model_abstract_screen"} for row in answer["decisions"])
    included = sorted((row for row in decisions if row["decision"] in {"core", "external"}),
                      key=lambda row: (row["by"] != "human", row["decision"] != "core", -row["priority"], row["paper_id"]))
    # Human inclusions are never silently demoted by a resource cap.
    if sum(row["by"] == "human" for row in included) > config["search"]["max_papers"]:
        raise ReviewError("Human inclusions exceed max_papers; raise the cap or change decisions.json.")
    chosen = {row["paper_id"] for row in included[:config["search"]["max_papers"]]}
    for row in decisions:
        if row["decision"] in {"core", "external"} and row["paper_id"] not in chosen:
            row["proposed_decision"] = row["decision"]
            row["decision"] = "deferred"
            row["reason"] += " [Deferred by max_papers budget, not a scientific exclusion.]"
    record = {"at": now(), "input_hash": selection_signature(root, config), "decisions": decisions,
              "candidate_fingerprints": {p["id"]: paper_fingerprint(p) for p in papers},
              "note": "Abstract-based screening is provisional. Record human corrections in decisions.json."}
    write_json(root / "selection.json", record)
    if sum(row["decision"] == "core" for row in decisions) < config["review"]["min_papers"]:
        raise ReviewError("Screening selected too few core papers. Inspect selection.json; broaden discovery or record justified manual inclusions before reading/extracting sources.")
    return record


def ingest(root: Path, config: dict, refresh=False) -> dict:
    _, papers = selection(root, config)
    need(bool(papers), "Screening selected no papers.")
    http, results = HTTP(config["limits"]["timeout_seconds"]), []
    for paper in papers:
        try:
            document = ingest_one(root, paper, config, http, refresh=refresh)
            results.append({"paper_id": paper["id"], "coverage": document["coverage"], "status": "ready"})
        except (ReviewError, OSError, UnicodeError) as exc:
            results.append({"paper_id": paper["id"], "status": "unavailable", "error": str(exc)})
    report = {"at": now(), "papers": results}
    write_json(root / "ingestion.json", report)
    if any(row["status"] != "ready" for row in results):
        raise ReviewError("Some selected sources could not be read. Inspect ingestion.json, provide local files, or record justified exclusions and screen again. No unavailable paper was silently omitted.")
    return report


def extraction_signature(document: dict, config: dict, role: str) -> str:
    return digest({"segments": document["segments_sha256"], "coverage": document["coverage"],
                   "review": config["review"], "role": role, "model": config["model"], "prompts": [prompts.EXTRACT, prompts.PICK],
                   "prompt_version": PROMPT_VERSION, "chunk_chars": config["documents"]["chunk_chars"],
                   "limits": {k: config["limits"][k] for k in ("claims_per_chunk", "quote_max_words", "synthesis_claims_per_paper")}})


def current_document(root: Path, paper: dict, config: dict) -> dict:
    doc = read_json(root / "corpus" / paper["id"] / "document.json")
    if not doc or doc["source_signature"] != source_signature(paper) or doc.get("processing_signature") != processing_signature(config):
        raise ReviewError(f"Source for {paper['id']} is missing or stale; run ingest.")
    need(doc["segments_sha256"] == digest(doc["segments"]), "Source segment checksum mismatch; ingest again.")
    need(doc["coverage"] != "abstract_only" or config["documents"]["allow_abstract_only"], "Current config forbids abstract-only sources.")
    return doc


def extract(root: Path, config: dict, model: Model) -> dict:
    selected, papers = selection(root, config)
    roles = {row["paper_id"]: row["decision"] for row in selected["decisions"]}
    counts = []
    for paper in papers:
        document = current_document(root, paper, config)
        folder = root / "corpus" / paper["id"]
        signature = extraction_signature(document, config, roles[paper["id"]])
        cached = read_json(folder / "card.json")
        if cached and cached.get("input_hash") == signature:
            counts.append({"paper_id": paper["id"], "claims": len(cached["claims"]), "cached": True})
            continue
        claims, seen = [], set()
        chunks = chunk_document(document, config["documents"]["chunk_chars"])
        for index, segments in enumerate(chunks, 1):
            answer = model.ask(f"extract:{paper['id']}:{index}", prompts.EXTRACT.replace("LIMIT", str(config["limits"]["claims_per_chunk"])),
                               {"review": config["review"], "paper": metadata(paper), "role": roles[paper["id"]],
                                "coverage": document["coverage"], "chunk": index, "chunks": len(chunks),
                                "quote_max_words": config["limits"]["quote_max_words"], "segments": segments},
                               lambda value: validate_claims(value, segments, config["limits"]))
            for claim in answer["claims"]:
                cid = "c-" + digest([paper["id"], document["segments_sha256"], claim])[:20]
                if cid not in seen:
                    seen.add(cid)
                    claims.append({"id": cid, "paper_id": paper["id"], **claim})
        if not claims:
            raise ReviewError(f"No supported relevant claims extracted from {paper['id']}. Inspect the source and screening; cached chunks are retained.")
        cap = config["limits"]["synthesis_claims_per_paper"]
        if len(claims) <= cap:
            picked = {"claim_ids": [c["id"] for c in claims], "reason": "All extracted claims fit the synthesis budget."}
        else:
            picked = model.ask(f"pick:{paper['id']}", prompts.PICK,
                               {"review": config["review"], "cap": cap, "claims": claims},
                               lambda value: validate_pick(value, {c["id"] for c in claims}, cap))
        card = {"paper_id": paper["id"], "input_hash": signature, "at": now(), "coverage": document["coverage"],
                "document_hash": document["segments_sha256"], "chunks_processed": len(chunks),
                "claims": claims, "synthesis_selection": picked, "human_reviewed": False}
        write_json(folder / "card.json", card)
        counts.append({"paper_id": paper["id"], "claims": len(claims), "cached": False})
    return {"papers": counts}


def evidence_bundle(root: Path, config: dict) -> dict:
    selected, papers = selection(root, config)
    cards, documents, claims = [], {}, []
    roles = {row["paper_id"]: row["decision"] for row in selected["decisions"]}
    core = [p for p in papers if roles[p["id"]] == "core"]
    need(len(core) >= config["review"]["min_papers"], "Too few core papers. Broaden justified discovery or explicitly lower min_papers; external context papers do not count.")
    for paper in papers:
        doc = current_document(root, paper, config)
        card = read_json(root / "corpus" / paper["id"] / "card.json")
        need(card and card["input_hash"] == extraction_signature(doc, config, roles[paper["id"]]), f"Extraction missing or stale for {paper['id']}; run extract.")
        for claim in card["claims"]:
            bare = {k: v for k, v in claim.items() if k not in {"id", "paper_id"}}
            limits = {**config["limits"], "claims_per_chunk": 1}
            validate_claims({"claims": [bare]}, doc["segments"], limits)
        cards.append(card)
        documents[paper["id"]] = doc
        ids = set(card["synthesis_selection"]["claim_ids"])
        need(ids <= {c["id"] for c in card["claims"]}, "Card selection refers to an unknown claim.")
        claims.extend({**claim, "paper_role": roles[paper["id"]], "coverage": doc["coverage"]}
                      for claim in card["claims"] if claim["id"] in ids)
    return {"review": config["review"], "papers": papers, "selection": selected, "cards": cards,
            "documents": documents, "synthesis_claims": claims}


def synthesis_input(bundle: dict) -> dict:
    return {"review": bundle["review"], "papers": [metadata(p) for p in bundle["papers"]],
            "claims": bundle["synthesis_claims"]}


def synthesis_signature(bundle: dict, config: dict) -> str:
    return digest({"input": synthesis_input(bundle), "prompt": prompts.SYNTHESIZE, "prompt_version": PROMPT_VERSION,
                   "model": config.get("synthesis_model", config["model"])})


def synthesize(root: Path, config: dict, model: Model) -> dict:
    bundle = evidence_bundle(root, config)
    ids = {c["id"] for c in bundle["synthesis_claims"]}
    value = model.ask("synthesis", prompts.SYNTHESIZE, synthesis_input(bundle),
                      lambda result: validate_synthesis(result, ids), synthesis=True)
    record = {"at": now(), "input_hash": synthesis_signature(bundle, config), "content": value}
    write_json(root / "synthesis.json", record)
    return record


def current_synthesis(root: Path, config: dict, bundle: dict) -> dict:
    record = read_json(root / "synthesis.json")
    need(record and record["input_hash"] == synthesis_signature(bundle, config), "Synthesis missing or stale; run synthesize.")
    validate_synthesis(record["content"], {c["id"] for c in bundle["synthesis_claims"]})
    return record


def audit(root: Path, config: dict, model: Model) -> dict:
    bundle = evidence_bundle(root, config)
    synthesis = current_synthesis(root, config, bundle)
    ids = {c["id"] for c in bundle["synthesis_claims"]}
    value = model.ask("audit", prompts.AUDIT, {**synthesis_input(bundle), "synthesis": synthesis["content"]},
                      lambda result: validate_audit(result, ids), synthesis=True)
    record = {"at": now(), "synthesis_hash": digest(synthesis["content"]), "content": value,
              "note": "Model critique only; not an independent human audit."}
    write_json(root / "audit.json", record)
    return record


def quality(root: Path, config: dict) -> dict:
    errors, warnings = [], ["Draft: semantic correctness, figures, derivations and discovery completeness require human review."]
    stats = {}
    try:
        bundle = evidence_bundle(root, config)
        synthesis = current_synthesis(root, config, bundle)
        stats = {"papers": len(bundle["papers"]), "concepts": len(synthesis["content"]["concepts"]),
                 "extracted_claims": sum(len(c["claims"]) for c in bundle["cards"]),
                 "synthesis_claims": len(bundle["synthesis_claims"]),
                 "abstract_only": sum(d["coverage"] == "abstract_only" for d in bundle["documents"].values())}
        if stats["abstract_only"]:
            warnings.append("Some cards use only abstracts; do not treat them as full-text evidence.")
        if stats["synthesis_claims"] < stats["extracted_claims"]:
            warnings.append("Cross-paper synthesis uses a bounded subset of claims. Full claim cards are retained; selection can omit important evidence.")
        if any(p.get("license", "unknown") == "unknown" for p in bundle["papers"]):
            warnings.append("Some source licenses are unknown. Original texts remain private; publication requires a separate rights review.")
        deferred = sum(row["decision"] == "deferred" for row in bundle["selection"]["decisions"])
        if deferred:
            warnings.append(f"{deferred} relevant papers deferred by max_papers; corpus is resource bounded.")
        delta = read_json(root / "discovery_delta.json", {})
        if delta.get("provider_errors"):
            warnings.append("At least one provider query failed; discovery coverage is incomplete. See discovery_delta.json.")
        for report_path in sorted((root / "searches").glob("*/report.json"))[-1:]:
            if any(row.get("truncated") for row in read_json(report_path)["attempts"]):
                warnings.append("The last search was truncated by per_query; unreturned papers were not screened. See the saved search report.")
        ingestion = read_json(root / "ingestion.json", {})
        current_ids = {p["id"] for p in bundle["papers"]}
        if any(row["paper_id"] in current_ids and row["status"] != "ready" for row in ingestion.get("papers", [])):
            warnings.append("The latest source retrieval attempt failed for a current paper; this draft reuses an older preserved document. Inspect ingestion.json.")
        audit_record = read_json(root / "audit.json")
        if not audit_record or audit_record.get("synthesis_hash") != digest(synthesis["content"]):
            warnings.append("No current model critique is available; run audit if desired.")
        else:
            findings = audit_record["content"]["findings"]
            warnings.append(f"Model critique findings: {len(findings)}; high severity: {sum(f['severity'] == 'high' for f in findings)}. Inspect audit.json.")
    except (ReviewError, KeyError, TypeError, ValueError) as exc:
        errors.append(str(exc))
    report = {"at": now(), "status": "blocked" if errors else "draft", "hard_errors": errors, "warnings": warnings, "stats": stats}
    write_json(root / "quality.json", report)
    return report


def snapshot(root: Path, config: dict) -> Path:
    report = quality(root, config)
    need(not report["hard_errors"], "Quality gate failed. See quality.json; no snapshot/export was created.")
    bundle = evidence_bundle(root, config)
    synthesis = current_synthesis(root, config, bundle)
    audit_record = read_json(root / "audit.json")
    if audit_record and audit_record.get("synthesis_hash") != digest(synthesis["content"]):
        audit_record = None
    fingerprint = digest({"evidence": bundle, "synthesis": synthesis, "config": config, "audit": audit_record})
    previous = read_json(root / "latest.json", {})
    name = now().replace(":", "-") + "-" + fingerprint[:10] + "-" + os.urandom(4).hex()
    folder = root / "snapshots" / name
    payload = {"schema_version": 1, "snapshot_id": name, "at": now(), "status": "draft",
               "config_hash": config_hash(config), "config": config, "quality": report, "runtime": runtime_inventory(),
               "synthesis": synthesis["content"], "audit": audit_record,
               "query_plan": read_json(root / "query_plan.json"),
               "search_reports": [{"path": str(p.relative_to(root)), "report": read_json(p)}
                                  for p in sorted((root / "searches").glob("*/report.json"))], **bundle}
    old_payload = read_json(root / "snapshots" / previous.get("snapshot_id", "__missing__") / "snapshot.json")
    if old_payload:
        need(old_payload.get("integrity_sha256") == digest({k: v for k, v in old_payload.items() if k != "integrity_sha256"}), "Previous snapshot checksum mismatch; repair its history before creating a new version.")
    old_papers = {p["id"]: p for p in (old_payload or {}).get("papers", [])}
    new_papers = {p["id"]: p for p in bundle["papers"]}
    old_claims = {c["id"] for card in (old_payload or {}).get("cards", []) for c in card["claims"]}
    new_claims = {c["id"] for card in bundle["cards"] for c in card["claims"]}
    payload["changes"] = {"previous_snapshot": previous.get("snapshot_id"),
                          "papers_added": sorted(new_papers.keys() - old_papers.keys()),
                          "papers_removed": sorted(old_papers.keys() - new_papers.keys()),
                          "claims_added": sorted(new_claims - old_claims), "claims_removed": sorted(old_claims - new_claims),
                          "note": "Changed claim IDs indicate changed extraction/source/text, not scientific progress by themselves."}
    payload["integrity_sha256"] = digest(payload)
    folder.mkdir(parents=True, exist_ok=False)
    write_json(folder / "snapshot.json", payload)
    write_json(root / "latest.json", {"snapshot_id": name, "at": payload["at"], "status": "draft"})
    return folder
