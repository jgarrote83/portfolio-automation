import logging
import requests

logger = logging.getLogger(__name__)

_BASE = "https://api.stlouisfed.org/fred"


class FREDClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()

    def get_series_latest(self, series_id: str, limit: int = 5) -> list[dict]:
        try:
            r = self.session.get(
                f"{_BASE}/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": self.api_key,
                    "file_type": "json",
                    "limit": limit,
                    "sort_order": "desc",
                },
                timeout=30,
            )
            r.raise_for_status()
            return r.json().get("observations", [])
        except Exception as e:
            logger.error("FRED %s failed: %s", series_id, e)
            return []

    def get_all_series(self, series_ids: list[str]) -> dict[str, list[dict]]:
        results: dict[str, list[dict]] = {}
        for sid in series_ids:
            results[sid] = self.get_series_latest(sid, limit=5)
        return results
