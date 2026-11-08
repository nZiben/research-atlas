"""Read-only serving and publishing boundaries; no external requests or generated reviews."""
from __future__ import annotations

import http.client
import json
import shutil
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from review_forge.publishing import build_site, release
from review_forge.export import write_html
from review_forge.server import handler_for
from review_forge.util import ReviewError, write_json, write_text


class StaticLibrary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = self.root / 'private-library'
        self.library.mkdir()
        write_text(self.library / '.env', 'PRIVATE_TEST_MARKER=not-a-real-credential\n')
        write_text(self.library / 'safety' / 'review.toml', '# Private config fixture\n')
        self.site = build_site(self.library, self.root / 'site')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.site))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        self.port = self.server.server_address[1]

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request(method, path)
        response = connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())

    def test_ten_empty_published_sections_without_draft_leakage(self):
        code, body, _ = self.request('GET', '/data/library.json')
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(len(data['topics']), 10)
        self.assertFalse(any(topic['has_review'] for topic in data['topics']))
        self.assertNotIn('settings', data)
        self.assertNotIn('csrf_token', data)
        self.assertFalse(list(self.library.glob('*/candidates.json')))
        self.assertEqual(self.site.stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.site / 'data/library.json').stat().st_mode & 0o777, 0o644)

    def test_static_assets_and_topic_data(self):
        for path, mime in (('/', 'text/html'), ('/app.js', 'javascript'), ('/style.css', 'text/css'), ('/mark.svg', 'image/svg+xml'), ('/data/topics/robotics.json', 'application/json')):
            code, body, headers = self.request('GET', path)
            self.assertEqual(code, 200)
            self.assertTrue(body)
            self.assertIn(mime, headers['Content-Type'])

    def test_generation_and_settings_routes_do_not_exist(self):
        for route in ('/api/jobs', '/api/settings', '/api/library', '/api/job'):
            self.assertEqual(self.request('GET', route)[0], 404)
            self.assertEqual(self.request('POST', route)[0], 405)
        for method in ('PUT', 'PATCH', 'DELETE'):
            self.assertEqual(self.request(method, '/')[0], 405)

    def test_public_directory_contains_no_private_material(self):
        for file in self.site.rglob('*'):
            if file.is_file():
                self.assertNotIn(b'not-a-real-credential', file.read_bytes())
                self.assertNotIn('snapshot.json', file.name)
        for path in ('/.env', '/../private-library/.env', '/private-library/safety/review.toml', '/snapshot.json'):
            self.assertEqual(self.request('GET', path)[0], 404)

    def test_only_released_snapshots_can_enter_the_site(self):
        write_json(self.library / 'safety' / 'latest.json', {'snapshot_id': 'unpublished-draft'})
        build_site(self.library, self.site)
        data = json.loads((self.site / 'data/topics/safety.json').read_text())
        self.assertIsNone(data['review'])
        self.assertEqual(data['versions'], [])

    def test_real_bibliography_does_not_become_a_scientific_review(self):
        source = Path(__file__).resolve().parents[1] / 'bibliography/papers.json'
        target = self.root / 'bibliography/papers.json'
        target.parent.mkdir()
        shutil.copyfile(source, target)
        metadata = json.loads(target.read_text())
        metadata['topics']['safety'][0]['local_path'] = 'PRIVATE_TEST_MARKER'
        write_json(target, metadata)
        build_site(self.library, self.site)
        data = json.loads((self.site / 'data/topics/safety.json').read_text())
        self.assertEqual(len(data['papers']), 4)
        self.assertIsNone(data['review'])
        self.assertEqual(data['versions'], [])
        self.assertTrue(all(p['decision'] == 'reading_list' for p in data['papers']))
        self.assertNotIn('PRIVATE_TEST_MARKER', json.dumps(data))

    def test_invalid_bibliography_cannot_replace_public_site(self):
        target = self.root / 'bibliography/papers.json'
        metadata = json.loads((Path(__file__).resolve().parents[1] / 'bibliography/papers.json').read_text())
        metadata['topics']['safety'][0]['coverage'] = 'full_text'
        write_json(target, metadata)
        before = (self.site / 'data/library.json').read_bytes()
        with self.assertRaises(ReviewError):
            build_site(self.library, self.site)
        self.assertEqual((self.site / 'data/library.json').read_bytes(), before)

    def test_missing_or_unapproved_release_is_rejected(self):
        with self.assertRaises(ReviewError):
            release(self.library, 'safety', 'missing', 'Technical check', reviewed=False)
        with self.assertRaises(ReviewError):
            release(self.library, 'safety', 'missing', 'Technical check', reviewed=True)
        self.assertFalse((self.library / 'releases.json').exists())

    def test_build_failure_preserves_previous_public_site(self):
        before = (self.site / 'data/library.json').read_bytes()
        write_json(self.library / 'releases.json', {'schema_version': 1, 'topics': {'safety': [{'snapshot_id': 'missing', 'editor_reviewed': True}]}})
        with self.assertRaises(ReviewError):
            build_site(self.library, self.site)
        self.assertEqual((self.site / 'data/library.json').read_bytes(), before)

    def test_build_refuses_private_or_unowned_destination(self):
        for path in (self.library, self.root, self.library / 'public'):
            with self.assertRaises(ReviewError):
                build_site(self.library, path)
        other = self.root / 'unrelated'
        write_text(other / 'keep.txt', 'Preserve this file')
        with self.assertRaises(ReviewError):
            build_site(self.library, other)
        self.assertEqual((other / 'keep.txt').read_text(), 'Preserve this file')

    def test_public_html_has_no_links_to_private_export_only_files(self):
        # Empty rendering scaffold, not a paper, model response or research review.
        payload = {'synthesis': {'title': 'Technical HTML boundary check', 'sections': [], 'concepts': []},
                   'at': 'technical-test', 'review': {'topic': 'Technical check', 'questions': []},
                   'cards': [], 'quality': {'warnings': []}}
        write_html(self.root, payload, {}, {}, public=True)
        body = (self.root / 'index.html').read_text()
        self.assertNotIn("href='review.json'", body)
        self.assertNotIn("href='graph.json'", body)
        self.assertIn("href='references.bib'", body)


if __name__ == '__main__':
    unittest.main()
