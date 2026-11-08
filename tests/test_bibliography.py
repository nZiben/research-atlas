"""Discovery controls using real bibliographic titles, without model/API stand-ins."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from review_forge.bibliography import build_bibliography, matches_scope
from review_forge.util import ReviewError

ROOT = Path(__file__).resolve().parents[1]


class Bibliography(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT / 'bibliography/search-plan.json').read_text())
        self.seeds = json.loads((ROOT / 'bibliography/seeds.json').read_text())

    def test_real_decoding_title_matches_and_speculative_samples_do_not(self):
        patterns = self.plan['topics']['efficiency']['must_match']
        paper = next(p for p in self.seeds['topics']['efficiency'] if 'Speculative Decoding' in p['title'])
        self.assertTrue(matches_scope(paper, patterns))
        # Actual text fragment from the unrelated sentiment-analysis paper arXiv:2010.08318.
        self.assertFalse(matches_scope({'title': 'Multi-task Learning of Negation and Speculation for Targeted Sentiment Classification',
                                        'abstract': 'negated and speculative samples'}, patterns))

    def test_reasonable_is_not_temporal_reasoning(self):
        patterns = self.plan['topics']['vision']['must_match']
        # Words from the unrelated audio-visual quality paper arXiv:2001.11406.
        self.assertFalse(matches_scope({'title': 'reasonable performance'}, [patterns[2]]))
        paper = next(p for p in self.seeds['topics']['vision'] if 'Temporal-Logical Reasoning' in p['title'])
        self.assertTrue(matches_scope(paper, [patterns[2]]))

    def test_future_search_cutoff_rejected_before_network(self):
        self.plan['through'] = '2027-01-01'
        with tempfile.TemporaryDirectory() as temp, patch('review_forge.bibliography.HTTP.get', side_effect=AssertionError('No network')):
            with self.assertRaises(ReviewError):
                build_bibliography(self.plan, self.seeds, Path(temp), '2026-11-08')

    def test_empty_scope_does_not_accept_every_candidate(self):
        self.assertFalse(matches_scope(self.seeds['topics']['safety'][0], []))

    def test_current_bibliography_has_no_duplicate_titles_or_ids_per_topic(self):
        data = json.loads((ROOT / 'bibliography/papers.json').read_text())
        for topic, rows in data['topics'].items():
            self.assertEqual(len(rows), len({p['id'] for p in rows}), topic)
            self.assertTrue(all(p['coverage'] == 'metadata_only' for p in rows))
            self.assertEqual(len(rows), data['coverage'][topic]['papers'])
            self.assertTrue(all(p['published_at'] <= data['through'] for p in rows))


if __name__ == '__main__':
    unittest.main()
