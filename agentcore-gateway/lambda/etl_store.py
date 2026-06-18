"""Read-only access to the bundled synthetic ETL corpus (SQL + docs).

Tier-0/1: the corpus is static synthetic text bundled into the Lambda package
(see deploy_lambda in setup_gateway.py), so search_etl/read_etl_file need neither
the sandbox nor a DB credential — they read files directly. For the real
institutional ETL repo this moves in-network (the executor reaches the repo
itself), but the tool contract here stays identical.
"""
import json
import os
from pathlib import Path

# Resolve the corpus root for both the Lambda (flat zip: etl_corpus/ sits next to
# the .py files) and local runs (repo layout: ../etl_corpus relative to lambda/).
_CANDIDATES = [
    os.environ.get("ETL_CORPUS_DIR"),
    str(Path(__file__).resolve().parent / "etl_corpus"),
    str(Path(__file__).resolve().parent.parent / "etl_corpus"),
]
CORPUS_ROOT = Path(
    next((p for p in _CANDIDATES if p and Path(p).is_dir()),
         str(Path(__file__).resolve().parent / "etl_corpus"))
).resolve()

# What the tools search over: SQL is the ETL code, md is the CDM documentation.
_SEARCHABLE = {".sql", ".md"}


def _searchable_files():
    return sorted(p for p in CORPUS_ROOT.rglob("*")
                  if p.is_file() and p.suffix.lower() in _SEARCHABLE)


def search(query: str, max_results: int = 40) -> str:
    """Case-insensitive substring search over the corpus; JSON with file/line/content."""
    q = (query or "").strip()
    if not q:
        return json.dumps({"error": "empty query"})
    needle = q.lower()
    matches = []
    for f in _searchable_files():
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, start=1):
            if needle in line.lower():
                matches.append({"file": f.relative_to(CORPUS_ROOT).as_posix(),
                                "line": i, "content": line.strip()})
                if len(matches) >= max_results:
                    return json.dumps({"query": q, "match_count": len(matches),
                                       "truncated": True, "matches": matches})
    return json.dumps({"query": q, "match_count": len(matches),
                       "truncated": False, "matches": matches})


def read_file(path: str, start_line: int = 0, max_lines: int = 400) -> str:
    """Read a corpus file, scoped to CORPUS_ROOT (no path traversal); JSON + metadata."""
    rel = (path or "").strip()
    if not rel:
        return json.dumps({"error": "no path provided"})
    full = (CORPUS_ROOT / rel).resolve()
    # Path-scoping: the resolved target must be the root itself or live under it.
    if full != CORPUS_ROOT and CORPUS_ROOT not in full.parents:
        return json.dumps({"error": "path is outside the ETL corpus"})
    if not full.is_file():
        return json.dumps({"error": f"file not found: {rel}"})
    lines = full.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    start = max(0, int(start_line))
    sel = lines[start:start + int(max_lines)]
    return json.dumps({"file": full.relative_to(CORPUS_ROOT).as_posix(),
                       "total_lines": len(lines), "start_line": start,
                       "lines_returned": len(sel),
                       "truncated": start + int(max_lines) < len(lines),
                       "content": "".join(sel)})
