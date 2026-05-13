import logging

logger = logging.getLogger(__name__)

_PROD_BASE = "https://api.etrade.com/v1"


class ETradeClient:
    """E*TRADE OAuth 1.0a client. Gracefully degrades when credentials are absent."""

    def __init__(
        self,
        consumer_key: str | None,
        consumer_secret: str | None,
        access_token: str | None,
        access_token_secret: str | None,
    ):
        self.ready = all([consumer_key, consumer_secret, access_token, access_token_secret])
        if self.ready:
            from requests_oauthlib import OAuth1Session
            self._session = OAuth1Session(
                consumer_key,
                client_secret=consumer_secret,
                resource_owner_key=access_token,
                resource_owner_secret=access_token_secret,
            )
        else:
            logger.warning("E*TRADE credentials not in Key Vault — collector will use fallback portfolio")

    # ------------------------------------------------------------------ #
    # Public methods                                                       #
    # ------------------------------------------------------------------ #

    def get_portfolio(self) -> list[dict] | None:
        if not self.ready:
            return None
        account_key = self._get_first_account_key()
        if not account_key:
            return []
        try:
            r = self._session.get(
                f"{_PROD_BASE}/accounts/{account_key}/portfolio",
                params={"view": "COMPLETE"},
                timeout=30,
            )
            r.raise_for_status()
            return self._parse_portfolio(r.json())
        except Exception as e:
            logger.error("E*TRADE portfolio fetch failed: %s", e)
            return None

    def get_balances(self) -> dict | None:
        if not self.ready:
            return None
        account_key = self._get_first_account_key()
        if not account_key:
            return {}
        try:
            r = self._session.get(
                f"{_PROD_BASE}/accounts/{account_key}/balance",
                params={"instType": "BROKERAGE", "realTimeNAV": "true"},
                timeout=30,
            )
            r.raise_for_status()
            return r.json().get("BalanceResponse", {})
        except Exception as e:
            logger.error("E*TRADE balance fetch failed: %s", e)
            return None

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _get_first_account_key(self) -> str | None:
        try:
            r = self._session.get(f"{_PROD_BASE}/accounts/list", timeout=30)
            r.raise_for_status()
            accounts = (
                r.json()
                .get("AccountListResponse", {})
                .get("Accounts", {})
                .get("Account", [])
            )
            return accounts[0].get("accountIdKey") if accounts else None
        except Exception as e:
            logger.error("E*TRADE account list failed: %s", e)
            return None

    @staticmethod
    def _parse_portfolio(data: dict) -> list[dict]:
        positions: list[dict] = []
        for acct in data.get("PortfolioResponse", {}).get("AccountPortfolio", []):
            for pos in acct.get("Position", []):
                product = pos.get("Product", {})
                positions.append({
                    "ticker": product.get("symbol"),
                    "quantity": pos.get("quantity", 0),
                    "market_value": pos.get("marketValue", 0),
                    "cost_basis": pos.get("costBasis", 0),
                    "day_gain": pos.get("daysGain", 0),
                    "total_gain": pos.get("totalGain", 0),
                    "security_type": product.get("securityType", "EQ"),
                })
        return positions
