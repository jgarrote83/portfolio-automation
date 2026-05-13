import os
import logging
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

logger = logging.getLogger(__name__)

_SECRET_NAMES = [
    "AlpacaApiKey",
    "AlpacaApiSecret",
    "FmpApiKey",
    "FredApiKey",
    "MassiveApiKey",
    "FinnhubApiKey",
    "FoundryApiKey",
    "EtradeConsumerKey",
    "EtradeConsumerSecret",
    "EtradeAccessToken",
    "EtradeAccessTokenSecret",
]


def load_secrets() -> dict[str, str | None]:
    vault_uri = os.environ["KEY_VAULT_URI"]
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=vault_uri, credential=credential)

    secrets: dict[str, str | None] = {}
    for name in _SECRET_NAMES:
        try:
            secrets[name] = client.get_secret(name).value
        except Exception as e:
            logger.warning("Secret %s not available: %s", name, e)
            secrets[name] = None

    loaded = [k for k, v in secrets.items() if v is not None]
    logger.info("Loaded %d/%d secrets: %s", len(loaded), len(_SECRET_NAMES), loaded)
    return secrets
