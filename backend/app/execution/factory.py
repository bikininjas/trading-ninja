"""Choix du broker selon le mode — un seul endroit, pour ne pas se tromper.

`paper` reste le défaut. `live` n'est construit que si les deux verrous de
`load_settings()` ont été posés ; `testnet` est refusé car Kraken spot n'a pas de
testnet dans CCXT 4.5.91. Un mode inconnu ne retombe jamais silencieusement sur
le papier.
"""

from __future__ import annotations

from typing import Any, Sequence

from app.core.settings import Settings
from app.exchange.kraken import resolve_taker_fee
from app.execution.live import LiveBroker
from app.execution.paper import PaperBroker

__all__ = ["make_broker"]


def make_broker(settings: Settings, exchange: Any,
                symbols: Sequence[str] | None = None,
                *, mode: str | None = None, repo: Any = None) -> Any:
    selected = (mode or settings.mode).lower()
    if selected == "live" and settings.mode != "live":
        raise RuntimeError("mode live refusé : configuration non armée")
    universe = tuple(symbols or settings.exchange.allowed_symbols)
    fees = [
        resolve_taker_fee(exchange, symbol, settings.exchange.taker_fee)
        for symbol in universe
    ]
    fee = max(fees) if fees else settings.exchange.taker_fee

    if selected == "paper":
        return PaperBroker(fee, settings.paper_slippage_bps)
    if selected == "live":
        if repo is None:raise RuntimeError("live exige un journal durable")
        broker = LiveBroker(
            exchange,
            quote_currency=settings.exchange.quote_currency,
            taker_fee=fee,
        )
        broker.bind_execution(repo,"LIVE")
        return broker
    raise RuntimeError(
        "Kraken spot n'a pas de testnet dans CCXT 4.5.91 : le mode testnet "
        "doit être redéfini avant de pouvoir exécuter."
    )
