"""Owner-side monitoring and draft preparation. Never releases or uploads content."""
from __future__ import annotations

import time
import math
from pathlib import Path

from . import pipeline
from .cli import topics
from .config import load_config, load_env, validate_model
from .export import export_snapshot, load_snapshot
from .http import HTTP
from .model import Model
from .sources import discover, paper_fingerprint
from .util import ReviewError, digest, locked, now, read_json, write_json


def corpus_signature(papers: list[dict], decisions: list[dict], review: dict) -> str:
    roles = {row["paper_id"]: row["decision"] for row in decisions if row["decision"] in {"core", "external"}}
    return digest({"review": review, "papers": {p["id"]: [roles[p["id"]], paper_fingerprint(p)] for p in papers if p["id"] in roles}})


def prepare_update(root: Path, config: dict, model: Model, delta: dict) -> dict:
    latest = read_json(root / "latest.json", {})
    previous = load_snapshot(root / "snapshots" / latest["snapshot_id"]) if latest else None
    if delta["requires_screening"] or not previous:
        pipeline.screen(root, config, model)
    else:
        # Changed criteria/overrides may require screening even with unchanged search hits.
        try:
            pipeline.selection(root, config)
        except ReviewError:
            pipeline.screen(root, config, model)
    selected, papers = pipeline.selection(root, config)
    before = corpus_signature(previous["papers"], previous["selection"]["decisions"], previous["review"]) if previous else None
    after = corpus_signature(papers, selected["decisions"], config["review"])
    if before == after:
        return {"status": "unchanged", "reason": "Selected corpus and research questions are unchanged. No synthesis or publication was performed."}
    pipeline.ingest(root, config)
    pipeline.extract(root, config, model)
    pipeline.synthesize(root, config, model)
    pipeline.audit(root, config, model)
    folder = pipeline.snapshot(root, config)
    export_snapshot(folder, root / "exports" / folder.name)
    return {"status": "draft_ready", "snapshot_id": folder.name,
            "reason": "The selected corpus/scope changed. This is a draft proposal, not a verified assessment of scientific importance or a public release."}


def maintain(library: Path, generate=False, topic_id=None) -> dict:
    library = library.resolve()
    presets = topics()
    if topic_id and topic_id not in {topic["id"] for topic in presets}:
        raise ReviewError("Unknown topic ID.")
    if not library.is_dir():
        raise ReviewError("Private library does not exist; initialize topic configurations first.")
    load_env(library.parent / ".env")
    report = {"at": now(), "mode": "drafts" if generate else "discovery_only", "topics": [], "published": False}
    with locked(library / ".maintenance"):
        for topic in presets:
            if topic_id and topic["id"] != topic_id:
                continue
            root = library / topic["id"]
            model = None
            row = {"topic_id": topic["id"], "at": now()}
            try:
                with locked(root):
                    config = load_config(root)
                    if generate:
                        validate_model(config["model"])
                        if "synthesis_model" in config:
                            validate_model(config["synthesis_model"])
                    model = Model(root, config)
                    delta = discover(root, config, HTTP(config["limits"]["timeout_seconds"]))
                    row["new_candidates"] = len(delta["new"])
                    row["pending_screening"] = len(delta["pending_screening"])
                    row["provider_errors"] = delta["provider_errors"]
                    if generate:
                        row.update(prepare_update(root, config, model, delta))
                    else:
                        row["status"] = "candidates_pending" if delta["requires_screening"] else "no_pending_candidates"
            except (ReviewError, OSError, ValueError, KeyError, TypeError) as exc:
                row.update({"status": "failed", "error": str(exc) if isinstance(exc, ReviewError) else f"Local artifact error: {type(exc).__name__}"})
            finally:
                row["model_calls"] = model.calls if model else 0
                if model:
                    write_json(root / "runs" / ("maintenance-" + now().replace(":", "-") + ".json"),
                               {"at": now(), "status": row.get("status", "interrupted"), "requests": model.records, "model_calls": model.calls})
            report["topics"].append(row)
            write_json(library / "maintenance.json", report)
    report["status"] = "failed" if any(row["status"] == "failed" for row in report["topics"]) else "completed"
    write_json(library / "maintenance.json", report)
    return report


def watch(library: Path, generate=False, interval_hours=168.0, topic_id=None):
    if not math.isfinite(interval_hours) or interval_hours < 1:
        raise ReviewError("Monitoring interval must be at least one hour.")
    if not library.is_dir():
        raise ReviewError("Initialize the private library before starting monitoring.")
    with locked(library / ".watch"):
        try:
            while True:
                report = maintain(library, generate, topic_id)
                failed = sum(row["status"] == "failed" for row in report["topics"])
                print(f"{report['at']}: {len(report['topics'])} topics attempted; {failed} failed; no automatic publication. Next check in {interval_hours:g} hours.", flush=True)
                deadline = time.monotonic() + interval_hours * 3600
                while time.monotonic() < deadline:
                    time.sleep(min(30, max(0, deadline - time.monotonic())))
        except KeyboardInterrupt:
            print("Private monitoring stopped. Preserved drafts and public releases are unchanged.", flush=True)
