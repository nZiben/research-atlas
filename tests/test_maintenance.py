"""Budget/control tests on technical metadata, not research/model output fixtures."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from review_forge.maintenance import corpus_signature, maintain, prepare_update, watch
from review_forge.util import ReviewError, write_json


class Maintenance(unittest.TestCase):
    def test_irrelevant_candidates_do_not_change_selected_corpus(self):
        a = {"id": "p-technical-a", "title": "Technical metadata fixture", "year": 2026}
        b = {"id": "p-technical-b", "title": "Different technical fixture", "year": 2026}
        included = [{"paper_id": a["id"], "decision": "core"}]
        expanded = included + [{"paper_id": b["id"], "decision": "exclude"}]
        self.assertEqual(corpus_signature([a], included, {}), corpus_signature([a, b], expanded, {}))
        expanded[-1]["decision"] = "core"
        self.assertNotEqual(corpus_signature([a], included, {}), corpus_signature([a, b], expanded, {}))

    def test_source_version_changes_trigger_draft_work(self):
        a = {"id": "p-technical", "title": "Technical version fixture", "arxiv_id": "2201.02177v1"}
        b = {**a, "arxiv_id": "2201.02177v2"}
        rows = [{"paper_id": a["id"], "decision": "core"}]
        self.assertNotEqual(corpus_signature([a], rows, {}), corpus_signature([b], rows, {}))

    def test_unchanged_corpus_skips_expensive_stages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_json(root / "latest.json", {"snapshot_id": "technical-control-test"})
            rows = [{"paper_id": "p-technical", "decision": "core"}]
            papers = [{"id": "p-technical", "title": "Technical control metadata"}]
            before = {"papers": papers, "selection": {"decisions": rows}, "review": {"topic": "Technical control test"}}
            with patch("review_forge.maintenance.load_snapshot", return_value=before), patch("review_forge.maintenance.pipeline.selection", return_value=({"decisions": rows}, papers)), patch("review_forge.maintenance.pipeline.ingest", side_effect=AssertionError("Must not reread unchanged corpus")), patch("review_forge.maintenance.pipeline.synthesize", side_effect=AssertionError("Must not rewrite unchanged corpus")):
                result = prepare_update(root, {"review": before["review"]}, None, {"requires_screening": False})
            self.assertEqual(result["status"], "unchanged")
            self.assertFalse((root / "releases.json").exists())

    def test_missing_configuration_is_failure_not_fake_success(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch("review_forge.maintenance.discover", side_effect=AssertionError("Must fail before network")):
                result = maintain(Path(temp), generate=True, topic_id="safety")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["topics"][0]["model_calls"], 0)
            self.assertFalse(result["published"])

    def test_watch_rejects_invalid_interval_before_work(self):
        with self.assertRaises(ReviewError):
            watch(Path("missing-private-library"), interval_hours=float("nan"))


if __name__ == "__main__":
    unittest.main()
