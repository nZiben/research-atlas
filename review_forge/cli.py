from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

from . import __version__, pipeline
from .config import load_config, load_env, validate_model
from .export import export_snapshot, load_snapshot
from .http import HTTP
from .model import Model
from .sources import active_queries, discover, import_papers
from .util import ReviewError, digest, locked, now, read_json, runtime_inventory, slug, write_json, write_text


def topics() -> list[dict]:
    return json.loads(files("review_forge").joinpath("topics.json").read_text(encoding="utf-8"))


def initialize(args) -> dict:
    preset = next((t for t in topics() if t["id"] == args.preset), None)
    if args.preset and not preset:
        raise ReviewError("Unknown preset. Run topics to see the available IDs.")
    topic = args.topic or (preset["starter_topic"] if preset else "")
    if not topic.strip():
        raise ReviewError("Provide --topic or --preset.")
    root = Path(args.dir or Path("library") / slug(topic)).resolve()
    if root.exists() and any(root.iterdir()):
        raise ReviewError("Destination is not empty; init never overwrites a project.")
    template = files("review_forge").joinpath("default.toml").read_text(encoding="utf-8")
    template = template.replace('topic = "REPLACE_WITH_TOPIC"', "topic = " + json.dumps(topic, ensure_ascii=False))
    template = template.replace("year_to = 2026", f"year_to = {datetime.now(timezone.utc).year}")
    if preset:
        template = template.replace("language = \"ru\"", 'language = "ru"\ncategory = ' + json.dumps(preset["id"]))
        template = template.replace("queries = []", "queries = " + json.dumps(preset["queries"], ensure_ascii=False))
        template = template.replace('questions = ["Какие основные подходы существуют, чем подтверждаются и где перестают работать?"]',
                                    "questions = " + json.dumps(preset["questions"], ensure_ascii=False))
    write_text(root / "review.toml", template)
    write_json(root / "decisions.json", {})
    return {"project": str(root), "topic": topic, "next": "Edit review.toml, then run plan. No search or model requests have been made."}


def plan(root: Path, config: dict) -> dict:
    candidates = read_json(root / "candidates.json", [])
    selected = read_json(root / "selection.json", {})
    known_chunks = 0
    for paper in candidates:
        doc = read_json(root / "corpus" / paper["id"] / "document.json")
        if doc:
            known_chunks += len(pipeline.chunk_document(doc, config["documents"]["chunk_chars"]))
    return {"topic": config["review"]["topic"], "questions": config["review"]["questions"],
            "year_range": [config["review"]["year_from"], config["review"]["year_to"]],
            "queries": active_queries(root, config),
            "providers": config["search"]["providers"], "max_candidates": config["search"]["max_candidates"],
            "max_papers": config["search"]["max_papers"], "model_configured": bool(config["model"].get("endpoint") and config["model"].get("name")),
            "candidates_present": len(candidates), "selected_present": sum(r["decision"] in {"core", "external"} for r in selected.get("decisions", [])),
            "known_document_chunks": known_chunks, "max_model_calls_per_invocation": config["limits"]["max_model_calls"],
            "max_output_tokens_per_call": config["model"]["max_output_tokens"],
            "abstract_only_allowed": config["documents"]["allow_abstract_only"],
            "network_requests_made": 0,
            "cost_note": "No currency estimate: model tariffs and actual document lengths are not configured. Each chunk, paper selection, screening batch, synthesis and audit may use a separate call. A call cap is not a monetary budget."}


def snapshot_folder(root: Path, name: str | None) -> Path:
    name = name or read_json(root / "latest.json", {}).get("snapshot_id")
    if not name or Path(name).name != name or name in {".", ".."} or "\\" in name:
        raise ReviewError("No valid snapshot ID. Create a snapshot or pass its directory name.")
    return root / "snapshots" / name


def compare(root: Path, before: str, after: str | None) -> dict:
    first, second = load_snapshot(snapshot_folder(root, before)), load_snapshot(snapshot_folder(root, after))
    left = {c["id"]: c for c in first["synthesis"]["concepts"]}
    right = {c["id"]: c for c in second["synthesis"]["concepts"]}
    versions = []
    for pid in first["documents"].keys() & second["documents"].keys():
        a, b = first["documents"][pid], second["documents"][pid]
        if a["segments_sha256"] != b["segments_sha256"]:
            versions.append({"paper_id": pid, "before_arxiv": a["resolved_arxiv_id"], "after_arxiv": b["resolved_arxiv_id"],
                             "before_text_hash": a["segments_sha256"], "after_text_hash": b["segments_sha256"]})
    return {"before": first["snapshot_id"], "after": second["snapshot_id"],
            "concepts_added": sorted(right.keys() - left.keys()), "concepts_removed": sorted(left.keys() - right.keys()),
            "concepts_changed": sorted(k for k in left.keys() & right.keys() if digest(left[k]) != digest(right[k])),
            "sources_changed": versions, "recorded_changes": second["changes"],
            "note": "ID/content differences are structural changes, not an automatic assessment of scientific importance."}


def catalog(library: Path) -> dict:
    entries = []
    if library.exists():
        for root in sorted(library.iterdir()):
            if root.is_dir() and (root / "review.toml").is_file():
                cfg = load_config(root)
                latest = read_json(root / "latest.json", {})
                export_dir = root / "exports" / latest.get("snapshot_id", "__missing__")
                entries.append({"topic": cfg["review"]["topic"], "category": cfg["review"].get("category"),
                                "project_dir": str(root.resolve()), "latest_snapshot": latest.get("snapshot_id"),
                                "status": latest.get("status", "not_generated"),
                                "export_dir": str(export_dir.resolve()) if export_dir.exists() else None})
    value = {"schema_version": 1, "at": now(), "topics": entries}
    write_json(library / "catalog.json", value)
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Local evidence-linked conceptual reviews. No model requests without an explicit generation command.")
    result.add_argument("--version", action="version", version=__version__)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("topics", help="Show 10 research categories and narrow starter topics (offline)")
    init = commands.add_parser("init", help="Create topic config only (offline)")
    init.add_argument("--topic")
    init.add_argument("--preset")
    init.add_argument("--dir")
    cat = commands.add_parser("catalog", help="Write a local multi-topic index for a future UI (offline)")
    cat.add_argument("--library", default="library")
    serve = commands.add_parser("serve", help="Open the local ten-topic research library")
    serve.add_argument("--library", type=Path, default=Path("library"))
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", action="store_true", help="Open the library in your browser")
    init_library = commands.add_parser("init-library", help="Create private configurations for all ten topics, without requests")
    init_library.add_argument("--library", type=Path, default=Path("library"))
    build = commands.add_parser("build-site", help="Build a static read-only site from editorially released snapshots")
    build.add_argument("--library", type=Path, default=Path("library"))
    build.add_argument("--destination", type=Path, default=Path("site"))
    publish = commands.add_parser("release", help="Select a reviewed local snapshot for the next static site build; no upload")
    publish.add_argument("--library", type=Path, default=Path("library"))
    publish.add_argument("--topic", required=True)
    publish.add_argument("--snapshot", required=True)
    publish.add_argument("--note", required=True)
    publish.add_argument("--reviewed", action="store_true")
    for name in ("maintain", "watch"):
        maintenance = commands.add_parser(name, help="Private owner-side discovery and optional draft updates; never publication")
        maintenance.add_argument("--library", type=Path, default=Path("library"))
        maintenance.add_argument("--topic", help="Optional topic ID; defaults to all ten")
        maintenance.add_argument("--generate", action="store_true", help="Allow paid model calls to screen and prepare changed drafts")
        if name == "watch":
            maintenance.add_argument("--interval-hours", type=float, default=168)
    descriptions = {"plan": "Inspect scope and limits without network requests", "status": "Inspect local artifacts",
                    "prepare": "Use the model to propose English search queries from the topic",
                    "discover": "Search scholarly APIs; no model calls", "check-updates": "Refresh discovery and report unreviewed candidates; no model calls",
                    "import": "Import supplied bibliography/local sources", "decide": "Record a human screening override",
                    "screen": "Model-assisted screening against explicit criteria", "ingest": "Download/read selected full texts; no model calls",
                    "extract": "Extract and check source-anchored claims using the model", "synthesize": "Generate concept cards and overview from checked claims",
                    "audit": "Model critique of current synthesis", "validate": "Offline structural/evidence checks",
                    "snapshot": "Freeze current valid evidence and draft", "export": "Write Markdown/HTML/JSON/BibTeX from an immutable snapshot",
                    "compare": "Compare two local snapshots", "run": "Explicit full pipeline: discovery through draft export"}
    for name, description in descriptions.items():
        cmd = commands.add_parser(name, help=description)
        cmd.add_argument("--project", required=True, type=Path)
        cmd.add_argument("--env", type=Path, help="Optional local dotenv file; existing shell variables take precedence")
        if name == "import":
            cmd.add_argument("path", type=Path)
        if name == "decide":
            cmd.add_argument("--paper-id", required=True)
            cmd.add_argument("--decision", choices=["core", "external", "exclude"], required=True)
            cmd.add_argument("--reason", required=True)
            cmd.add_argument("--priority", type=int, default=100)
        if name in {"ingest", "run"}:
            cmd.add_argument("--refresh-sources", action="store_true", help="Refetch exact source versions instead of reusing document cache; unversioned arXiv IDs are resolved again")
        if name == "run":
            cmd.add_argument("--resume", action="store_true", help="Skip discovery; resume from existing candidates and cached model calls")
            cmd.add_argument("--skip-audit", action="store_true", help="Omit the optional model critique; output remains a draft")
        if name == "export":
            cmd.add_argument("--snapshot", help="Existing snapshot ID; otherwise freeze current state first")
            cmd.add_argument("--destination", type=Path, help="Must be absent or empty")
        if name == "compare":
            cmd.add_argument("--before", required=True)
            cmd.add_argument("--after", help="Defaults to latest snapshot")
    return result


def execute(args, root: Path, config: dict, model: Model):
    name = args.command
    if name == "plan":
        return plan(root, config)
    if name == "prepare":
        return pipeline.prepare(root, config, model)
    if name == "status":
        return {"project": str(root), "latest": read_json(root / "latest.json"),
                "quality": read_json(root / "quality.json"), "candidates": len(read_json(root / "candidates.json", [])),
                "selection": read_json(root / "selection.json"), "ingestion": read_json(root / "ingestion.json")}
    if name in {"discover", "check-updates"}:
        return discover(root, config, HTTP(config["limits"]["timeout_seconds"]))
    if name == "import":
        return import_papers(root, args.path.resolve())
    if name == "decide":
        known = {p["id"] for p in read_json(root / "candidates.json", [])}
        if args.paper_id not in known or not 0 <= args.priority <= 100 or not args.reason.strip():
            raise ReviewError("Unknown paper ID, invalid priority, or empty reason.")
        decisions = read_json(root / "decisions.json", {})
        decisions[args.paper_id] = {"decision": args.decision, "reason": args.reason, "priority": args.priority}
        write_json(root / "decisions.json", decisions)
        return {"saved": args.paper_id, "next": "Run screen to apply the changed criteria/override."}
    if name == "screen":
        return pipeline.screen(root, config, model)
    if name == "ingest":
        return pipeline.ingest(root, config, args.refresh_sources)
    if name == "extract":
        return pipeline.extract(root, config, model)
    if name == "synthesize":
        return pipeline.synthesize(root, config, model)
    if name == "audit":
        return pipeline.audit(root, config, model)
    if name == "validate":
        report = pipeline.quality(root, config)
        if report["hard_errors"]:
            raise ReviewError("Validation blocked: " + "; ".join(report["hard_errors"]))
        return report
    if name == "snapshot":
        return {"snapshot": str(pipeline.snapshot(root, config))}
    if name == "compare":
        return compare(root, args.before, args.after)
    if name == "export":
        folder = snapshot_folder(root, args.snapshot) if args.snapshot else pipeline.snapshot(root, config)
        dest = args.destination.resolve() if args.destination else root / "exports" / folder.name
        return {"export": str(export_snapshot(folder, dest))}
    if name == "run":
        validate_model(config["model"])
        if "synthesis_model" in config:
            validate_model(config["synthesis_model"])
        if not args.resume:
            if not config["search"]["queries"]:
                print("[prepare] Proposing search queries", file=sys.stderr)
                pipeline.prepare(root, config, model)
            print("[discover] Searching scholarly APIs", file=sys.stderr)
            discover(root, config, HTTP(config["limits"]["timeout_seconds"]))
        for label, action in [("screen", lambda: pipeline.screen(root, config, model)),
                              ("ingest", lambda: pipeline.ingest(root, config, args.refresh_sources)),
                              ("extract", lambda: pipeline.extract(root, config, model)),
                              ("synthesize", lambda: pipeline.synthesize(root, config, model))]:
            print(f"[{label}]", file=sys.stderr)
            action()
        if not args.skip_audit:
            print("[audit]", file=sys.stderr)
            pipeline.audit(root, config, model)
        folder = pipeline.snapshot(root, config)
        destination = export_snapshot(folder, root / "exports" / folder.name)
        return {"snapshot": folder.name, "export": str(destination), "status": "draft", "human_review_required": True}
    raise ReviewError("Unknown command.")


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "topics":
            value = topics()
        elif args.command == "init":
            value = initialize(args)
        elif args.command == "catalog":
            value = catalog(Path(args.library).resolve())
        elif args.command == "serve":
            from .server import serve
            serve(args.library.resolve(), args.port, args.open)
            return 0
        elif args.command == "init-library":
            roots = []
            for topic in topics():
                root = args.library.resolve() / topic["id"]
                if not root.exists():
                    initialize(argparse.Namespace(topic=None, preset=topic["id"], dir=str(root)))
                roots.append(str(root))
            value = {"projects": roots, "network_requests": 0}
        elif args.command == "build-site":
            from .publishing import build_site
            value = {"site": str(build_site(args.library, args.destination)), "read_only": True}
        elif args.command == "release":
            from .publishing import release
            value = release(args.library.resolve(), args.topic, args.snapshot, args.note, args.reviewed)
        elif args.command in {"maintain", "watch"}:
            from .maintenance import maintain, watch
            if args.command == "watch":
                watch(args.library.resolve(), args.generate, args.interval_hours, args.topic)
                return 0
            value = maintain(args.library.resolve(), args.generate, args.topic)
        else:
            root = args.project.resolve()
            config = load_config(root)
            load_env(args.env.resolve() if args.env else root / ".env")
            if not args.env and root != Path.cwd():
                load_env(Path.cwd() / ".env")
            model = Model(root, config)
            with locked(root):
                record = {"at": now(), "command": args.command, "version": __version__, "runtime": runtime_inventory(), "status": "running"}
                try:
                    value = execute(args, root, config, model)
                    record["status"] = "completed"
                except BaseException as exc:
                    record["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
                    raise
                finally:
                    # Environment values, authorization headers and API response bodies never enter this log.
                    if args.command not in {"plan", "status"}:
                        record["model_calls"] = model.calls
                        record["requests"] = model.records
                        write_json(root / "runs" / (record["at"].replace(":", "-") + "-" + os.urandom(3).hex() + ".json"), record)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 2 if isinstance(value, dict) and value.get("status") == "failed" else 0
    except ReviewError as exc:
        print("Error: " + str(exc), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"Error: local configuration/artifact could not be read ({type(exc).__name__}). Inspect the file format and permissions; no success recorded.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted. Completed source/model chunks are retained; use run --resume after checking provider usage.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
