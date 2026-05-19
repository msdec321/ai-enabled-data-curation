"""AutoDQA — Documentation Search MCP Server

Search and read documentation markdown files from configured documentation
directories. Covers PCORNet CDM+ docs, Clarity view docs, Allscripts/Centricity
docs, and other project documentation.
"""

import json
import os
import subprocess
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("doc_search")

_doc_paths = None


def _get_doc_paths() -> list[Path]:
    """Return all configured documentation directories that exist."""
    global _doc_paths
    if _doc_paths is None:
        raw = os.environ.get("DOC_PATHS")
        if not raw:
            raise RuntimeError("DOC_PATHS environment variable not set")
        _doc_paths = [Path(p) for p in json.loads(raw) if Path(p).is_dir()]
    return _doc_paths


@mcp.tool()
def search_docs(query: str, max_results: int = 30) -> str:
    """Search documentation files for a keyword or phrase.

    Searches all markdown files in the configured documentation directories
    for the given query string (case-insensitive).

    Args:
        query: Search string — table name, column name, source system,
               or any keyword.
        max_results: Maximum matching lines to return (default: 30).

    Returns:
        JSON with matching file paths, line numbers, and content.
    """
    doc_paths = _get_doc_paths()

    if not doc_paths:
        return json.dumps({"error": "No documentation directories found"})

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
                rel_path = os.path.relpath(filepath, str(doc_dir))
                matches.append({
                    "file": f"{doc_dir.name}/{rel_path}",
                    "line": int(lineno),
                    "content": content.strip(),
                })

    matches = matches[:max_results]

    return json.dumps({
        "query": query,
        "match_count": len(matches),
        "matches": matches,
    })


def _resolve_doc_path(path: str) -> tuple[Path | None, Path | None]:
    """Resolve a doc path and find which doc root it belongs to.

    Returns (full_path, doc_root) or (None, None) if not found/allowed.
    """
    doc_paths = _get_doc_paths()

    if os.path.isabs(path):
        full_path = Path(path)
    else:
        for doc_dir in doc_paths:
            candidate = doc_dir / path
            if candidate.is_file():
                full_path = candidate
                break
        else:
            return None, None

    try:
        resolved = full_path.resolve()
        for doc_dir in doc_paths:
            if str(resolved).startswith(str(doc_dir.resolve())):
                return full_path, doc_dir
    except Exception:
        pass
    return None, None


@mcp.tool()
def read_doc(path: str) -> str:
    """Read a documentation markdown file.

    Args:
        path: Relative path within a documentation directory (e.g.,
              "PCORNet_CDM+/DEMOGRAPHIC.md") or absolute path.

    Returns:
        JSON with file contents.
    """
    full_path, doc_root = _resolve_doc_path(path)

    if full_path is None:
        return json.dumps({"error": f"File not found or outside documentation directories: {path}"})

    if not full_path.is_file():
        return json.dumps({"error": f"File not found: {path}"})

    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return json.dumps({"error": f"Failed to read file: {str(e)}"})

    return json.dumps({
        "file": f"{doc_root.name}/{os.path.relpath(full_path, str(doc_root))}",
        "content": content,
    })


@mcp.tool()
def list_docs(directory: str = "") -> str:
    """List documentation files available in the configured doc directories.

    Args:
        directory: Subdirectory to list (e.g., "PCORNet_CDM+").
                   Default: lists all documentation directories.

    Returns:
        JSON with list of documentation files.
    """
    doc_paths = _get_doc_paths()

    if directory:
        for doc_dir in doc_paths:
            target = doc_dir / directory
            if target.is_dir():
                files = sorted(
                    f"{doc_dir.name}/{f.relative_to(doc_dir)}"
                    for f in target.rglob("*.md")
                )
                return json.dumps({
                    "directory": directory,
                    "file_count": len(files),
                    "files": files,
                })
        return json.dumps({"error": f"Directory not found: {directory}"})

    all_files = {}
    for doc_dir in doc_paths:
        files = sorted(
            f"{doc_dir.name}/{f.relative_to(doc_dir)}"
            for f in doc_dir.rglob("*.md")
        )
        all_files[str(doc_dir)] = files

    return json.dumps({
        "doc_directories": all_files,
        "total_files": sum(len(v) for v in all_files.values()),
    })


if __name__ == "__main__":
    mcp.run()
