from __future__ import annotations

from .util import ReviewError, normalized, safe_id

SECTION_KEYS = ("overview", "taxonomy", "timeline", "methods", "comparison", "disagreements", "gaps", "reading_path")
CLAIM_KINDS = {"empirical", "theoretical", "hypothesis", "interpretation"}


def need(condition, message: str):
    if not condition:
        raise ReviewError(message)


def obj(value, keys):
    need(isinstance(value, dict), "Expected a JSON object.")
    need(set(value) == set(keys), "Object keys do not match the requested schema.")


def text(value):
    need(isinstance(value, str) and bool(value.strip()), "Expected a nonempty string.")


def strings(value):
    need(isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value), "Expected an array of nonempty strings.")


def validate_claims(result, segments: list[dict], limits: dict):
    obj(result, ["claims"])
    need(isinstance(result["claims"], list) and len(result["claims"]) <= limits["claims_per_chunk"], "Invalid number of extracted claims.")
    lookup = {s["id"]: s["text"] for s in segments}
    for claim in result["claims"]:
        obj(claim, ["text", "kind", "conditions", "limitations", "concepts", "evidence"])
        text(claim["text"])
        need(claim["kind"] in CLAIM_KINDS, "Invalid claim kind.")
        for key in ("conditions", "limitations", "concepts"):
            strings(claim[key])
        need(isinstance(claim["evidence"], list) and 1 <= len(claim["evidence"]) <= 3, "Each claim needs 1–3 source excerpts.")
        for evidence in claim["evidence"]:
            obj(evidence, ["segment_id", "quote"])
            text(evidence["quote"])
            need(isinstance(evidence["segment_id"], str) and evidence["segment_id"] in lookup, "Unknown evidence segment.")
            quote = normalized(evidence["quote"])
            need(len(quote.split()) <= limits["quote_max_words"], "Evidence quote exceeds quote_max_words.")
            need(quote in normalized(lookup[evidence["segment_id"]]), "Evidence quote was not found in its source segment.")


def validate_screen(result, ids: set[str]):
    obj(result, ["decisions"])
    need(isinstance(result["decisions"], list), "Expected screening decisions.")
    seen = set()
    for row in result["decisions"]:
        obj(row, ["paper_id", "decision", "reason", "priority"])
        need(row["paper_id"] in ids and row["paper_id"] not in seen, "Invalid or duplicate screened paper.")
        need(row["decision"] in {"core", "external", "exclude"}, "Invalid screening decision.")
        text(row["reason"])
        need(type(row["priority"]) is int and 0 <= row["priority"] <= 100, "Priority must be an integer from 0 to 100.")
        seen.add(row["paper_id"])
    need(seen == ids, "Model omitted screening decisions.")


def validate_pick(result, ids: set[str], cap: int):
    obj(result, ["claim_ids", "reason"])
    strings(result["claim_ids"])
    need(1 <= len(result["claim_ids"]) <= cap, "Invalid number of selected claims.")
    need(len(result["claim_ids"]) == len(set(result["claim_ids"])) and set(result["claim_ids"]) <= ids, "Unknown/duplicate selected claim.")
    text(result["reason"])


def validate_statement(statement, claims: set[str], editorial=False):
    obj(statement, ["text", "claim_ids", "kind"])
    text(statement["text"])
    strings(statement["claim_ids"])
    need(len(statement["claim_ids"]) == len(set(statement["claim_ids"])), "Duplicate claim references.")
    need(set(statement["claim_ids"]) <= claims, "Statement cites a nonexistent claim.")
    need(statement["kind"] in ({"evidence", "editorial"} if editorial else {"evidence"}), "Unsupported statement kind in this section.")
    if statement["kind"] == "evidence":
        need(bool(statement["claim_ids"]), "Evidence-based statements require claim references.")


def validate_synthesis(result, claims: set[str]):
    obj(result, ["title", "sections", "concepts", "relations"])
    text(result["title"])
    need(isinstance(result["sections"], list), "Expected sections array.")
    need([s.get("key") for s in result["sections"]] == list(SECTION_KEYS), "Synthesis must include all required sections in order.")
    for section in result["sections"]:
        obj(section, ["key", "title", "items"])
        text(section["title"])
        need(isinstance(section["items"], list) and bool(section["items"]), "Every section needs at least one item; state evidence gaps explicitly.")
        for item in section["items"]:
            validate_statement(item, claims, editorial=section["key"] in {"gaps", "reading_path"})
    need(isinstance(result["concepts"], list) and bool(result["concepts"]), "At least one evidenced concept is required.")
    ids = set()
    for concept in result["concepts"]:
        obj(concept, ["id", "name", "aliases", "definition", "statements", "disagreements"])
        safe_id(concept["id"])
        need(concept["id"] != "index", "Concept ID 'index' is reserved for navigation.")
        need(concept["id"] not in ids, "Duplicate concept ID.")
        ids.add(concept["id"])
        text(concept["name"])
        strings(concept["aliases"])
        validate_statement(concept["definition"], claims)
        for key in ("statements", "disagreements"):
            need(isinstance(concept[key], list), "Expected concept statement list.")
            for statement in concept[key]:
                validate_statement(statement, claims)
    need(isinstance(result["relations"], list), "Expected concept relations.")
    for relation in result["relations"]:
        obj(relation, ["source", "target", "kind", "statement"])
        need(relation["source"] in ids and relation["target"] in ids and relation["source"] != relation["target"], "Invalid graph endpoint.")
        need(relation["kind"] in {"prerequisite", "related", "disagrees"}, "Invalid relation kind.")
        validate_statement(relation["statement"], claims)
    prerequisites = {cid: [] for cid in ids}
    for relation in result["relations"]:
        if relation["kind"] == "prerequisite":
            prerequisites[relation["source"]].append(relation["target"])
    visited, active = set(), set()
    def visit(cid):
        need(cid not in active, "Prerequisite graph contains a cycle.")
        if cid not in visited:
            active.add(cid)
            for target in prerequisites[cid]:
                visit(target)
            active.remove(cid)
            visited.add(cid)
    for cid in ids:
        visit(cid)


def validate_audit(result, claims: set[str]):
    obj(result, ["findings"])
    need(isinstance(result["findings"], list), "Expected findings array.")
    for finding in result["findings"]:
        obj(finding, ["severity", "location", "issue", "claim_ids"])
        need(finding["severity"] in {"high", "medium", "low"}, "Invalid audit severity.")
        text(finding["location"])
        text(finding["issue"])
        strings(finding["claim_ids"])
        need(set(finding["claim_ids"]) <= claims, "Audit cites an unknown claim.")
