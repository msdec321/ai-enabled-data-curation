"""
Stream Viewer — Pretty-prints Claude Code stream-json output
=============================================================
Reads streaming JSON events from stdin and displays a clean,
readable progress log. Pairs with `claude -p --output-format stream-json`.

Usage:
    claude -p --output-format stream-json ... 2>&1 | python3 tools/stream_viewer.py
    claude -p --output-format stream-json ... 2>&1 | python3 tools/stream_viewer.py --label "Worker"
"""

import argparse
import json
import sys
import textwrap

DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
RESET = "\033[0m"


def format_tool_use(tool_name: str) -> str:
    short_name = (
        tool_name
        .replace("mcp__sql_executor__", "sql:")
        .replace("mcp__etl_reader__", "etl:")
        .replace("mcp__spec_query__", "spec:")
        .replace("mcp__doc_search__", "doc:")
    )
    return f"{CYAN}[tool] {short_name}{RESET}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="", help="Label prefix for this agent level")
    args = parser.parse_args()

    prefix = f"{MAGENTA}[{args.label}]{RESET} " if args.label else ""
    turn_count = 0

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            print(f"{prefix}{line}")
            sys.stdout.flush()
            continue

        event_type = event.get("type", "")

        if event_type == "assistant":
            turn_count += 1
            message = event.get("message", {})
            for block in message.get("content", []):
                block_type = block.get("type", "")

                if block_type == "text":
                    text = block.get("text", "")
                    if text.strip():
                        print(f"\n{prefix}{BOLD}[Turn {turn_count}]{RESET}")
                        for para in text.split("\n"):
                            if para.strip():
                                wrapped = textwrap.fill(
                                    para.strip(), width=88,
                                    initial_indent=f"{prefix}  ",
                                    subsequent_indent=f"{prefix}  ",
                                )
                                print(wrapped)

                elif block_type == "tool_use":
                    tool_name = block.get("name", "unknown")
                    tool_input = block.get("input", {})
                    label = format_tool_use(tool_name)

                    if "execute_sql" in tool_name:
                        query = tool_input.get("query", "")
                        print(f"{prefix}  {label} {DIM}{query[:100]}{RESET}")
                    elif "search_etl" in tool_name:
                        query = tool_input.get("query", "")
                        print(f"{prefix}  {label} query={DIM}{query[:80]}{RESET}")
                    elif "read_etl_file" in tool_name:
                        path = tool_input.get("path", "")
                        print(f"{prefix}  {label} {path}")
                    elif "search_docs" in tool_name:
                        query = tool_input.get("query", "")
                        print(f"{prefix}  {label} query={DIM}{query[:80]}{RESET}")
                    elif tool_name == "Write":
                        path = tool_input.get("file_path", "")
                        print(f"{prefix}  {label} {GREEN}-> {path}{RESET}")
                    elif tool_name == "Read":
                        path = tool_input.get("file_path", "")
                        print(f"{prefix}  {label} <- {path}")
                    elif tool_name == "Bash":
                        cmd = tool_input.get("command", "")
                        if "claude -p" in cmd:
                            print(f"\n{prefix}  {YELLOW}{BOLD}> Launching sub-agent...{RESET}")
                        else:
                            print(f"{prefix}  {label} $ {DIM}{cmd[:100]}{RESET}")
                    else:
                        print(f"{prefix}  {label}")

        elif event_type == "result":
            result_text = event.get("result", "")
            if result_text:
                print(f"\n{prefix}{GREEN}{BOLD}=== AGENT COMPLETE ==={RESET}")
                lines = result_text.strip().split("\n")
                for l in lines[:10]:
                    print(f"{prefix}  {l}")
                if len(lines) > 10:
                    print(f"{prefix}  {DIM}... ({len(lines)} lines total){RESET}")

        elif event_type == "error":
            error = event.get("error", {})
            msg = error.get("message", str(error))
            print(f"{prefix}  {RED}Error: {msg}{RESET}")

        sys.stdout.flush()


if __name__ == "__main__":
    main()
