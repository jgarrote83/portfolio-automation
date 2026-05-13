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
