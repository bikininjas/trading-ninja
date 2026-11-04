#!/usr/bin/env python3
"""Sonde **lecture seule** de la clé Binance — étape 1.1 de la feuille de route.

Ce script ne peut rien casser : il n'envoie aucun ordre, ne transfère rien, ne
modifie aucun réglage. Il répond à trois questions :

1. La clé fournie parle-t-elle à **mainnet** ou à **testnet** ?
2. Le compte est-il lisible (signature HMAC valide, IP autorisée) ?
3. La clé autorise-t-elle les **retraits** — auquel cas elle doit être remplacée,
   pas utilisée (garde-fou n°1 du projet).

Sortie : code 0 si au moins un réseau authentifie la clé et que les permissions
sont saines ; 1 sinon. Le secret n'est jamais affiché, ni en clair ni tronqué.

Usage :
    .venv/bin/python scripts/check_exchange.py
    .venv/bin/python scripts/check_exchange.py --network mainnet
    .venv/bin/python scripts/check_exchange.py --json
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.env import load_env  # noqa: E402
from app.engine.risk_gate import ExchangeConstraints  # noqa: E402

NETWORKS: dict[str, str] = {
    "mainnet": "https://api.binance.com",
    "testnet": "https://testnet.binance.vision",
}

TIMEOUT = 15


class BinanceError(RuntimeError):
    def __init__(self, http_status: int, code: int | None, message: str) -> None:
        super().__init__(f"HTTP {http_status} · code {code} · {message}")
        self.http_status = http_status
        self.code = code
        self.message = message


def _http(url: str, headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(body)
            code, message = payload.get("code"), payload.get("msg", body)
        except json.JSONDecodeError:
            code, message = None, body[:200]
        raise BinanceError(exc.code, code, str(message)) from None
    except urllib.error.URLError as exc:
        raise BinanceError(0, None, f"réseau injoignable : {exc.reason}") from None


def server_offset_ms(base: str) -> int:
    """Décalage entre l'horloge locale et celle de l'exchange (les deux en ms)."""
    payload = _http(f"{base}/api/v3/time")
    return int(payload["serverTime"]) - int(time.time() * 1000)


def signed_get(base: str, path: str, api_key: str, secret: str, offset_ms: int) -> dict:
    params = {
        "timestamp": int(time.time() * 1000) + offset_ms,
        "recvWindow": 5000,
    }
    query = urllib.parse.urlencode(params)
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return _http(f"{base}{path}?{query}&signature={signature}", {"X-MBX-APIKEY": api_key})


def probe(network: str, api_key: str, secret: str) -> dict:
    base = NETWORKS[network]
    result: dict[str, object] = {"network": network, "base_url": base}
    try:
        offset = server_offset_ms(base)
        result["clock_offset_ms"] = offset
        account = signed_get(base, "/api/v3/account", api_key, secret, offset)
    except BinanceError as exc:
        result["ok"] = False
        result["error"] = str(exc)
        result["error_code"] = exc.code
        return result

    result["ok"] = True
    result["account_type"] = account.get("accountType")
    result["can_trade"] = account.get("canTrade")
    result["can_withdraw"] = account.get("canWithdraw")
    result["can_deposit"] = account.get("canDeposit")
    result["permissions"] = account.get("permissions")
    result["balances_nonzero"] = [
        {"asset": b["asset"], "free": b["free"], "locked": b["locked"]}
        for b in account.get("balances", [])
        if float(b["free"]) or float(b["locked"])
    ]

    # Endpoint des restrictions : absent sur testnet, c'est normal et non bloquant.
    result["restrictions"] = None
    if network == "mainnet":
        try:
            result["restrictions"] = signed_get(
                base, "/sapi/v1/account/apiRestrictions", api_key, secret, offset
            )
        except BinanceError as exc:
            result["restrictions_error"] = str(exc)
    return result


def symbol_constraints(symbol: str) -> dict:
    """Filtres de l'exchange pour un symbole — les entrées de `ExchangeConstraints`.

    Aucune valeur n'est codée en dur dans le pare-feu : `amount_step`, `min_amount`,
    `min_notional` et `price_tick` viennent d'ici, sinon ils dérivent au premier
    changement décidé par Binance.
    """
    base = NETWORKS["mainnet"]
    pair = symbol.replace("/", "").upper()
    info = _http(f"{base}/api/v3/exchangeInfo?symbol={pair}")
    symbols = info.get("symbols") or []
    if not symbols:
        raise BinanceError(200, None, f"symbole inconnu : {pair}")
    entry = symbols[0]
    filters = {f["filterType"]: f for f in entry.get("filters", [])}
    return {
        "symbol": entry["symbol"],
        "status": entry.get("status"),
        "base_asset": entry.get("baseAsset"),
        "quote_asset": entry.get("quoteAsset"),
        "price_tick": filters.get("PRICE_FILTER", {}).get("tickSize"),
        "amount_step": filters.get("LOT_SIZE", {}).get("stepSize"),
        "min_amount": filters.get("LOT_SIZE", {}).get("minQty"),
        "min_notional": filters.get("NOTIONAL", {}).get("minNotional")
        or filters.get("MIN_NOTIONAL", {}).get("minNotional"),
        "ticker_price": _http(f"{base}/api/v3/ticker/price?symbol={pair}").get("price"),
    }


def verdict(result: dict) -> tuple[bool, str]:
    """Verdict fail-closed.

    Sur mainnet, `apiRestrictions` fait autorité : `canWithdraw` au niveau du
    compte décrit la capacité du compte, pas celle de la clé — les confondre
    condamne une clé saine et donne une fausse alerte.
    """
    if not result.get("ok"):
        code = result.get("error_code")
        if code == -2015:
            return False, "clé refusée (signature, permissions ou IP non autorisée)"
        if code == -1021:
            return False, "horloge désynchronisée au-delà de recvWindow"
        return False, f"échec : {result.get('error')}"

    restrictions = result.get("restrictions")
    if restrictions:
        if restrictions.get("enableWithdrawals") is not False:
            return False, "DANGER : la clé autorise les retraits — à remplacer immédiatement"
        if restrictions.get("enableReading") is not True:
            return False, "clé sans droit de lecture : impossible de vérifier quoi que ce soit"
        if restrictions.get("enableSpotAndMarginTrading") is not True:
            return False, "clé sans droit de trading spot : le bot ne pourra pas exécuter"
        return True, "clé lecture + trading spot, retraits désactivés"

    # Pas de restrictions disponibles (testnet) : le compte lisible suffit, mais
    # `canWithdraw` reste un signal à ne pas ignorer si l'endpoint apparaît.
    if result.get("can_withdraw") is True:
        return False, "canWithdraw=true sans endpoint de restrictions pour infirmer — refus"
    if result.get("can_trade") is not True:
        return False, "clé sans droit de trading : le bot ne pourra pas exécuter"
    return True, "clé lisible (restrictions non exposées par ce réseau)"


def main() -> int:
    parser = argparse.ArgumentParser(description="Sonde lecture seule de la clé Binance")
    parser.add_argument("--network", choices=[*NETWORKS, "auto"], default="auto")
    parser.add_argument("--market", action="store_true", help="vérifie aussi les filtres du symbole")
    parser.add_argument("--json", action="store_true", help="sortie machine")
    args = parser.parse_args()

    env = load_env()
    api_key = next(
        (env[name] for name in ("EXCHANGE_API_KEY", "BINANCE_API_KEY", "BINANCE_KEY") if env.get(name)),
        "",
    )
    secret = next(
        (
            env[name]
            for name in ("EXCHANGE_SECRET", "EXCHANGE_SECRET_KEY", "BINANCE_SECRET_KEY", "BINANCE_SECRET")
            if env.get(name)
        ),
        "",
    )
    if not api_key or not secret:
        print("Aucune clé exchange trouvée (BINANCE_API_KEY / BINANCE_SECRET_KEY).", file=sys.stderr)
        return 1

    networks = ["mainnet", "testnet"] if args.network == "auto" else [args.network]
    results = [probe(network, api_key, secret) for network in networks]

    market: dict | None = None
    if args.market:
        try:
            market = symbol_constraints(env.get("TRADING_SYMBOL", "BTC/USDT"))
        except BinanceError as exc:
            market = {"error": str(exc)}

    if args.json:
        print(json.dumps({"key_length": len(api_key), "results": results, "market": market},
                         indent=2, ensure_ascii=False))
    else:
        print(f"clé : {len(api_key)} caractères (valeur non affichée)")
        for result in results:
            ok, reason = verdict(result)
            mark = "OK   " if ok else "ÉCHEC"
            print(f"\n[{mark}] {result['network']} — {reason}")
            if result.get("ok"):
                print(f"        canTrade={result['can_trade']} canWithdraw={result['can_withdraw']} "
                      f"canDeposit={result['can_deposit']} type={result['account_type']}")
                restrictions = result.get("restrictions")
                if restrictions:
                    print("        apiRestrictions : "
                          f"enableReading={restrictions.get('enableReading')} "
                          f"enableSpotAndMarginTrading={restrictions.get('enableSpotAndMarginTrading')} "
                          f"enableWithdrawals={restrictions.get('enableWithdrawals')} "
                          f"ipRestrict={restrictions.get('ipRestrict')}")
                balances = result.get("balances_nonzero") or []
                if balances:
                    print("        soldes non nuls : "
                          + ", ".join(f"{b['asset']}={b['free']}" for b in balances[:10]))
                else:
                    print("        soldes non nuls : aucun (compte vide)")
            else:
                print(f"        {result.get('error')}")

    if market is not None:
        print()
        if "error" in market:
            print(f"[ÉCHEC] marché — {market['error']}")
        else:
            print(f"[OK   ] marché — {market['symbol']} {market['status']} · "
                  f"prix {market['ticker_price']} {market['quote_asset']}")
            print(f"        tick={market['price_tick']} pas={market['amount_step']} "
                  f"min_qty={market['min_amount']} min_notional={market['min_notional']}")
            try:
                constraints = ExchangeConstraints(
                    amount_step=Decimal(market["amount_step"]),
                    min_amount=Decimal(market["min_amount"]),
                    min_notional=Decimal(market["min_notional"]),
                    price_tick=Decimal(market["price_tick"]),
                )
                print("        ExchangeConstraints construites — directement injectables "
                      f"dans evaluate() : {constraints}")
            except (TypeError, ArithmeticError) as exc:
                print(f"[ÉCHEC] filtres non convertibles en Decimal : {exc}")

    authenticated = [r for r in results if r.get("ok")]
    if not authenticated:
        return 1
    healthy = [r for r in authenticated if verdict(r)[0]]
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
