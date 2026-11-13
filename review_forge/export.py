from __future__ import annotations

import csv
import html
import io
import json
import re
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit

from .evidence import need, validate_claims, validate_synthesis
from .util import ReviewError, digest, read_json, safe_id, write_json, write_text


def md(value) -> str:
    return re.sub(r"([\\`*_{}\[\]<>#|])", r"\\\1", " ".join(str(value).split()))


def web_link(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return ""
    return quote(url, safe=":/?=&%#@+;,-._~")


def bibliography(papers: list[dict]) -> str:
    def escape(value):
        escapes = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "%": r"\%", "&": r"\&", "_": r"\_", "#": r"\#"}
        return "".join(escapes.get(char, char) for char in " ".join(str(value).split()))
    entries = []
    for paper in papers:
        fields = {"title": paper["title"], "author": " and ".join(paper.get("authors") or []),
                  "year": paper.get("year"), "doi": paper.get("doi"), "url": paper.get("url")}
        if paper.get("arxiv_id"):
            fields.update({"eprint": paper["arxiv_id"], "archivePrefix": "arXiv"})
        body = ",\n".join(f"  {key} = {{{escape(value)}}}" for key, value in fields.items() if value)
        entries.append("@misc{" + paper["id"] + ",\n" + body + "\n}")
    return "\n\n".join(entries) + "\n"


def load_snapshot(folder: Path) -> dict:
    payload = read_json(folder / "snapshot.json")
    if not payload:
        raise ReviewError("Snapshot does not exist.")
    need(payload.get("schema_version") == 1, "Unsupported snapshot schema.")
    checksum = payload.get("integrity_sha256")
    need(checksum == digest({k: v for k, v in payload.items() if k != "integrity_sha256"}), "Snapshot checksum mismatch.")
    all_claims = []
    for card in payload["cards"]:
        safe_id(card["paper_id"])
        document = payload["documents"][card["paper_id"]]
        need(document["segments_sha256"] == digest(document["segments"]), "Snapshot source checksum mismatch.")
        for claim in card["claims"]:
            safe_id(claim["id"])
            validate_claims({"claims": [{k: v for k, v in claim.items() if k not in {"id", "paper_id"}}]},
                            document["segments"], {**payload["config"]["limits"], "claims_per_chunk": 1})
            all_claims.append(claim)
    need(len({c["id"] for c in all_claims}) == len(all_claims), "Duplicate snapshot claim IDs.")
    validate_synthesis(payload["synthesis"], {c["id"] for c in payload["synthesis_claims"]})
    return payload


def export_snapshot(folder: Path, destination: Path) -> Path:
    payload = load_snapshot(folder)
    if destination.exists() and any(destination.iterdir()):
        raise ReviewError("Export directory is not empty. Choose a new destination; existing exports are preserved.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".export-", dir=destination.parent))
    try:
        write_export(payload, temporary)
        if destination.exists():
            destination.rmdir()  # Fails safely if another process added files.
        os.rename(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return destination


def write_export(payload: dict, destination: Path):
    papers = {p["id"]: p for p in payload["papers"]}
    claims = {c["id"]: c for card in payload["cards"] for c in card["claims"]}
    roles = {r["paper_id"]: r["decision"] for r in payload["selection"]["decisions"]}
    synthesis = payload["synthesis"]
    def references(ids, prefix=""):
        return " ".join(f"[{md(papers[claims[cid]['paper_id']]['title'])}]({prefix}papers/{claims[cid]['paper_id']}.md#{cid})" for cid in ids)
    def statement(item, prefix=""):
        label = "**Редакторское предложение:** " if item["kind"] == "editorial" else ""
        return label + md(item["text"]) + " " + references(item["claim_ids"], prefix)
    intro = [f"# {md(synthesis['title'])}", "", "**Черновик. Требуется научная проверка человеком.**", "",
             f"Снимок: `{payload['snapshot_id']}` · Дата (UTC): {payload['at']}", "",
             "[Понятия](concepts/index.md) · [Статьи](papers/index.md) · [Матрица доказательств](matrix.csv) · [Метод поиска](methodology.md) · [Изменения](changes.md)", "",
             "## Границы обзора", "", md(payload["review"]["topic"]), "",
             f"Годы: {payload['review']['year_from']}–{payload['review']['year_to']}. "
             f"Основной корпус: {sum(v == 'core' for v in roles.values())}; внешние работы: {sum(v == 'external' for v in roles.values())}.", ""]
    intro += ["- " + md(q) for q in payload["review"]["questions"]]
    for section in synthesis["sections"]:
        intro += ["", "## " + md(section["title"]), ""]
        intro += ["- " + statement(item) for item in section["items"]]
    intro += ["", "## Ограничения и проверка", ""] + ["- " + md(w) for w in payload["quality"]["warnings"]]
    write_text(destination / "README.md", "\n".join(intro) + "\n")
    concept_index = ["# Понятия", "", "[Обзор](../README.md)", ""]
    for concept in synthesis["concepts"]:
        concept_index.append(f"- [{md(concept['name'])}]({concept['id']}.md)")
        lines = ["# " + md(concept["name"]), "", "[Индекс](index.md) · [Обзор](../README.md)", "",
                 "Синонимы: " + (", ".join(md(a) for a in concept["aliases"]) or "—"), "", "## Определение", "",
                 statement(concept["definition"], "../"), "", "## Позиции работ", ""]
        lines += ["- " + statement(item, "../") for item in concept["statements"]]
        lines += ["", "## Расхождения и границы применимости", ""]
        lines += ["- " + statement(item, "../") for item in concept["disagreements"]] or ["В выбранных тезисах расхождения не выделены; это не доказывает консенсус."]
        related = [r for r in synthesis["relations"] if concept["id"] in {r["source"], r["target"]}]
        if related:
            lines += ["", "## Связи", ""]
            for edge in related:
                other = edge["target"] if edge["source"] == concept["id"] else edge["source"]
                lines.append(f"- [{md(other)}]({other}.md) ({edge['kind']}): " + statement(edge["statement"], "../"))
        write_text(destination / "concepts" / f"{concept['id']}.md", "\n".join(lines) + "\n")
    write_text(destination / "concepts" / "index.md", "\n".join(concept_index) + "\n")
    paper_index = ["# Статьи", "", "[Обзор](../README.md)", ""]
    for card in payload["cards"]:
        pid = card["paper_id"]
        paper, doc = papers[pid], payload["documents"][pid]
        url = web_link(doc["source"].get("url", "") or paper.get("url", ""))
        source_link = f"[Первоисточник]({url})" if url else "Первоисточник: локальный файл (путь хранится только в приватном снимке)."
        paper_index.append(f"- [{md(paper['title'])}]({pid}.md) — {paper.get('year') or 'год неизвестен'}, {roles[pid]}, {card['coverage']}")
        lines = ["# " + md(paper["title"]), "", "[Индекс](index.md) · [Обзор](../README.md)", "",
                 md(", ".join(paper.get("authors") or [])), "", source_link, "",
                 f"Роль: {roles[pid]}. Покрытие: **{card['coverage']}**. Проверено человеком: **нет**.", "",
                 f"Версия arXiv: {md(doc.get('resolved_arxiv_id') or 'не применимо')}. Лицензия: {md(doc['license'])}.", "",
                 f"SHA-256 текста: `{doc['segments_sha256']}`.", "",
                 "## Извлечённые тезисы", ""]
        remaining = payload["config"]["limits"]["export_quote_words_per_paper"]
        selected = set(card["synthesis_selection"]["claim_ids"])
        for claim in card["claims"]:
            lines += [f"###### {claim['id']}", "", f"**{claim['kind']}** · В синтезе: {'да' if claim['id'] in selected else 'нет'}", "", md(claim["text"]), "",
                      "Условия: " + ("; ".join(md(v) for v in claim["conditions"]) or "не извлечены из фрагмента"), "",
                      "Авторские ограничения: " + ("; ".join(md(v) for v in claim["limitations"]) or "не извлечены; это не означает их отсутствия"), ""]
            for ev in claim["evidence"]:
                segment = next(s for s in doc["segments"] if s["id"] == ev["segment_id"])
                locator = md(segment["locator"] + ", " + segment["id"])
                if url and segment["locator"].startswith("PDF page "):
                    page_url = url.split("#")[0] + "#page=" + segment["locator"].split()[-1]
                    locator = f"[{locator}]({page_url})"
                words = len(ev["quote"].split())
                if words <= remaining:
                    lines += ["> " + md(ev["quote"]), "", "Источник: " + locator, ""]
                    remaining -= words
                else:
                    lines += ["Источник: " + locator + ". Цитата сохранена в локальных JSON-данных; лимит цитат Markdown исчерпан.", ""]
        lines += ["## Отбор тезисов для синтеза", "", md(card["synthesis_selection"]["reason"]), "", "## Ограничения извлечения", ""]
        lines += ["- " + md(w) for w in doc["warnings"]]
        write_text(destination / "papers" / f"{pid}.md", "\n".join(lines) + "\n")
    write_text(destination / "papers" / "index.md", "\n".join(paper_index) + "\n")
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["paper_id", "paper_title", "role", "coverage", "claim_id", "kind", "claim", "conditions", "limitations", "source_segments"])
    for card in payload["cards"]:
        for claim in card["claims"]:
            row = [card["paper_id"], papers[card["paper_id"]]["title"], roles[card["paper_id"]], card["coverage"],
                   claim["id"], claim["kind"], claim["text"], "; ".join(claim["conditions"]), "; ".join(claim["limitations"]),
                   "; ".join(e["segment_id"] for e in claim["evidence"])]
            # CSV may later be opened in a spreadsheet. Prevent formula injection.
            writer.writerow(["'" + value if str(value).startswith(("=", "+", "-", "@", "\t", "\r")) else value for value in row])
    write_text(destination / "matrix.csv", stream.getvalue())
    graph_nodes = [{"id": pid, "kind": "paper", "label": p["title"]} for pid, p in papers.items()]
    graph_nodes += [{"id": c["id"], "kind": "claim", "label": c["text"]} for c in claims.values()]
    graph_nodes += [{"id": "concept-" + c["id"], "kind": "concept", "label": c["name"]} for c in synthesis["concepts"]]
    edges = [{"source": c["paper_id"], "target": c["id"], "kind": "contains"} for c in claims.values()]
    for concept in synthesis["concepts"]:
        for item in [concept["definition"], *concept["statements"], *concept["disagreements"]]:
            edges += [{"source": cid, "target": "concept-" + concept["id"], "kind": "supports"} for cid in item["claim_ids"]]
    edges += [{"source": "concept-" + r["source"], "target": "concept-" + r["target"], "kind": r["kind"], "claim_ids": r["statement"]["claim_ids"]} for r in synthesis["relations"]]
    write_json(destination / "graph.json", {"schema_version": 1, "nodes": graph_nodes, "edges": edges})
    public_papers = [{k: v for k, v in p.items() if k not in {"local_path", "abstract"}} for p in papers.values()]
    write_json(destination / "review.json", {"schema_version": 1, "snapshot_id": payload["snapshot_id"], "at": payload["at"],
               "status": "draft", "review": payload["review"], "papers": public_papers, "cards": payload["cards"],
               "synthesis": synthesis, "quality": payload["quality"], "audit": payload["audit"], "changes": payload["changes"],
               "search_reports": payload.get("search_reports", []),
               "note": "Local export includes short evidence quotes; the Markdown quote budget does not redact JSON. No publication permission is implied."})
    write_text(destination / "references.bib", bibliography(list(papers.values())))
    selection_lines = ["# Метод и журнал отбора", "", "Поиск ограничен провайдерами, запросами и бюджетом; полнота не доказана.", "",
                       "Критерии включения: " + "; ".join(md(v) for v in payload["review"]["include"]), "",
                       "Критерии исключения: " + "; ".join(md(v) for v in payload["review"]["exclude"]), "",
                       "Запросы: " + "; ".join(md(q) for q in (payload["config"]["search"]["queries"] or (payload.get("query_plan") or {}).get("content", {}).get("queries") or [payload["review"]["topic"]])), "",
                       "Провайдеры: " + ", ".join(payload["config"]["search"]["providers"]), "",
                       "Полные ответы поисковых API и попытки запросов сохранены в приватной папке searches/ темы.", ""]
    for row in payload["selection"]["decisions"]:
        selection_lines.append(f"- `{row['paper_id']}`: **{row['decision']}** ({row['by']}) — {md(row['reason'])}")
    for search in payload.get("search_reports", []):
        report = search["report"]
        selection_lines += ["", "## Поиск " + md(report["at"]), ""]
        for attempt in report["attempts"]:
            status = attempt.get("error") or f"получено {attempt['returned']} из {attempt['total']}; усечение: {attempt['truncated']}"
            selection_lines.append("- " + md(f"{attempt['provider']}, {attempt['query']}, {attempt['sort']}: {status}"))
    write_text(destination / "methodology.md", "\n".join(selection_lines) + "\n")
    write_text(destination / "changes.md", "# Изменения\n\n```json\n" + json.dumps(payload["changes"], ensure_ascii=False, indent=2) + "\n```\n")
    write_html(destination, payload, claims, papers)
    return destination


def write_html(destination: Path, payload: dict, claims: dict, papers: dict, public=False):
    esc = lambda value: html.escape(str(value), quote=True)
    def item(statement):
        label = "<strong>Редакторское предложение.</strong> " if statement["kind"] == "editorial" else ""
        refs = " ".join(f'<a class="ref" href="#{esc(cid)}">{esc(papers[claims[cid]["paper_id"]]["title"])}</a>' for cid in statement["claim_ids"])
        return f"<p>{label}{esc(statement['text'])} {refs}</p>"
    parts = ["<!doctype html><html lang='ru'><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>",
             "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\">",
             "<title>" + esc(payload["synthesis"]["title"]) + "</title>",
             "<style>body{margin:0;background:#f6f5f1;color:#202a31;font:17px/1.65 system-ui,sans-serif}main{max-width:960px;margin:auto;padding:48px 24px}h1{font-size:2.5em;line-height:1.15}h2{margin-top:2.5em}a{color:#176a70}.ref{font-size:.76em}nav{display:flex;gap:16px;flex-wrap:wrap}details{background:white;border:1px solid #dbe0dd;border-radius:8px;padding:16px;margin:12px 0}summary{cursor:pointer;font-weight:650}small,.muted{color:#5c676d}.badge{display:inline-block;background:#ffe5aa;border-radius:4px;padding:3px 9px}article{border-top:1px solid #ddd;padding:12px 0;scroll-margin-top:20px}blockquote{border-left:3px solid #c4d6cf;padding-left:16px}code{overflow-wrap:anywhere}footer{margin-top:50px;border-top:1px solid #ccc}</style><main>",
             "<span class='badge'>" + ("Редакционный выпуск · ограничения указаны ниже" if public else "Черновик · научная проверка требуется") + "</span><h1>" + esc(payload["synthesis"]["title"]) + "</h1>",
             "<p class='muted'>" + esc(payload["at"]) + " · " + str(len(papers)) + " работ</p>",
             "<nav><a href='#review'>Обзор</a><a href='#concepts'>Понятия</a><a href='#papers'>Статьи и доказательства</a>" + ("" if public else "<a href='review.json'>JSON</a><a href='graph.json'>Граф</a>") + "<a href='references.bib'>BibTeX</a></nav>",
             "<h2>Границы и вопросы</h2><p>" + esc(payload["review"]["topic"]) + "</p><ul>"]
    parts += ["<li>" + esc(q) + "</li>" for q in payload["review"]["questions"]]
    parts += ["</ul><section id='review'>"]
    for section in payload["synthesis"]["sections"]:
        parts.append("<h2>" + esc(section["title"]) + "</h2>")
        parts.extend(item(s) for s in section["items"])
        if section['key'] == 'comparison' and payload.get('comparison'):
            parts.append('<div style="overflow-x:auto"><table><thead><tr><th>Работа</th><th>Механизм</th><th>Гарантия</th><th>Условия</th></tr></thead><tbody>')
            for row in payload['comparison']:
                refs = ' '.join('<a href="#' + esc(cid) + '">источник</a>' for cid in row['claim_ids'])
                parts.append('<tr><td>' + esc(papers[row['paper_id']]['title']) + ' ' + refs + '</td>' + ''.join('<td>' + esc(row[key]) + '</td>' for key in ('method', 'guarantee', 'setup')) + '</tr>')
            parts.append('</tbody></table></div>')
    parts.append("</section><section id='concepts'><h2>Понятия</h2>")
    for concept in payload["synthesis"]["concepts"]:
        parts.append("<details><summary>" + esc(concept["name"]) + "</summary>")
        parts.extend(item(s) for s in [concept["definition"], *concept["statements"], *concept["disagreements"]])
        parts.append("</details>")
    parts.append("</section><section id='papers'><h2>Статьи и доказательства</h2>")
    for card in payload["cards"]:
        paper = papers[card["paper_id"]]
        parts += ["<h3>" + esc(paper["title"]) + "</h3>", "<p class='muted'>" + esc(card["coverage"]) + "</p>"]
        remaining = payload["config"]["limits"]["export_quote_words_per_paper"]
        for claim in card["claims"]:
            parts.append(f'<article id="{esc(claim["id"])}"><strong>{esc(claim["kind"])}</strong><p>{esc(claim["text"])}</p>')
            for key, label in (("conditions", "Условия"), ("limitations", "Авторские ограничения")):
                parts.append("<p><small>" + label + ": " + esc("; ".join(claim[key]) or "не извлечены; требуется сверка") + "</small></p>")
            for ev in claim["evidence"]:
                words = len(ev["quote"].split())
                if words <= remaining:
                    parts.append("<blockquote>" + esc(ev["quote"]) + "</blockquote>")
                    remaining -= words
                if payload.get('kind') == 'editorial_review':
                    parts.append('<small>Источник: <a rel="noreferrer" href="' + esc(web_link(ev['url'])) + '">' + esc(ev['locator']) + '</a></small>')
                    continue
                document = payload["documents"][card["paper_id"]]
                segment = next(s for s in document["segments"] if s["id"] == ev["segment_id"])
                evidence_url = web_link(document["source"].get("url", ""))
                if evidence_url and segment["locator"].startswith("PDF page "):
                    evidence_url = evidence_url.split("#")[0] + "#page=" + segment["locator"].split()[-1]
                    parts.append(f'<small>Локатор: <a rel="noreferrer" href="{esc(evidence_url)}">{esc(segment["locator"] + ", " + ev["segment_id"])}</a></small>')
                else:
                    parts.append("<small>Локатор: " + esc(segment["locator"] + ", " + ev["segment_id"]) + "</small>")
            source_url = web_link(payload.get('documents', {}).get(card['paper_id'], {}).get('source', {}).get('url') or paper.get("url", ""))
            if source_url:
                parts.append(f'<p><a rel="noreferrer" href="{esc(source_url)}">Первоисточник</a></p>')
            parts.append("</article>")
    parts.append("</section><footer><h2>Ограничения</h2>")
    parts.extend("<p>" + esc(w) + "</p>" for w in payload["quality"]["warnings"])
    parts.append("</footer></main></html>")
    write_text(destination / "index.html", "\n".join(parts))
