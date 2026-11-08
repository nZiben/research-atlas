"""Offline mechanical checks. Technical fixtures are NOT papers or model-generated reviews."""
from __future__ import annotations

import argparse
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from review_forge.cli import initialize, main, topics
from review_forge.config import load_config, load_env, validate_model
from review_forge.documents import chunk_document, ingest_one
from review_forge.evidence import SECTION_KEYS, validate_claims, validate_statement, validate_synthesis
from review_forge.export import load_snapshot, md, web_link
from review_forge.http import HTTP, NoRedirect
from review_forge.model import Model
from review_forge.pipeline import current_document, selection_signature
from review_forge.sources import arxiv_id, import_papers, merge_papers, paper_fingerprint
from review_forge.util import ReviewError, digest, locked, normalized, read_json, safe_id, slug, write_json, write_text


class Invariants(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        # A regression must never make a live network/model call from this suite.
        self.network = patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Network is forbidden in offline tests"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def project(self):
        root = self.base / "topic"
        initialize(argparse.Namespace(topic="Technical input validation", preset=None, dir=str(root)))
        return root, load_config(root)

    def claim_fixture(self):
        return {"text": "Technical validator fixture, not a scientific claim.", "kind": "interpretation",
                "conditions": [], "limitations": [], "concepts": ["technical fixture"],
                "evidence": [{"segment_id": "text-b0001", "quote": "Alpha beta gamma"}]}

    def test_all_ten_presets_initialize_offline(self):
        self.assertEqual(len(topics()), 10)
        for entry in topics():
            root = self.base / entry["id"]
            initialize(argparse.Namespace(topic=None, preset=entry["id"], dir=str(root)))
            cfg = load_config(root)
            self.assertEqual(cfg["review"]["category"], entry["id"])
            self.assertTrue(cfg["search"]["queries"])
            self.assertFalse((root / "candidates.json").exists())

    def test_init_preserves_existing_files(self):
        root, _ = self.project()
        before = (root / "review.toml").read_bytes()
        with self.assertRaises(ReviewError):
            initialize(argparse.Namespace(topic="different", preset=None, dir=str(root)))
        self.assertEqual(before, (root / "review.toml").read_bytes())

    def test_plan_requires_no_credentials_or_network(self):
        root, _ = self.project()
        self.assertEqual(main(["plan", "--project", str(root)]), 0)
        self.assertFalse((root / "runs").exists())

    def test_missing_model_never_produces_success(self):
        root, _ = self.project()
        self.assertEqual(main(["run", "--project", str(root)]), 2)
        self.assertFalse((root / "latest.json").exists())
        log = read_json(next((root / "runs").glob("*.json")))
        self.assertEqual(log["status"], "failed")
        self.assertEqual(log["model_calls"], 0)

    def test_invalid_state_cannot_export(self):
        root, _ = self.project()
        self.assertEqual(main(["export", "--project", str(root)]), 2)
        self.assertFalse((root / "latest.json").exists())
        self.assertFalse((root / "exports").exists())

    def test_environment_no_expansion_and_shell_precedence(self):
        path = self.base / "env"
        write_text(path, 'RF_TEST_VALUE="$(never_execute)"\n')
        with patch.dict(os.environ, {}, clear=True):
            load_env(path)
            self.assertEqual(os.environ["RF_TEST_VALUE"], "$(never_execute)")
            os.environ["RF_TEST_VALUE"] = "shell-wins"
            load_env(path)
            self.assertEqual(os.environ["RF_TEST_VALUE"], "shell-wins")

    def test_secrets_in_endpoint_rejected(self):
        model = {"name": "technical-test", "endpoint": "https://user:private@example.org/v1/chat/completions", "require_api_key": False}
        with self.assertRaises(ReviewError) as failure:
            validate_model(model)
        self.assertNotIn("private", str(failure.exception))

    def test_model_input_limit_fails_before_network(self):
        root, cfg = self.project()
        cfg["limits"]["max_input_chars"] = 5
        with self.assertRaises(ReviewError):
            Model(root, cfg).ask("unit-input", "technical instruction", {}, lambda value: None)
        self.assertFalse((root / "model_requests").exists())

    def test_model_budget_fails_before_network(self):
        root, cfg = self.project()
        cfg["model"].update({"endpoint": "http://127.0.0.1:9999/v1/chat/completions", "name": "unit-test-only", "require_api_key": False})
        cfg["limits"]["max_model_calls"] = 0
        with self.assertRaisesRegex(ReviewError, "budget"):
            Model(root, cfg).ask("unit-budget", "technical instruction", {}, lambda value: None)

    def test_model_redirect_never_forwards_key(self):
        with self.assertRaises(ReviewError):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.example/")

    def test_identifiers_do_not_allow_path_traversal(self):
        with self.assertRaises(ReviewError):
            safe_id("../../secrets")
        self.assertTrue(slug("Русская тема").startswith("topic-"))
        self.assertNotEqual(slug("Русская тема"), slug("Другая тема"))

    def test_arxiv_identifier_preserves_version(self):
        self.assertEqual(arxiv_id("https://arxiv.org/pdf/2201.02177v1.pdf"), "2201.02177v1")
        self.assertEqual(arxiv_id("math/0309136v1"), "math/0309136v1")
        with self.assertRaises(ReviewError):
            arxiv_id("../../file")

    def test_dedup_keeps_existing_id(self):
        # Structural metadata fixtures, not bibliographic claims about existing works.
        first = merge_papers([], [{"title": "Technical merge fixture", "arxiv_id": "2201.02177v1"}])
        merged = merge_papers(first, [{"title": "Technical merge fixture", "arxiv_id": "2201.02177", "doi": "fixture-only"}])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["id"], first[0]["id"])
        self.assertEqual(merged[0]["arxiv_id"], "2201.02177v1")

    def test_fingerprint_ignores_retrieval_time_but_not_source_change(self):
        a = {"title": "Technical hash fixture", "retrieved_at": "earlier", "pdf_url": "https://example.org/a.pdf"}
        b = {**a, "retrieved_at": "later"}
        self.assertEqual(paper_fingerprint(a), paper_fingerprint(b))
        b["pdf_url"] = "https://example.org/b.pdf"
        self.assertNotEqual(paper_fingerprint(a), paper_fingerprint(b))

    def test_quote_normalization_preserves_words_and_hedges(self):
        claim = self.claim_fixture()
        validate_claims({"claims": [claim]}, [{"id": "text-b0001", "text": "Alpha  beta\ngamma delta"}], {"claims_per_chunk": 1, "quote_max_words": 5})
        claim["evidence"][0]["quote"] = "Alpha proves gamma"
        with self.assertRaises(ReviewError):
            validate_claims({"claims": [claim]}, [{"id": "text-b0001", "text": "Alpha beta gamma"}], {"claims_per_chunk": 1, "quote_max_words": 5})

    def test_unknown_quote_segment_rejected(self):
        with self.assertRaises(ReviewError):
            validate_claims({"claims": [self.claim_fixture()]}, [{"id": "other", "text": "Alpha beta gamma"}], {"claims_per_chunk": 1, "quote_max_words": 5})

    def test_claim_without_evidence_rejected(self):
        claim = self.claim_fixture()
        claim["evidence"] = []
        with self.assertRaises(ReviewError):
            validate_claims({"claims": [claim]}, [], {"claims_per_chunk": 1, "quote_max_words": 5})

    def test_scientific_statement_requires_known_reference(self):
        with self.assertRaises(ReviewError):
            validate_statement({"text": "Technical fixture", "claim_ids": [], "kind": "evidence"}, {"c-fixture"})
        with self.assertRaises(ReviewError):
            validate_statement({"text": "Technical fixture", "claim_ids": ["c-unknown"], "kind": "evidence"}, {"c-fixture"})
        validate_statement({"text": "Editorial proposal fixture", "claim_ids": [], "kind": "editorial"}, set(), editorial=True)

    def test_prerequisite_cycle_rejected(self):
        # Schema-only data, never written or exported as a review.
        item = {"text": "Technical schema fixture", "claim_ids": ["c-fixture"], "kind": "evidence"}
        data = {"title": "Schema fixture only", "sections": [{"key": key, "title": key, "items": [item]} for key in SECTION_KEYS],
                "concepts": [{"id": key, "name": key, "aliases": [], "definition": item, "statements": [], "disagreements": []} for key in ("alpha", "beta")],
                "relations": [{"source": "alpha", "target": "beta", "kind": "prerequisite", "statement": item}]}
        validate_synthesis(data, {"c-fixture"})
        reserved = copy.deepcopy(data)
        reserved["concepts"][0]["id"] = "index"
        with self.assertRaises(ReviewError):
            validate_synthesis(reserved, {"c-fixture"})
        data["relations"].append({"source": "beta", "target": "alpha", "kind": "prerequisite", "statement": item})
        with self.assertRaises(ReviewError):
            validate_synthesis(data, {"c-fixture"})

    def test_local_ingestion_preserves_all_text_and_invalidates_on_change(self):
        root, cfg = self.project()
        cfg["documents"].update({"chunk_chars": 1000, "min_fulltext_chars": 100})
        source = self.base / "technical-input.txt"
        original = "Technical unit input only. " * 120
        write_text(source, original)
        paper = {"id": "p-unit-input", "title": "Local ingestion unit fixture", "local_path": str(source)}
        document = ingest_one(root, paper, cfg, HTTP())
        self.assertEqual("".join(s["text"] for s in document["segments"]), normalized(original))
        self.assertTrue(all(len(s["text"]) <= 1000 for s in document["segments"]))
        self.assertEqual(sum(len(c) for c in chunk_document(document, 1000)), len(document["segments"]))
        current_document(root, paper, cfg)
        write_text(source, original + "Changed technical input.")
        with self.assertRaises(ReviewError):
            current_document(root, paper, cfg)
        updated = ingest_one(root, paper, cfg, HTTP())
        self.assertEqual((root / "corpus" / paper["id"] / document["source_file"]).read_text(), original)
        self.assertNotEqual(document["source_file"], updated["source_file"])
        self.assertEqual(len(list((root / "corpus" / paper["id"] / "sources").iterdir())), 2)

    def test_processing_config_change_invalidates_document(self):
        root, cfg = self.project()
        source = self.base / "input.txt"
        write_text(source, "Technical unit input only. " * 60)
        paper = {"id": "p-unit-input", "title": "Technical input", "local_path": str(source)}
        ingest_one(root, paper, cfg, HTTP())
        cfg["documents"]["chunk_chars"] += 1
        with self.assertRaises(ReviewError):
            current_document(root, paper, cfg)

    def test_selection_invalidated_by_source_update(self):
        root, cfg = self.project()
        write_json(root / "candidates.json", [{"id": "p-unit-input", "title": "Technical fixture", "pdf_url": "https://example.org/a.pdf"}])
        before = selection_signature(root, cfg)
        records = read_json(root / "candidates.json")
        records[0]["pdf_url"] = "https://example.org/b.pdf"
        write_json(root / "candidates.json", records)
        self.assertNotEqual(before, selection_signature(root, cfg))

    def test_import_missing_real_source_rejected(self):
        root, _ = self.project()
        path = self.base / "invalid-import.json"
        write_json(path, [{"title": "Technical validation fixture"}])
        with self.assertRaises(ReviewError):
            import_papers(root, path)

    def test_snapshot_tampering_blocks_export(self):
        write_json(self.base / "snapshot.json", {"schema_version": 1, "integrity_sha256": "wrong"})
        with self.assertRaises(ReviewError):
            load_snapshot(self.base)

    def test_markup_and_unsafe_links_are_not_interpreted(self):
        self.assertNotEqual(md("[link](javascript:alert(1))"), "[link](javascript:alert(1))")
        self.assertEqual(web_link("javascript:alert(1)"), "")
        self.assertEqual(web_link("https://user:secret@example.org"), "")
        self.assertEqual(web_link("https://example.org/a b.pdf"), "https://example.org/a%20b.pdf")

    def test_concurrent_writers_block(self):
        with locked(self.base):
            with self.assertRaises(ReviewError):
                with locked(self.base):
                    self.fail("Second writer acquired the lock")


if __name__ == "__main__":
    unittest.main()
