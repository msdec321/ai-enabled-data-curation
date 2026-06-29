"""AgentCore Gateway Lambda entry point. One Lambda backs the "sandbox" target
and exposes several tools; this routes a tools/call to the right handler by the
tool name the gateway passes in the client context as "<target>___<tool>".
"""
import tool_destroy_sandbox
import tool_get_valuesets
import tool_grep_etl
import tool_list_etl
import tool_query_cdw
import tool_read_etl
import tool_read_etl_file
import tool_run_python
import tool_search_etl

TOOLS = {
    "run_python": tool_run_python.handle,
    "query_cdw": tool_query_cdw.handle,
    "search_etl": tool_search_etl.handle,
    "read_etl_file": tool_read_etl_file.handle,
    "destroy_sandbox": tool_destroy_sandbox.handle,
    "list_etl": tool_list_etl.handle,
    "read_etl": tool_read_etl.handle,
    "grep_etl": tool_grep_etl.handle,
    "get_valuesets": tool_get_valuesets.handle,
}


def _tool_name(context) -> str:
    ctx = getattr(context, "client_context", None)
    custom = getattr(ctx, "custom", None) or {}
    return custom.get("bedrockAgentCoreToolName", "").split("___")[-1]


def handler(event, context):
    fn = TOOLS.get(_tool_name(context))
    if fn is None:
        return {"error": f"unknown tool: {_tool_name(context)!r}"}
    return fn(event)
