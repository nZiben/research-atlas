"""Incremental discovery invariants using the real retained bibliography."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from review_forge.publishing import load_bibliography
from review_forge.refresh import bare, merge_record, page_finished, read_page, refresh
from review_forge.util import ReviewError, write_json

ROOT = Path(__file__).resolve().parents[1]


def incoming(paper):
    return {**paper, 'published': paper['published_at'], 'updated': paper['updated_at']}


class DiscoveryRefresh(unittest.TestCase):
    def setUp(self):
        self.data = load_bibliography(ROOT / 'bibliography/papers.json')
        self.plan = json.loads((ROOT / 'bibliography/search-plan.json').read_text())
        self.records = self.data['topics']['efficiency']
        self.checked = self.data['checked_at']

    def test_new_record_is_candidate_and_does_not_remove_prior_records(self):
        prior, candidate = self.records[:2]
        rows = {bare(prior['arxiv_id']): copy.deepcopy(prior)}
        self.assertEqual(merge_record(rows, incoming(candidate), self.checked), 'added')
        self.assertEqual(rows[bare(prior['arxiv_id'])], prior)
        self.assertEqual(rows[bare(candidate['arxiv_id'])]['coverage'], 'metadata_only')
        self.assertEqual(rows[bare(candidate['arxiv_id'])]['screening_status'], 'not_fulltext_reviewed')
        self.assertEqual(rows[bare(candidate['arxiv_id'])]['added_at'], self.checked)

    def test_same_version_is_not_reported_as_new(self):
        paper = self.records[0]
        rows = {bare(paper['arxiv_id']): copy.deepcopy(paper)}
        self.assertIsNone(merge_record(rows, incoming(paper), self.checked))
        self.assertEqual(len(rows), 1)

    def test_only_returned_record_advances_metadata_check_date(self):
        first, second = self.records[:2]
        rows = {bare(p['arxiv_id']): copy.deepcopy(p) for p in (first, second)}
        merge_record(rows, incoming(first), self.checked)
        self.assertEqual(rows[bare(second['arxiv_id'])], second)

    def test_retained_older_verification_dates_remain_truthful(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'papers.json'
            write_json(path, self.data)
            self.assertEqual(load_bibliography(path), self.data)
            changed = copy.deepcopy(self.data)
            changed['topics']['efficiency'][0]['checked_at'] = '2999-01-01'
            write_json(path, changed)
            with self.assertRaises(ReviewError):
                load_bibliography(path)

    def test_non_feed_cannot_look_like_successful_empty_search(self):
        for raw in (b'<html><body>Unavailable</body></html>', b'<feed/>', b'not xml'):
            with self.assertRaises(ReviewError):
                read_page(raw)

    def test_short_page_must_not_skip_unseen_records(self):
        record = incoming(self.records[0])
        with self.assertRaises(ReviewError):
            page_finished([record], 2, 0, 100, record['published'][:10], 'published')
        self.assertTrue(page_finished([record], 2, 1, 100, record['published'][:10], 'published'))

    def test_invalid_date_or_page_limit_fails_before_network(self):
        with tempfile.TemporaryDirectory() as temp, patch('review_forge.refresh.HTTP.get', side_effect=AssertionError('No network')):
            for kwargs in ({'max_pages': 0}, {'page_size': 101}):
                with self.assertRaises(ReviewError):
                    refresh(self.plan, self.data, Path(temp), self.checked, **kwargs)

    def test_discovery_failure_does_not_mutate_input_corpus(self):
        before = copy.deepcopy(self.data)
        with tempfile.TemporaryDirectory() as temp, patch('review_forge.refresh.HTTP.get', side_effect=ReviewError('Unavailable')):
            with self.assertRaises(ReviewError):
                refresh(self.plan, self.data, Path(temp), self.checked)
        self.assertEqual(self.data, before)


if __name__ == '__main__':
    unittest.main()
