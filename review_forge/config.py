from __future__ import annotations

import os
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from .util import ReviewError, digest


def load_env(path: Path):
    """Small dotenv subset: NAME=value; no expansion, shell commands or multiline values."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.strip().isidentifier():
            raise ReviewError(f"Invalid .env entry in {path}; expected NAME=value.")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def validate_model(model: dict):
    if not model.get("name") or not model.get("endpoint"):
        raise ReviewError("Set model.name and model.endpoint in review.toml before using the model.")
    url = urlsplit(model["endpoint"])
    local = url.hostname in {"localhost", "127.0.0.1", "::1"}
    if (url.scheme != "https" and not (url.scheme == "http" and local)) or not url.netloc:
        raise ReviewError("Model endpoint must use HTTPS, or HTTP on localhost.")
    if url.username or url.password or url.query or url.fragment:
        raise ReviewError("Keep credentials out of endpoint URLs. Use the named environment variable.")
    if model.get("token_parameter") not in {"max_tokens", "max_completion_tokens"}:
        raise ReviewError("token_parameter must be max_tokens or max_completion_tokens.")
    if not isinstance(model.get("max_output_tokens"), int) or model["max_output_tokens"] < 256:
        raise ReviewError("max_output_tokens must be an integer >= 256.")
    if model.get("require_api_key", True) and not os.environ.get(model.get("api_key_env", "REVIEW_API_KEY")):
        raise ReviewError("Missing API key. Fill the environment variable named by model.api_key_env.")


def load_config(root: Path) -> dict:
    path = root / "review.toml"
    if not path.is_file():
        raise ReviewError(f"No review.toml in {root}. Run init first.")
    with path.open("rb") as stream:
        config = tomllib.load(stream)
    shared = root.parent / "model.toml"
    if shared.is_file():
        with shared.open("rb") as stream:
            shared_config = tomllib.load(stream)
        if not isinstance(shared_config.get("model"), dict):
            raise ReviewError("Shared model.toml requires a [model] table.")
        config["model"] = {**config.get("model", {}), **shared_config["model"]}
    for section in ("review", "search", "documents", "model", "limits", "updates"):
        if not isinstance(config.get(section), dict):
            raise ReviewError(f"Missing [{section}] section in review.toml.")
    review, search, docs, limits = (config[k] for k in ("review", "search", "documents", "limits"))
    if not isinstance(review.get("topic"), str) or not review["topic"].strip() or review["topic"] == "REPLACE_WITH_TOPIC":
        raise ReviewError("Set a nonempty review.topic.")
    for key in ("questions", "include", "exclude"):
        if not isinstance(review.get(key), list) or not all(isinstance(x, str) and x.strip() for x in review[key]):
            raise ReviewError(f"review.{key} must be an array of nonempty strings.")
    if not review["questions"] or not review["include"]:
        raise ReviewError("State at least one research question and inclusion criterion.")
    for obj, key, low, high in ((review,"year_from",1900,2200), (review,"year_to",1900,2200),
                              (review,"min_papers",1,500), (search,"per_query",1,100),
                              (search,"max_candidates",1,1000), (search,"max_papers",1,500),
                              (docs,"chunk_chars",1000,100000), (docs,"min_fulltext_chars",100,100000),
                              (limits,"max_model_calls",1,10000), (limits,"max_input_chars",5000,2000000),
                              (limits,"timeout_seconds",1,600), (limits,"claims_per_chunk",1,20),
                              (limits,"synthesis_claims_per_paper",1,50), (limits,"quote_max_words",5,100),
                              (limits,"export_quote_words_per_paper",0,1000)):
        if type(obj.get(key)) is not int or not low <= obj[key] <= high:
            raise ReviewError(f"{key} must be an integer from {low} to {high}.")
    if review["year_from"] > review["year_to"] or review["min_papers"] > search["max_papers"]:
        raise ReviewError("Invalid year range or min_papers > max_papers.")
    if not isinstance(search.get("providers"), list) or not set(search["providers"]) <= {"arxiv", "semantic_scholar"}:
        raise ReviewError("Supported search providers: arxiv, semantic_scholar.")
    if not isinstance(search.get("queries"), list) or not all(isinstance(q, str) and q.strip() for q in search["queries"]):
        raise ReviewError("search.queries must contain nonempty strings.")
    if not search.get("arxiv_sorts") or not set(search["arxiv_sorts"]) <= {"relevance", "submittedDate", "lastUpdatedDate"}:
        raise ReviewError("Invalid search.arxiv_sorts.")
    if type(docs.get("allow_abstract_only")) is not bool:
        raise ReviewError("documents.allow_abstract_only must be true or false.")
    return config


def config_hash(config: dict) -> str:
    # Config stores environment variable names only, never environment values.
    return digest(config)
