"""Publication boundaries checked against the real first editorial review."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from review_forge.cli import topics
from review_forge.editorial import load_editorial
from review_forge.publishing import build_site
from review_forge.util import ReviewError, write_json

ROOT = Path(__file__).resolve().parents[1]


class EditorialPublication(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'reviews/efficiency.json'
        self.path.parent.mkdir()
        shutil.copyfile(ROOT / 'reviews/efficiency.json', self.path)
        self.data = json.loads(self.path.read_text())

    def reject(self, data):
        write_json(self.path, data)
        with self.assertRaises(ReviewError):
            load_editorial(self.path)

    def test_real_review_publishes_without_inventing_model_run_or_private_release(self):
        library = self.root / 'library'
        library.mkdir()
        site = build_site(library, self.root / 'public')
        data = json.loads((site / 'data/topics/efficiency.json').read_text())
        self.assertEqual(len(data['review']['papers']), 10)
        self.assertEqual(data['review']['kind'], 'editorial_review')
        self.assertFalse(data['review']['publication']['expert_peer_review'])
        self.assertFalse((library / 'releases.json').exists())
        standalone = site / 'exports/efficiency/editorial-2026-11-13/index.html'
        self.assertIn('https://arxiv.org/html/2211.17192v2#S3.SS3', standalone.read_text())
        self.assertIn('<table>', standalone.read_text())
        self.assertTrue((site / 'related.html').is_file())
        self.assertEqual(len(data['versions']), 1)

    def test_unknown_claim_cannot_support_a_conclusion(self):
        self.data['synthesis']['sections'][0]['items'][0]['claim_ids'].append('missing')
        self.reject(self.data)

    def test_unsafe_or_wrong_version_evidence_link_is_rejected(self):
        for url in ('javascript:alert(1)', 'https://arxiv.org/html/2211.17192v1#S3.SS3'):
            changed = copy.deepcopy(self.data)
            changed['cards'][0]['claims'][0]['evidence'][0]['url'] = url
            self.reject(changed)

    def test_quote_budget_and_private_metadata_are_rejected(self):
        changed = copy.deepcopy(self.data)
        changed['cards'][0]['claims'][0]['evidence'][0]['quote'] = 'word ' * 26
        self.reject(changed)
        changed = copy.deepcopy(self.data)
        changed['papers'][0]['local_path'] = '/private/source.html'
        self.reject(changed)

    def test_failed_editorial_build_preserves_published_site(self):
        library = self.root / 'library'
        library.mkdir()
        site = build_site(library, self.root / 'public')
        previous = (site / 'data/topics/efficiency.json').read_bytes()
        self.data['comparison'][0]['claim_ids'] = ['c2']
        write_json(self.path, self.data)
        with self.assertRaises(ReviewError):
            build_site(library, site)
        self.assertEqual(previous, (site / 'data/topics/efficiency.json').read_bytes())

    def test_every_active_tree_leaf_targets_a_real_section(self):
        sections = {s['key'] for s in self.data['synthesis']['sections']}
        def check(nodes):
            for node in nodes:
                if 'section' in node:
                    self.assertIn(node['section'], sections)
                check(node.get('children', []))
        check(next(t for t in topics() if t['id'] == 'efficiency')['tree'])


class AllPublishedReviews(unittest.TestCase):
    def test_every_direction_has_a_real_evidence_linked_review_and_tree(self):
        for topic in topics():
            data = load_editorial(ROOT / 'reviews' / (topic['id'] + '.json'))
            self.assertIsNotNone(data, topic['id'])
            self.assertGreaterEqual(len(data['papers']), 7)
            sections = {s['key'] for s in data['synthesis']['sections']}
            def check(nodes):
                for node in nodes:
                    if 'section' in node:
                        self.assertIn(node['section'], sections)
                    check(node.get('children', []))
            self.assertTrue(topic.get('tree'))
            check(topic['tree'])


if __name__ == '__main__':
    unittest.main()
