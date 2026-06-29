"""search_etl tool: case-insensitive search over the bundled synthetic ETL
corpus (SQL + docs). Reads static bundled text — no sandbox, no DB credential."""
import etl_store


def handle(event):
    return etl_store.search(event.get("query", ""), int(event.get("max_results", 40) or 40))
