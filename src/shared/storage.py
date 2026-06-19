import json
import os
import logging
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from azure.data.tables import TableServiceClient

logger = logging.getLogger(__name__)

_TABLES = [
    "PortfolioHistory",
    "FundamentalsHistory",
    "MacroHistory",
    "ETFLookthroughHistory",
    "SentimentHistory",
    "TradeHistory",
]


def _account_name() -> str:
    return (
        os.environ.get("STORAGE_ACCOUNT_NAME")
        or os.environ["AzureWebJobsStorage__accountName"]
    )


def _credential() -> DefaultAzureCredential:
    return DefaultAzureCredential()


def _blob_client() -> BlobServiceClient:
    name = _account_name()
    return BlobServiceClient(
        account_url=f"https://{name}.blob.core.windows.net",
        credential=_credential(),
    )


def _table_service() -> TableServiceClient:
    name = _account_name()
    return TableServiceClient(
        endpoint=f"https://{name}.table.core.windows.net",
        credential=_credential(),
    )


def ensure_tables() -> None:
    svc = _table_service()
    for table in _TABLES:
        try:
            svc.create_table_if_not_exists(table)
        except Exception as e:
            logger.warning("Could not ensure table %s: %s", table, e)


def write_snapshot(date_str: str, snapshot: dict) -> None:
    client = _blob_client()
    blob = client.get_blob_client("daily-snapshots", f"{date_str}.json")
    data = json.dumps(snapshot, default=str, indent=2)
    blob.upload_blob(data, overwrite=True)
    logger.info("Snapshot written: daily-snapshots/%s.json (%d bytes)", date_str, len(data))


def upsert_entity(table_name: str, entity: dict) -> None:
    svc = _table_service()
    table = svc.get_table_client(table_name)
    try:
        table.upsert_entity(entity)
    except Exception as e:
        logger.error("Table upsert failed (%s / %s-%s): %s",
                     table_name, entity.get("PartitionKey"), entity.get("RowKey"), e)


def query_entities(table_name: str, query_filter: str | None = None) -> list[dict]:
    """Return entities from a table as plain dicts (optionally OData-filtered).

    Best-effort: returns [] on error so callers (e.g. Phase C outcome stamping)
    never die over a read. `query_filter` is an OData string, e.g.
    "recommended_at le '2026-05-19'".
    """
    svc = _table_service()
    table = svc.get_table_client(table_name)
    try:
        it = table.query_entities(query_filter) if query_filter else table.list_entities()
        return [dict(e) for e in it]
    except Exception as e:
        logger.error("Table query failed (%s / %s): %s", table_name, query_filter, e)
        return []


def read_snapshot(date_str: str) -> dict:
    """Load a daily snapshot JSON from blob storage."""
    client = _blob_client()
    blob = client.get_blob_client("daily-snapshots", f"{date_str}.json")
    raw = blob.download_blob().readall()
    return json.loads(raw)


def read_blob_bytes(container: str, name: str) -> bytes:
    client = _blob_client()
    blob = client.get_blob_client(container, name)
    return blob.download_blob().readall()


def list_recent_reports(limit: int = 5) -> list[tuple[str, str]]:
    """Return up to `limit` most recent (date, markdown) pairs from daily-reports.

    Reports are blobs named `YYYY-MM-DD.md`. Sorted by name descending (date desc).
    """
    client = _blob_client()
    container = client.get_container_client("daily-reports")
    names: list[str] = []
    try:
        for b in container.list_blobs():
            if b.name.endswith(".md"):
                names.append(b.name)
    except Exception as e:
        logger.warning("Could not list daily-reports: %s", e)
        return []

    names.sort(reverse=True)
    out: list[tuple[str, str]] = []
    for n in names[:limit]:
        try:
            md = container.get_blob_client(n).download_blob().readall().decode("utf-8")
            out.append((n.replace(".md", ""), md))
        except Exception as e:
            logger.warning("Could not read report %s: %s", n, e)
    return out


def write_report(date_str: str, markdown: str) -> None:
    client = _blob_client()
    blob = client.get_blob_client("daily-reports", f"{date_str}.md")
    blob.upload_blob(markdown.encode("utf-8"), overwrite=True)
    logger.info("Report written: daily-reports/%s.md (%d bytes)", date_str, len(markdown))


def write_trades(date_str: str, trades: dict | list) -> None:
    client = _blob_client()
    blob = client.get_blob_client("daily-trades", f"{date_str}.json")
    data = json.dumps(trades, default=str, indent=2)
    blob.upload_blob(data, overwrite=True)
    logger.info("Trades written: daily-trades/%s.json (%d bytes)", date_str, len(data))


def write_debug_raw(date_str: str, raw: str) -> None:
    """Persist the raw Claude response for forensics when parsing fails."""
    client = _blob_client()
    blob = client.get_blob_client("daily-reports", f"_debug/{date_str}-raw.txt")
    blob.upload_blob(raw.encode("utf-8"), overwrite=True)
    logger.info(
        "Debug raw response written: daily-reports/_debug/%s-raw.txt (%d bytes)",
        date_str, len(raw),
    )


def _read_json_blob(container: str, name: str) -> dict | list | None:
    """Best-effort blob read; returns None if blob is missing."""
    client = _blob_client()
    blob = client.get_blob_client(container, name)
    try:
        raw = blob.download_blob().readall()
    except Exception as e:
        logger.info("Blob %s/%s not found: %s", container, name, e)
        return None
    if not raw:
        return None
    return json.loads(raw)


def read_trades(date_str: str) -> dict | list | None:
    return _read_json_blob("daily-trades", f"{date_str}.json")


def read_approvals(date_str: str) -> dict | None:
    """Load approvals/{date}.json written by the SWA managed API."""
    data = _read_json_blob("approvals", f"{date_str}.json")
    if data is None or isinstance(data, list):
        return data if isinstance(data, dict) else None
    return data


def read_executions(date_str: str) -> dict | None:
    data = _read_json_blob("daily-executions", f"{date_str}.json")
    return data if isinstance(data, dict) else None


def write_executions(date_str: str, executions: dict) -> None:
    client = _blob_client()
    container = client.get_container_client("daily-executions")
    try:
        container.create_container()
    except Exception:
        pass
    blob = client.get_blob_client("daily-executions", f"{date_str}.json")
    data = json.dumps(executions, default=str, indent=2)
    blob.upload_blob(data, overwrite=True)
    logger.info(
        "Executions written: daily-executions/%s.json (%d bytes)",
        date_str, len(data),
    )
