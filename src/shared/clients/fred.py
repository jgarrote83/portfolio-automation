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

    def get_series_vintages(
        self, series_id: str, realtime_start: str, realtime_end: str, limit: int = 1000
    ) -> list[dict]:
        """All vintage revisions of a series across a real-time window (ALFRED).

        For a nowcast like GDPNOW the observation ``date`` stays fixed to the target
        quarter while ``value`` is revised across ``realtime_start`` periods — i.e.
        this returns the *within-quarter* trajectory the standard endpoint hides
        (the standard /observations call returns one latest value per quarter).
        Each row: ``{realtime_start, realtime_end, date, value}``. Oldest-first.
        """
        try:
            r = self.session.get(
                f"{_BASE}/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": self.api_key,
                    "file_type": "json",
                    "realtime_start": realtime_start,
                    "realtime_end": realtime_end,
                    "limit": limit,
                    "sort_order": "asc",
                },
                timeout=30,
            )
            r.raise_for_status()
            return r.json().get("observations", [])
        except Exception as e:
            logger.error("FRED vintages %s failed: %s", series_id, e)
            return []

    def get_all_series(self, series_ids: list[str]) -> dict[str, list[dict]]:
        results: dict[str, list[dict]] = {}
        for sid in series_ids:
            results[sid] = self.get_series_latest(sid, limit=5)
        return results
