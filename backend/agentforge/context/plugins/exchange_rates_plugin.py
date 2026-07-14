"""Exchange rate context via Frankfurter."""

from __future__ import annotations

from agentforge.config import settings
from agentforge.context.base import ContextPlugin, ContextRequest, ContextResult, PluginTiming
from agentforge.context.http_utils import fetch_json

API_URL = "https://api.frankfurter.dev/v1/latest"


class ExchangeRatesContextPlugin(ContextPlugin):
    """Latest ECB exchange rates."""

    id = "exchange_rates"
    timing = PluginTiming.JIT
    trigger_keywords = (
        "exchange rate",
        "wechselkurs",
        "currency",
        "währung",
        "usd",
        "eur",
        "dollar",
        "euro",
        "forex",
    )

    async def resolve(self, request: ContextRequest) -> ContextResult:
        """
        Fetch configured base currency rates.

        :param request: Context request payload
        :return: Exchange rate context result
        """
        base = settings.context_exchange_base.strip().upper() or "EUR"
        symbols = settings.context_exchange_symbols
        try:
            params: dict[str, str] = {"base": base}
            if symbols:
                params["symbols"] = ",".join(symbols)
            payload = await fetch_json(API_URL, params=params)
            rates = payload.get("rates") or {}
            rate_date = payload.get("date") or "unknown"
            pairs = ", ".join(f"{currency}={value}" for currency, value in sorted(rates.items())[:8])
            text = f"Exchange rates ({base}, ECB via Frankfurter, {rate_date}): {pairs or 'no rates returned'}."
            return ContextResult(
                plugin_id=self.id,
                ok=True,
                text=text,
                data={"base": base, "date": rate_date, "rates": rates},
            )
        except Exception as exc:
            return ContextResult(
                plugin_id=self.id,
                ok=False,
                text="",
                error=str(exc),
            )
