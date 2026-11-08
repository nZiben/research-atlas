from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import re
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version


class ReviewError(Exception):
    """An actionable failure, safe to display without provider response bodies."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value) -> str:
    data = value if isinstance(value, bytes) else canonical(value).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_text(path: Path, text: str):
    write_bytes(path, text.encode("utf-8"))


def write_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_json(path: Path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def slug(text: str) -> str:
    short = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:65].rstrip("-")
    return (short or "topic") + "-" + digest(text)[:8]


def safe_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,100}", value):
        raise ReviewError("Invalid identifier; use lowercase ASCII letters, numbers and hyphens.")
    return value


def runtime_inventory() -> dict:
    try:
        pdf_version = version("pypdf")
    except PackageNotFoundError:
        pdf_version = None
    package = Path(__file__).parent
    code = {p.name: digest(p.read_bytes()) for p in sorted(package.iterdir()) if p.suffix in {".py", ".toml", ".json"}}
    return {"python": sys.version, "platform": platform.platform(), "pypdf": pdf_version,
            "implementation_sha256": digest(code), "files_sha256": code}


@contextlib.contextmanager
def locked(root: Path):
    # flock releases automatically after crashes and also protects the future UI caller.
    import fcntl

    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ReviewError("Another command is writing this topic. Wait for it to finish.") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
