"""read_etl_file tool: read a file from the bundled synthetic ETL corpus, scoped
to the corpus root. Reads static bundled text — no sandbox, no DB credential."""
import etl_store


def handle(event):
    return etl_store.read_file(
        event.get("path", ""),
        int(event.get("start_line", 0) or 0),
        int(event.get("max_lines", 400) or 400),
    )
