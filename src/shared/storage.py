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
