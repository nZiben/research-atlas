"""Bounded, reproducible bibliography discovery. No models or scientific synthesis."""
from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from .http import HTTP
from .sources import parse_arxiv
from .util import ReviewError, digest, locked, normalized, read_json, write_bytes, write_json


def matches_scope(paper: dict, patterns: list[str]) -> bool:
    text = paper.get('title', '') + ' ' + paper.get('abstract', '')
    return bool(patterns) and all(re.search(pattern, text, re.I) for pattern in patterns)


def build_bibliography(plan: dict, seeds: dict, cache: Path, checked_at: str) -> dict:
    if not isinstance(plan, dict) or not isinstance(seeds, dict):
        raise ReviewError('Both a search plan and a seed bibliography JSON object are required.')
    checked = date.fromisoformat(checked_at)
    through = date.fromisoformat(plan['through'])
    if plan.get('schema_version') != 1 or through > checked or not 1900 <= plan['year_from'] <= through.year:
        raise ReviewError('Invalid bibliography plan or date range.')
    strategies = plan['strategies']
    if not strategies or len({s['id'] for s in strategies}) != len(strategies):
        raise ReviewError('Search strategies must have unique IDs.')
    for strategy in strategies:
        if (strategy['sort'] not in {'relevance', 'submittedDate', 'lastUpdatedDate'}
                or strategy['order'] not in {'ascending', 'descending'}
                or type(strategy['limit']) is not int or not 1 <= strategy['limit'] <= 100):
            raise ReviewError('Invalid sort or bounded result limit.')
    result = {'schema_version': 1, 'checked_at': checked_at, 'through': plan['through'],
              'method': 'Bounded multi-strategy arXiv search, explicit title/abstract scope filters, retained seeds and deduplication. Bibliographic metadata only; relevance and full texts require screening. All eligible returned records retained, no equal per-topic quota.',
              'topics': {}, 'searches': [], 'coverage': {}, 'plan_sha256': digest(plan)}
    http = HTTP(timeout=45)
    for topic_id, queries in plan['topics'].items():
        papers, private_papers = {}, {}
        for seed in seeds.get('topics', {}).get(topic_id, []):
            paper = {**seed, 'checked_at': checked_at, 'discovery_groups': ['seed'], 'screening_status': 'not_fulltext_reviewed'}
            papers[re.sub(r'v\d+$', '', paper['arxiv_id'])] = paper
        excluded_by_date, excluded_by_scope = 0, 0
        patterns = queries['must_match']
        for strategy in strategies:
            query = queries[strategy['query']]
            if not isinstance(query, str) or not query.strip():
                raise ReviewError('Empty bibliography query.')
            bounded = f'({query}) AND submittedDate:[{plan["year_from"]}01010000 TO {through:%Y%m%d}2359]'
            url = 'https://export.arxiv.org/api/query?' + urlencode({'search_query': bounded, 'start': 0,
                     'max_results': strategy['limit'], 'sortBy': strategy['sort'], 'sortOrder': strategy['order']})
            file = cache / (digest([url, checked_at]) + '.xml')
            cached = file.exists()
            print(f'{topic_id}/{strategy["id"]}: querying arXiv' + (' (cached)' if cached else ''), flush=True)
            if cached:
                raw = file.read_bytes()
            else:
                raw = http.get(url, delay=3.1)
                write_bytes(file, raw)
            incoming, total = parse_arxiv(raw)
            accepted, scope_excluded = 0, []
            for item in incoming:
                published, updated = item['published'][:10], item['updated'][:10]
                if not (f'{plan["year_from"]}-01-01' <= published <= through.isoformat() and published <= updated <= checked_at):
                    excluded_by_date += 1
                    continue
                if not matches_scope(item, patterns):
                    excluded_by_scope += 1
                    scope_excluded.append(item['arxiv_id'])
                    continue
                aid = item['arxiv_id']
                bare = re.sub(r'v\d+$', '', aid)
                previous = papers.get(bare, {})
                groups = sorted(set(previous.get('discovery_groups', []) + [strategy['id']]))
                row = {'id': 'arxiv-' + bare.replace('.', '-'), 'title': item['title'],
                       'authors': item['authors'], 'year': item['year'], 'published_at': published,
                       'updated_at': updated, 'arxiv_id': aid, 'doi': item.get('doi'), 'url': item['url'],
                       'verification': 'arxiv_metadata', 'coverage': 'metadata_only', 'checked_at': checked_at,
                       'discovery_groups': groups, 'screening_status': 'not_fulltext_reviewed'}
                if previous and previous['updated_at'] > updated:
                    row = {**previous, 'discovery_groups': groups}
                papers[bare] = row
                private_papers[bare] = item
                accepted += 1
            record = {'topic': topic_id, 'strategy': strategy['id'], 'label': strategy['label'], 'url': url,
                      'returned': len(incoming), 'eligible_returned': accepted, 'total_matches': total,
                      'truncated': total > len(incoming), 'scope_excluded_ids': scope_excluded,
                      'scope_excluded': len(scope_excluded), 'response_sha256': digest(raw), 'cache_hit': cached}
            result['searches'].append(record)
            print(f'  returned={len(incoming)}, total_matches={total}, eligible={accepted}', flush=True)
            write_json(cache / 'progress.json', result)
        # Merge only exact DOI/title matches; no fuzzy bibliographic guesses.
        unique, aliases, duplicates = [], {}, 0
        for paper in sorted(papers.values(), key=lambda p: (p['published_at'], p['arxiv_id']), reverse=True):
            keys = {'title:' + re.sub(r'\W+', '', normalized(paper['title']).lower())}
            if paper.get('doi'):
                keys.add('doi:' + paper['doi'].lower().removeprefix('https://doi.org/'))
            match = next((aliases[k] for k in keys if k in aliases), None)
            if match is not None:
                unique[match]['discovery_groups'] = sorted(set(unique[match]['discovery_groups'] + paper['discovery_groups']))
                duplicates += 1
                for key in keys:
                    aliases[key] = match
                continue
            index = len(unique)
            unique.append(paper)
            for key in keys:
                aliases[key] = index
        result['topics'][topic_id] = unique
        # Abstracts stay private, available to the existing model-assisted screening command.
        write_json(cache / 'imports' / (topic_id + '.json'),
                   [private_papers.get(re.sub(r'v\d+$', '', paper['arxiv_id']), paper) for paper in unique])
        result['coverage'][topic_id] = {'papers': len(unique), 'first_publication': min((p['published_at'] for p in unique), default=None),
             'last_publication': max((p['published_at'] for p in unique), default=None),
             'years': {str(y): sum(p['year'] == y for p in unique) for y in sorted({p['year'] for p in unique})},
             'groups': {s['id']: sum(s['id'] in p['discovery_groups'] for p in unique) for s in strategies},
             'duplicate_records_removed': duplicates, 'records_excluded_by_date': excluded_by_date,
             'scope_filter_exclusions': excluded_by_scope,
             'full_text_reviewed': 0, 'systematic_search_complete': False}
        print(f'{topic_id}: {len(unique)} distinct records retained', flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, default=Path('bibliography/search-plan.json'))
    parser.add_argument('--seeds', type=Path, default=Path('bibliography/seeds.json'))
    parser.add_argument('--output', type=Path, default=Path('bibliography/papers.json'))
    parser.add_argument('--cache', type=Path, default=Path('library/.bibliography-search'))
    parser.add_argument('--checked-at', default=date.today().isoformat())
    args = parser.parse_args()
    try:
        with locked(args.cache):
            result = build_bibliography(read_json(args.plan), read_json(args.seeds), args.cache, args.checked_at)
            # Validation precedes replacement of the last successful public bibliography.
            from .publishing import load_bibliography
            staged = args.cache / 'completed.json'
            write_json(staged, result)
            load_bibliography(staged)
            write_json(args.output, result)
        print(f'Saved {sum(len(v) for v in result["topics"].values())} topic records. No full-text review or publication performed.')
    except (ReviewError, OSError, ValueError, KeyError, TypeError, re.error) as exc:
        parser.exit(2, f'Bibliography discovery failed; previous public output preserved: {exc}\n')


if __name__ == '__main__':
    main()
