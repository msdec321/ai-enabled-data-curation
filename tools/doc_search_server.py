"""AutoDQA — Documentation Search MCP Server

Search and read documentation markdown files from the ETL repository.
Covers PCORNet CDM+ docs, Clarity view docs, Allscripts/Centricity docs,
and other project documentation.
"""

import json
import os
import subprocess
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("doc_search")

_etl_repo = None
_doc_dirs = [
    "Documentation",
    "docs",
]


def _get_etl_repo() -> Path:
    global _etl_repo
    if _etl_repo is None:
        repo_path = os.environ.get("ETL_REPO")
        if not repo_path:
            raise RuntimeError("ETL_REPO environment variable not set")
        _etl_repo = Path(repo_path)
    return _etl_repo


def _get_doc_paths() -> list[Path]:
    """Return all documentation directories that exist."""
    repo = _get_etl_repo()
    paths = []
    for d in _doc_dirs:
        full = repo / d
        if full.is_dir():
            paths.append(full)
    return paths


@mcp.tool()
def search_docs(query: str, max_results: int = 30) -> str:
    """Search documentation files for a keyword or phrase.

    Searches all markdown files in the ETL repo's Documentation/ directory
    for the given query string (case-insensitive).

    Args:
        query: Search string — table name, column name, source system,
               or any keyword.
        max_results: Maximum matching lines to return (default: 30).

    Returns:
        JSON with matching file paths, line numbers, and content.
    """
    repo = _get_etl_repo()
    doc_paths = _get_doc_paths()

    if not doc_paths:
        return json.dumps({"error": "No documentation directories found in ETL repo"})

    matches = []
    for doc_dir in doc_paths:
        try:
            result = subprocess.run(
                [
                    "grep", "-rni", "--include", "*.md",
                    "-m", str(max_results),
                    query, str(doc_dir),
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            continue

        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split(":", 2)
            if len(parts) >= 3:
                filepath, lineno, content = parts[0], parts[1], parts[2]
                rel_path = os.path.relpath(filepath, str(repo))
                matches.append({
                    "file": rel_path,
                    "line": int(lineno),
                    "content": content.strip(),
                })

    matches = matches[:max_results]

    return json.dumps({
        "query": query,
        "match_count": len(matches),
        "matches": matches,
    })


@mcp.tool()
def read_doc(path: str) -> str:
    """Read a documentation markdown file.

    Args:
        path: Relative path within the ETL repo (e.g.,
              "Documentation/PCORNet_CDM+/DEMOGRAPHIC.md") or absolute path.

    Returns:
        JSON with file contents.
    """
    repo = _get_etl_repo()

    if os.path.isabs(path):
        full_path = Path(path)
    else:
        full_path = repo / path

    if not full_path.is_file():
        return json.dumps({"error": f"File not found: {path}"})

    try:
        resolved = full_path.resolve()
        repo_resolved = repo.resolve()
        if not str(resolved).startswith(str(repo_resolved)):
            return json.dumps({"error": "Path is outside the ETL repository"})
    except Exception:
        return json.dumps({"error": "Invalid path"})

    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return json.dumps({"error": f"Failed to read file: {str(e)}"})

    return json.dumps({
        "file": str(os.path.relpath(full_path, str(repo))),
        "content": content,
    })


@mcp.tool()
def list_docs(directory: str = "") -> str:
    """List documentation files available in the ETL repo.

    Args:
        directory: Subdirectory to list (e.g., "Documentation/PCORNet_CDM+").
                   Default: lists all documentation directories.

    Returns:
        JSON with list of documentation files.
    """
    repo = _get_etl_repo()

    if directory:
        target = repo / directory
        if not target.is_dir():
            return json.dumps({"error": f"Directory not found: {directory}"})
        files = sorted(
            str(f.relative_to(repo))
            for f in target.rglob("*.md")
        )
        return json.dumps({
            "directory": directory,
            "file_count": len(files),
            "files": files,
        })

    all_files = {}
    for doc_dir in _get_doc_paths():
        rel_dir = str(doc_dir.relative_to(repo))
        files = sorted(
            str(f.relative_to(repo))
            for f in doc_dir.rglob("*.md")
        )
        all_files[rel_dir] = files

    return json.dumps({
        "doc_directories": all_files,
        "total_files": sum(len(v) for v in all_files.values()),
    })


if __name__ == "__main__":
    mcp.run()
