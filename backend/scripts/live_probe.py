#!/usr/bin/env python3
"""Sonde 3.1 — vérifier qu'un ordre réel rejeté ne fait pas planter le bot.

    .venv/bin/python scripts/live_probe.py --confirm-live

Envoie **un seul ordre volontairement invalide** à Kraken : une quantité sous le
minimum du marché, pour un notionnel plafonné à 0,05 EUR. L'ordre ne peut pas
être exécuté ; l'objectif est de lire l'erreur réelle de l'exchange et de vérifier
que le processus la capture proprement.

Ce script ne passe **pas** par `LiveBroker.entry` : fabriquer une `RiskDecision`
approuvée pour un ordre factice serait un mauvais précédent. Il teste le rejet
brut de l'exchange ; les tests unitaires couvrent déjà l'enrobage
`LiveExecutionError` de l'exécuteur.

Sécurités :
  - `--confirm-live` obligatoire, aucun mode par défaut ;
  - quantité construite sous `min_amount` **et** sous `min_cost`, avec un
    notionnel maximal de 0,05 EUR ;
  - si l'ordre était accepté malgré tout, il est revendu immédiatement et le
    script sort en erreur ;
  - aucune modification de réglage, aucun `.env` touché.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.settings import load_settings  # noqa: E402
from app.exchange.kraken import build_exchange  # noqa: E402

MAX_NOTIONAL = Decimal("0.05")


def _decimal(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _invalid_amount(exchange, symbol: str, price: Decimal) -> Decimal | None:
    """Plus petite quantité strictement sous les deux minimums du marché."""
    market = exchange.market(symbol)
    limits = market.get("limits") or {}
    min_amount = _decimal((limits.get("amount") or {}).get("min") or 0)
    min_cost = _decimal((limits.get("cost") or {}).get("min") or 0)

    caps = [MAX_NOTIONAL / price]
    if min_amount > 0:
        caps.append(min_amount * Decimal("0.5"))
    if min_cost > 0:
        caps.append(min_cost * Decimal("0.5") / price)
    raw = min(caps)

    try:
        formatted = exchange.amount_to_precision(symbol, raw)
        amount = Decimal(formatted)
    except (InvalidOperation, ValueError):
        return None
    if amount <= 0:
        return None
    notional = amount * price
    if min_amount > 0 and amount >= min_amount:
        return None
    if min_cost > 0 and notional >= min_cost:
        return None
    if notional > MAX_NOTIONAL:
        return None
    return amount


def _run(symbol: str | None) -> int:
    settings = load_settings()
    if not settings.exchange.api_key or not settings.exchange.secret:
        print("REFUS : aucune clé API exchange configurée.", file=sys.stderr)
        return 2

    exchange = build_exchange(
        api_key=settings.exchange.api_key, secret=settings.exchange.secret
    )
    exchange.load_markets()
    symbol = symbol or settings.exchange.symbol
    if symbol not in exchange.markets:
        print(f"REFUS : marché inconnu {symbol!r}.", file=sys.stderr)
        return 2

    balance = exchange.fetch_balance()
    free_quote = _decimal((balance.get("free") or {}).get(settings.exchange.quote_currency, 0))
    print(f"mode configuré : {settings.mode} (inchangé) · paire {symbol} · "
          f"solde libre {free_quote} {settings.exchange.quote_currency}")

    ticker = exchange.fetch_ticker(symbol)
    price = _decimal(ticker.get("last") or ticker.get("close") or 0)
    if price <= 0:
        print("REFUS : prix courant illisible.", file=sys.stderr)
        return 2

    amount = _invalid_amount(exchange, symbol, price)
    if amount is None:
        print(
            "SKIP : impossible de construire une quantité à la fois sous "
            "min_amount et sous min_cost sans dépasser 0,05 EUR.",
            file=sys.stderr,
        )
        return 2
    print(f"ordre volontairement invalide : BUY market {amount} {symbol} "
          f"(≈ {amount * price:.4f} EUR, sous le minimum)")

    rejection: Exception | None = None
    accepted = None
    try:
        accepted = exchange.create_order(symbol, "market", "buy", str(amount), None, {})
    except Exception as exc:  # noqa: BLE001 — c'est précisément ce qu'on mesure
        rejection = exc

    if accepted is not None:
        # Ne devrait jamais arriver : la quantité est sous les deux minimums.
        print("CRITIQUE : l'ordre a été accepté malgré une quantité sous le minimum.",
              file=sys.stderr)
        filled = _decimal((accepted or {}).get("filled") or amount)
        try:
            exchange.create_order(symbol, "market", "sell", str(filled), None, {})
            print("position revendue immédiatement.", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"REVENTE IMPOSSIBLE : {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    message = f"{type(rejection).__name__}: {rejection}" if rejection else "aucune erreur"
    lowered = message.lower()
    looks_like_rejection = any(
        token in lowered
        for token in ("minimum", "min", "amount", "invalid", "order", "funds", "volume")
    )
    print(f"rejet reçu : {message}")
    print(f"classification : {'rejet exchange propre' if looks_like_rejection else 'à relire'}")

    open_orders = exchange.fetch_open_orders(symbol)
    if open_orders:
        print(f"CRITIQUE : {len(open_orders)} ordre(s) ouvert(s) après le rejet.",
              file=sys.stderr)
        return 1
    print("ordres ouverts après le test : 0")
    print("RÉSULTAT : la capture d'erreur est propre, aucun ordre n'a été créé.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sonde 3.1 : ordre réel volontairement invalide"
    )
    parser.add_argument("--confirm-live", action="store_true",
                        help="confirme l'envoi d'un ordre réel (rejeté par construction)")
    parser.add_argument("--symbol", default=None, help="paire à sonder (défaut: TRADING_SYMBOL)")
    args = parser.parse_args()
    if not args.confirm_live:
        print(
            "REFUS : la sonde envoie un ordre réel (invalide) et exige --confirm-live.\n"
            "Elle ne peut pas être exécutée en mode papier par accident.",
            file=sys.stderr,
        )
        return 2
    try:
        return _run(args.symbol)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
