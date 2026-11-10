#!/usr/bin/env python3
"""Sonde Kraken AddOrder en validation seulement (aucun ordre créé).

La quantité est volontairement sous les minima. Le paramètre Kraken
``validate=True`` est obligatoire, même en mode papier : la sonde ne doit jamais
constituer un chemin d'exécution hors du verrou et du journal de LiveBroker.
Une réponse ambiguë n'autorise ni renvoi automatique ni vente compensatoire.
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
        accepted = exchange.create_order(symbol, "market", "buy", str(amount), None, {"validate": True})
    except Exception as exc:  # noqa: BLE001 — c'est précisément ce qu'on mesure
        rejection = exc

    if accepted is not None:
        print("Validation retournée sans rejet ; aucun ordre demandé (validate=true).")
        if (accepted or {}).get('id'):
            print("CRITIQUE : identifiant inattendu en mode validation ; inspecter le compte sans renvoi.", file=sys.stderr)
            return 1
        return 0

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
    if rejection is not None:
        import ccxt
        if not isinstance(rejection, ccxt.ExchangeError):
            print("RÉSULTAT INCONNU : la réponse ne prouve pas un rejet de validation.", file=sys.stderr)
            return 1
    print("RÉSULTAT : rejet de validation confirmé ; aucun ordre demandé.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sonde Kraken : validation seule, sans création d’ordre"
    )
    parser.add_argument("--confirm-live", action="store_true",
                        help="confirme la validation avec la clé privée Kraken (sans ordre)")
    parser.add_argument("--symbol", default=None, help="paire à sonder (défaut: TRADING_SYMBOL)")
    args = parser.parse_args()
    if not args.confirm_live:
        print(
            "REFUS : la sonde utilise la validation privée Kraken et exige --confirm-live.\n"
            "Aucun ordre n’est créé : validate=true est systématique.",
            file=sys.stderr,
        )
        return 2
    try:
        return _run(args.symbol)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
