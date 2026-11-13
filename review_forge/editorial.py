"""Explicit public, source-based reviews, separate from model-run snapshots."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .evidence import need
from .util import read_json


def load_editorial(path: Path) -> dict | None:
    if not path.exists():
        return None
    data = read_json(path)
    need(set(data) == {'schema_version', 'kind', 'snapshot_id', 'at', 'review', 'papers', 'cards', 'synthesis', 'comparison', 'quality', 'changes', 'publication'}, 'Unexpected editorial fields; only public content is allowed.')
    need(data.get('schema_version') == 1 and data.get('kind') == 'editorial_review', 'Invalid editorial review.')
    need(re.fullmatch(r'editorial-\d{4}-\d{2}-\d{2}', data['snapshot_id']), 'Invalid editorial version.')
    date.fromisoformat(data['at'])
    need(data['publication']['preparation'] == 'source_based_editorial'
         and data['publication']['expert_peer_review'] is False, 'Editorial provenance must be explicit.')
    papers = {paper['id']: paper for paper in data['papers']}
    need(papers and len(papers) == len(data['papers']), 'Duplicate or missing editorial papers.')
    for paper in papers.values():
        need(set(paper) == {'id', 'title', 'authors', 'year', 'arxiv_id', 'url', 'doi', 'source_sha256', 'checked_at', 'read_sections'}, 'Unexpected source metadata.')
        aid = paper['arxiv_id']
        need(re.fullmatch(r'\d{4}\.\d{4,5}v\d+', aid), 'Pin the read source version.')
        need(paper['url'] == 'https://arxiv.org/abs/' + aid, 'Invalid source URL.')
        need(paper['id'] == 'arxiv-' + aid.split('v')[0].replace('.', '-'), 'Source ID mismatch.')
        need(re.fullmatch(r'[0-9a-f]{64}', paper['source_sha256']), 'Missing source checksum.')
        need(paper['title'] and paper['authors'] and paper['read_sections'], 'Missing source metadata.')
    claims = {}
    seen_papers = set()
    for card in data['cards']:
        need(set(card) == {'paper_id', 'coverage', 'claims'}, 'Unexpected card metadata.')
        need(card['paper_id'] in papers and card['paper_id'] not in seen_papers, 'Invalid evidence card.')
        seen_papers.add(card['paper_id'])
        need(card['coverage'] == 'selected_full_text_sections', 'Do not overstate reading coverage.')
        quote_words = 0
        for claim in card['claims']:
            need(set(claim) == {'id', 'paper_id', 'kind', 'text', 'conditions', 'limitations', 'evidence'}, 'Unexpected claim metadata.')
            need(claim['id'] not in claims and claim['paper_id'] == card['paper_id'], 'Invalid claim ID.')
            need(claim['text'] and claim['conditions'] and claim['limitations'] and claim['evidence'], 'Incomplete evidence.')
            claims[claim['id']] = claim
            base = 'https://arxiv.org/html/' + papers[card['paper_id']]['arxiv_id'] + '#'
            for evidence in claim['evidence']:
                need(set(evidence) == {'quote', 'url', 'locator', 'segment_id'}, 'Unexpected evidence metadata.')
                need(re.fullmatch(r'[A-Za-z0-9.]+', evidence['segment_id']), 'Invalid source anchor.')
                need(evidence['url'] == base + evidence['segment_id'] and evidence['locator'], 'Evidence must link to the pinned source section.')
                need(evidence['quote'].strip(), 'Missing evidence quote.')
                quote_words += len(evidence['quote'].split())
        need(quote_words <= 25, 'Editorial quote budget exceeded.')
    need(seen_papers == set(papers), 'Every reviewed paper needs evidence.')

    def statement(item):
        need(set(item) == {'kind', 'text', 'claim_ids'}, 'Unexpected statement metadata.')
        need(item['kind'] in {'editorial', 'synthesis'} and item['text'], 'Invalid statement.')
        need(set(item['claim_ids']) <= claims.keys(), 'Unknown evidence reference.')
        need(item['claim_ids'] or item['kind'] == 'editorial', 'Scientific statements need evidence.')

    keys = []
    for section in data['synthesis']['sections']:
        need(re.fullmatch(r'[a-z][a-z-]*', section['key']), 'Invalid section key.')
        keys.append(section['key'])
        need(section['title'] and section['items'], 'Empty review section.')
        for item in section['items']:
            statement(item)
    need(len(keys) == len(set(keys)) and {'brief', 'comparison', 'scope'} <= set(keys), 'Missing reading levels.')
    for concept in data['synthesis']['concepts']:
        for item in [concept['definition'], *concept['statements'], *concept['disagreements']]:
            statement(item)
    need(data['comparison'] and data['quality']['warnings'], 'Missing comparison or limitations.')
    for row in data['comparison']:
        need(row['paper_id'] in papers and row['method'] and row['guarantee'] and row['setup'], 'Invalid comparison row.')
        need(row['claim_ids'] and all(cid in claims and claims[cid]['paper_id'] == row['paper_id'] for cid in row['claim_ids']), 'Comparison needs evidence from its paper.')
    return data
