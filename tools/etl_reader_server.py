"""AutoDQA — ETL Reader MCP Server

Search and read SQL files from the ETL codebase. Provides grep-like search
and file reading capabilities scoped to the ETL repository.
"""

import json
import os
import re
import subprocess
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("etl_reader")

_etl_repo = None


def _get_etl_repo() -> Path:
    global _etl_repo
    if _etl_repo is None:
        repo_path = os.environ.get("ETL_REPO")
        if not repo_path:
            raise RuntimeError("ETL_REPO environment variable not set")
        _etl_repo = Path(repo_path)
        if not _etl_repo.is_dir():
            raise RuntimeError(f"ETL_REPO path does not exist: {repo_path}")
    return _etl_repo


@mcp.tool()
def search_etl(query: str, file_pattern: str = "*.sql", max_results: int = 50) -> str:
    """Search ETL SQL files for a string pattern.

    Args:
        query: Search string (case-insensitive). Can be a table name, column
               name, function name, or any SQL fragment.
        file_pattern: Glob pattern to filter files (default: *.sql).
        max_results: Maximum number of matching lines to return (default: 50).

    Returns:
        JSON with matching file paths, line numbers, and line content.
    """
    repo = _get_etl_repo()

    try:
        result = subprocess.run(
            [
                "grep", "-rni", "--include", file_pattern,
                "-m", str(max_results),
                query, str(repo),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "Search timed out after 30 seconds"})

    matches = []
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

    return json.dumps({
        "query": query,
        "match_count": len(matches),
        "truncated": len(matches) >= max_results,
        "matches": matches,
    })


@mcp.tool()
def read_etl_file(path: str, start_line: int = 0, max_lines: int = 500) -> str:
    """Read the contents of an ETL SQL file.

    Args:
        path: Relative path within the ETL repo (e.g.,
              "etl/tables/etl.DEMOGRAPHIC_EPIC.View.sql") or absolute path.
        start_line: Line number to start reading from (0-based, default: 0).
        max_lines: Maximum lines to return (default: 500).

    Returns:
        JSON with file contents and metadata.
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
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except Exception as e:
        return json.dumps({"error": f"Failed to read file: {str(e)}"})

    total_lines = len(all_lines)
    selected = all_lines[start_line:start_line + max_lines]
    content = "".join(selected)

    return json.dumps({
        "file": str(os.path.relpath(full_path, str(repo))),
        "total_lines": total_lines,
        "start_line": start_line,
        "lines_returned": len(selected),
        "truncated": start_line + max_lines < total_lines,
        "content": content,
    })


@mcp.tool()
def list_etl_files(directory: str = "", pattern: str = "*.sql") -> str:
    """List SQL files in a directory of the ETL repo.

    Args:
        directory: Relative directory path (default: repo root).
                   Common values: "etl/tables", "etl/procedures",
                   "etl/functions", "etl/views", "etl/quality_checks".
        pattern: Glob pattern (default: *.sql).

    Returns:
        JSON with list of file paths relative to the ETL repo.
    """
    repo = _get_etl_repo()
    target = repo / directory if directory else repo

    if not target.is_dir():
        return json.dumps({"error": f"Directory not found: {directory}"})

    files = sorted(str(f.relative_to(repo)) for f in target.glob(pattern))

    return json.dumps({
        "directory": directory or ".",
        "pattern": pattern,
        "file_count": len(files),
        "files": files,
    })


if __name__ == "__main__":
    mcp.run()
