"""Incremental arXiv discovery: retain old records, add candidates, never edit reviews."""
from __future__ import annotations

import argparse
import copy
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

from .bibliography import matches_scope
from .http import HTTP
from .publishing import load_bibliography
from .sources import parse_arxiv
from .util import ReviewError, digest, locked, normalized, read_json, write_bytes, write_json


def bare(aid: str) -> str:
    return re.sub(r'v\d+$', '', aid)


def read_page(raw: bytes):
    try:
        tree = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ReviewError('Invalid arXiv response.') from exc
    if tree.tag != '{http://www.w3.org/2005/Atom}feed' or tree.find('{http://a9.com/-/spec/opensearch/1.1/}totalResults') is None:
        raise ReviewError('Expected an arXiv Atom feed with a total count.')
    return parse_arxiv(raw)


def page_finished(incoming: list, total: int, offset: int, page_size: int, start: str, field: str) -> bool:
    finished = offset + len(incoming) >= total or (bool(incoming) and min(p[field][:10] for p in incoming) < start)
    if len(incoming) < page_size and not finished:
        raise ReviewError('Short arXiv page before the window ended; refusing to skip records.')
    return finished


def merge_record(rows: dict, item: dict, checked_at: str) -> str | None:
    """Only metadata returned by arXiv can advance its verification date."""
    key = bare(item['arxiv_id'])
    old = rows.get(key)
    title_key = re.sub(r'\W+', '', normalized(item['title']).casefold())
    doi = (item.get('doi') or '').casefold().removeprefix('https://doi.org/')
    for other_key, other in rows.items():
        if other_key != key and (re.sub(r'\W+', '', normalized(other['title']).casefold()) == title_key
                or (doi and (other.get('doi') or '').casefold().removeprefix('https://doi.org/') == doi)):
            return None
    if old and (old['updated_at'], int(old['arxiv_id'].split('v')[1])) > (item['updated'][:10], int(item['arxiv_id'].split('v')[1])):
        return None
    row = {key: item.get(key) for key in ('title', 'authors', 'year', 'arxiv_id', 'doi', 'url')}
    row.update(id='arxiv-' + key.replace('.', '-'), published_at=item['published'][:10],
               updated_at=item['updated'][:10], checked_at=checked_at,
               coverage='metadata_only', verification='arxiv_metadata', screening_status='not_fulltext_reviewed',
               discovery_groups=sorted(set((old or {}).get('discovery_groups', []) + ['recent'])),
               added_at=(old or {}).get('added_at', checked_at if not old else old['checked_at']))
    rows[key] = row
    return 'added' if old is None else 'updated' if old['arxiv_id'] != item['arxiv_id'] else None


def refresh(plan: dict, previous: dict, cache: Path, checked_at: str, page_size=100, max_pages=10) -> dict:
    checked = date.fromisoformat(checked_at)
    start = date.fromisoformat(previous['checked_at']) - timedelta(days=7)
    if checked < date.fromisoformat(previous['checked_at']) or not 1 <= page_size <= 100 or not 1 <= max_pages <= 100:
        raise ReviewError('Invalid refresh date or pagination limits.')
    if set(plan['topics']) != set(previous['topics']):
        raise ReviewError('Refresh requires an existing bibliography for every configured topic.')
    result = copy.deepcopy(previous)
    result.update(checked_at=checked_at, through=checked_at, plan_sha256=digest({**plan, 'through': checked_at}))
    result['method'] = ('Retained initial bounded corpus plus incremental, paginated arXiv submission/update discovery with a seven-day overlap. '
                        'Explicit title/abstract filters; metadata-only candidates. No automatic scientific synthesis or review changes.')
    result['searches'] = [s for s in previous.get('searches', []) if not s.get('incremental')]
    report = {'checked_at': checked_at, 'window_start': start.isoformat(), 'topics': {}}
    http = HTTP(timeout=45)
    for topic, query in plan['topics'].items():
        rows = {bare(p['arxiv_id']): p for p in result['topics'][topic]}
        changes = {'added': set(), 'updated': set()}
        for sort, field in (('submittedDate', 'published'), ('lastUpdatedDate', 'updated')):
            complete = False
            seen_ids = set()
            for page in range(max_pages):
                bounded = f'({query["main"]}) AND submittedDate:[{plan["year_from"]}01010000 TO {checked:%Y%m%d}2359]'
                if sort == 'submittedDate':
                    bounded += f' AND submittedDate:[{start:%Y%m%d}0000 TO {checked:%Y%m%d}2359]'
                url = 'https://export.arxiv.org/api/query?' + urlencode({'search_query': bounded, 'start': page * page_size,
                    'max_results': page_size, 'sortBy': sort, 'sortOrder': 'descending'})
                cached = cache / (digest([url, checked_at]) + '.xml')
                if cached.exists():
                    raw = cached.read_bytes()
                else:
                    raw = http.get(url, delay=3.1)
                    write_bytes(cached, raw)
                incoming, total = read_page(raw)
                if total < 0 or (not incoming and total > page * page_size):
                    raise ReviewError('Incomplete arXiv page; refusing to advance the discovery watermark.')
                if incoming and all(p['arxiv_id'] in seen_ids for p in incoming):
                    raise ReviewError('Repeated arXiv page; pagination did not advance.')
                eligible, excluded = 0, []
                for item in incoming:
                    seen_ids.add(item['arxiv_id'])
                    published, updated = item['published'][:10], item['updated'][:10]
                    if not f'{plan["year_from"]}-01-01' <= published <= updated <= checked_at:
                        raise ReviewError('Inconsistent arXiv dates; refusing to publish the response.')
                    if item[field][:10] < start.isoformat():
                        continue
                    if not matches_scope(item, query['must_match']):
                        excluded.append(item['arxiv_id'])
                        continue
                    change = merge_record(rows, item, checked_at)
                    if change:
                        changes[change].add(bare(item['arxiv_id']))
                    eligible += 1
                complete = page_finished(incoming, total, page * page_size, page_size, start.isoformat(), field)
                result['searches'].append({'topic': topic, 'strategy': 'recent', 'label': 'Incremental ' + sort,
                    'url': url, 'returned': len(incoming), 'eligible_returned': eligible, 'total_matches': total,
                    'truncated': False, 'scope_excluded': len(excluded), 'scope_excluded_ids': excluded,
                    'response_sha256': digest(raw), 'incremental': True, 'complete': complete,
                    'window_start': start.isoformat(), 'page': page})
                print(f'{topic}/{sort} page {page+1}: {len(incoming)} records, {eligible} eligible', flush=True)
                if complete:
                    break
            if not complete:
                raise ReviewError(f'{topic}/{sort}: page limit reached before the overlap window was covered. Previous public data preserved; increase --max-pages.')
        # A record first encountered by submission and then by update remains one addition.
        changes['updated'] -= changes['added']
        result['topics'][topic] = sorted(rows.values(), key=lambda p: (p['published_at'], p['arxiv_id']), reverse=True)
        counts = {key: len(ids) for key, ids in changes.items()}
        report['topics'][topic] = {**counts, 'added_ids': sorted(changes['added']), 'updated_ids': sorted(changes['updated'])}
        papers = result['topics'][topic]
        years = sorted({p['year'] for p in papers})
        result.setdefault('coverage', {})[topic] = {**previous.get('coverage', {}).get(topic, {}),
            'papers': len(papers), 'first_publication': min(p['published_at'] for p in papers),
            'last_publication': max(p['published_at'] for p in papers),
            'years': {str(y): sum(p['year'] == y for p in papers) for y in years},
            'groups': {g: sum(g in p['discovery_groups'] for p in papers) for g in ('relevance', 'recent', 'earlier', 'evaluation')},
            'systematic_search_complete': False, 'full_text_reviewed': 0}
        print(f'{topic}: {counts["added"]} new, {counts["updated"]} revised; {len(papers)} retained', flush=True)
    result['last_update'] = report
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, default=Path('bibliography/search-plan.json'))
    parser.add_argument('--bibliography', type=Path, default=Path('bibliography/papers.json'))
    parser.add_argument('--cache', type=Path, default=Path('library/.refresh'))
    parser.add_argument('--checked-at', default=date.today().isoformat())
    parser.add_argument('--max-pages', type=int, default=10)
    parser.add_argument('--force', action='store_true', help='Recheck even when the bibliography was checked today.')
    args = parser.parse_args()
    try:
        with locked(args.cache):
            previous = load_bibliography(args.bibliography)
            if previous['checked_at'] == args.checked_at and not args.force:
                print('Already checked on this date; no requests or changes.')
                return
            result = refresh(read_json(args.plan), previous, args.cache, args.checked_at, max_pages=args.max_pages)
            staged = args.cache / 'completed.json'
            write_json(staged, result)
            load_bibliography(staged)
            write_json(args.bibliography, result)
            print('Complete discovery saved. Reviews were not modified.')
    except (ReviewError, OSError, ValueError, KeyError, TypeError, re.error) as exc:
        parser.exit(2, f'Refresh failed; previous bibliography preserved: {exc}\n')


if __name__ == '__main__':
    main()
