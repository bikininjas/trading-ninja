#!/usr/bin/env python3
"""Inspection Kraken spot — lecture seule.

Quatre usages :

    .venv/bin/python scripts/check_kraken.py --markets
    .venv/bin/python scripts/check_kraken.py --permissions
    .venv/bin/python scripts/check_kraken.py --affordable
    .venv/bin/python scripts/check_kraken.py --scan

`--markets` ne demande aucune clé : il lit les marchés publics et affiche les
contraintes que le pare-feu utilisera (pas de quantité, minimum, tick, frais).
`--permissions` interroge `GetApiKeyInfo` et applique le verdict fail-closed de
`app.exchange.kraken` : retraits interdits, droits de trading exigés.
`--affordable` calcule, pour chaque paire configurée, l'équité qu'exige son minimum
d'ordre. `--scan` fait le même calcul sur tout l'univers et liste ce qui est
réellement tradable avec l'équité disponible.

Aucun ordre n'est envoyé, aucun réglage n'est modifié.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.env import load_env  # noqa: E402
from app.core.settings import load_settings  # noqa: E402
from app.engine.affordability import assess  # noqa: E402
from app.exchange.kraken import (  # noqa: E402
    KrakenAdapterError,
    build_exchange,
    market_info,
    resolve_taker_fee,
    verify_key_permissions,
)

DEFAULT_SYMBOLS = ("BTC/EUR", "ETH/EUR", "SOL/EUR")

# Jetons indexés sur une devise : ce ne sont pas des opportunités de trading, leur
# ATR est celui d'un stablecoin et le filtre de volatilité les refuse de toute façon.
STABLE_BASES = frozenset({
    "EURC", "USDC", "USDT", "DAI", "PYUSD", "USDP", "TUSD", "EURS", "EURT", "GBPT", "USDG",
})


def _closed_candles(exchange, symbol: str, timeframe: str = "4h", limit: int = 80) -> list[list]:
    """Bougies **clôturées** uniquement.

    CCXT renvoie la bougie en cours en dernière position ; l'inclure ferait varier
    l'ATR à chaque tick et rendrait le diagnostic irreproductible. C'est la règle du
    projet, appliquée même à un outil de mesure.
    """
    candles = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    timeframe_ms = exchange.parse_timeframe(timeframe) * 1000
    now_ms = exchange.milliseconds()
    closed = [c for c in candles if c[0] + timeframe_ms <= now_ms]
    if len(closed) < 15 or closed[-1][0] != (now_ms // timeframe_ms - 1) * timeframe_ms:
        raise ValueError("dernière bougie clôturée attendue indisponible")
    if any(b[0] - a[0] != timeframe_ms for a, b in zip(closed[-15:], closed[-14:])):
        raise ValueError("trou dans les bougies nécessaires à l'ATR")
    return closed


def _atr_14(candles: list[list]) -> Decimal:
    if len(candles) < 15:
        raise ValueError(f"{len(candles)} bougies clôturées : il en faut 15")
    true_ranges = []
    for previous, current in zip(candles, candles[1:]):
        high, low, prev_close = (Decimal(str(current[2])), Decimal(str(current[3])),
                                 Decimal(str(previous[4])))
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(true_ranges[-14:]) / Decimal(14)


def _print_affordability(exchange, symbols, equity: Decimal, risk_config, quote: str) -> int:
    print(f"\nFaisabilité avec {equity} {quote} · risque {risk_config.risk_per_trade_pct:.2%} · "
          f"exposition max {risk_config.max_notional_pct:.0%}")
    print(f"  {'paire':12} {'min ordre':>10} {'ATR H4':>7} {'stop':>7} "
          f"{'risque au min':>14} {'équité requise':>15}  verdict")
    failures = 0
    for symbol in symbols:
        info = market_info(exchange, symbol)
        if info.quote != quote:
            print(f"  {symbol:12} ignorée : cotation {info.quote}, équité en {quote}")
            continue
        price = Decimal(str(exchange.fetch_ticker(symbol)["last"]))
        try:
            atr = _atr_14(_closed_candles(exchange, symbol))
        except Exception as exc:  # noqa: BLE001 — un diagnostic ne doit pas s'arrêter
            print(f"  {symbol:12} ATR indisponible : {exc}")
            failures += 1
            continue
        try:
            verdict = assess(
                symbol, price=price, atr_14=atr, constraints=info.constraints,
                config=risk_config, equity=equity,
            )
        except ValueError as exc:
            print(f"  {symbol:12} non mesurable : {exc}")
            failures += 1
            continue
        required = verdict.binding if verdict.required_equity is None else f"{verdict.required_equity:.2f}"
        state = "possible" if verdict.affordable else (
            f"refus {verdict.binding}" if verdict.required_equity is None
            else f"manque {verdict.shortfall:.2f}"
        )
        print(f"  {symbol:12} {verdict.min_order_quote:10.2f} {atr / price:7.2%} "
              f"{verdict.stop_distance_pct:7.2%} {verdict.effective_risk_pct_at_minimum:14.2%} "
              f"{required:>15}  {state}")
        if not verdict.affordable:
            failures += 1
    return failures


def _print_scan(exchange, equity: Decimal, risk_config, quote: str, top: int,
                min_volume: Decimal = Decimal("100000")) -> int:
    """Cherche dans tout l'univers les paires réellement tradables avec cette équité.

    Filtre d'abord sur ce qui est mécaniquement possible (minimum d'ordre sous le
    plafond d'exposition), puis sur la liquidité, puis calcule l'ATR réel des plus
    liquides. Les paires illiquides sont écartées avant le calcul : un minimum bas
    sur un carnet vide n'est pas une opportunité, c'est un piège à slippage.
    """
    tickers = exchange.fetch_tickers()
    candidates = []
    for symbol, market in exchange.markets.items():
        if not market.get("active") or market.get("quote") != quote or not market.get("spot"):
            continue
        if market.get("base") in STABLE_BASES:
            continue
        ticker = tickers.get(symbol) or {}
        if not ticker.get("last"):
            continue
        volume = Decimal(str(ticker.get("quoteVolume") or 0))
        if volume < min_volume:
            continue
        price = Decimal(str(ticker["last"]))
        min_order = max(
            Decimal(str(market["limits"]["amount"]["min"])) * price,
            Decimal(str(market["limits"]["cost"]["min"])),
        )
        if min_order > equity * risk_config.max_notional_pct:
            continue
        candidates.append((volume, symbol, price))
    candidates.sort(reverse=True)

    print(f"\nScan de l'univers {quote} avec {equity} {quote} · risque "
          f"{risk_config.risk_per_trade_pct:.2%} · exposition max "
          f"{risk_config.max_notional_pct:.0%}")
    print(f"  {len(candidates)} paires passent le filtre mécanique et de liquidité ; "
          f"ATR réel calculé sur les {min(top, len(candidates))} plus liquides.\n")
    print(f"  {'paire':12} {'volume 24h':>13} {'min ordre':>10} {'ATR H4':>7} "
          f"{'équité req.':>12}  verdict")
    affordable = []
    failures = 0
    for volume, symbol, price in candidates[:top]:
        try:
            atr = _atr_14(_closed_candles(exchange, symbol))
        except Exception as exc:  # noqa: BLE001 — une paire sans historique ne bloque pas le scan
            print(f"  {symbol:12} ATR non mesurable : {type(exc).__name__}")
            failures += 1
            continue
        info = market_info(exchange, symbol)
        try:
            verdict = assess(
                symbol, price=price, atr_14=atr, constraints=info.constraints,
                config=risk_config, equity=equity,
            )
        except ValueError as exc:
            print(f"  {symbol:12} non mesurable : {exc}")
            failures += 1
            continue
        required = "—" if verdict.required_equity is None else f"{verdict.required_equity:.2f}"
        state = "possible" if verdict.affordable else (
            f"refus {verdict.binding}" if verdict.required_equity is None
            else f"manque {verdict.shortfall:.2f}"
        )
        print(f"  {symbol:12} {volume:13,.0f} {verdict.min_order_quote:10.2f} "
              f"{atr / price:7.2%} {required:>12}  {state}")
        if verdict.affordable:
            affordable.append(symbol)

    if affordable:
        print(f"\n  {len(affordable)} paires tradables : " + ", ".join(affordable))
        print("  Pour les utiliser : TRADING_SYMBOLS=" + ",".join(affordable))
    elif not failures:
        print("\n  Aucune paire évaluée ne passe ces contraintes ; le scan n'est pas exhaustif.")
    else:
        print("\n  Diagnostic incomplet : aucune conclusion sur les paires non mesurables.")
    return 1 if failures else 0


def _credentials(env: dict[str, str]) -> tuple[str, str]:
    if env.get("EXCHANGE_ID", "binance").lower() == "kraken" and env.get("EXCHANGE_API_KEY"):
        return env["EXCHANGE_API_KEY"], env.get("EXCHANGE_SECRET", "")
    key = next((env[name] for name in ("KRAKEN_API_KEY", "KRAKEN_KEY") if env.get(name)), "")
    secret = next((env[name] for name in ("KRAKEN_SECRET", "KRAKEN_SECRET_KEY") if env.get(name)), "")
    return key, secret


def _resolve_equity(exchange, quote: str, api_key: str, secret: str, explicit: str | None):
    """Équité à utiliser : explicite, sinon lue en lecture seule sur le compte."""
    if explicit:
        return Decimal(explicit)
    if api_key and secret:
        try:
            balance = exchange.fetch_balance()
            return Decimal(str(balance["total"].get(quote, 0)))
        except Exception as exc:  # noqa: BLE001 — diagnostic, pas un ordre
            print(f"  équité illisible : {exc}")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspection Kraken spot, lecture seule")
    parser.add_argument("--markets", action="store_true", help="affiche les contraintes des paires")
    parser.add_argument("--permissions", action="store_true", help="vérifie les droits de la clé")
    parser.add_argument("--affordable", action="store_true",
                        help="calcule l'équité minimale pour trader chaque paire")
    parser.add_argument("--scan", action="store_true",
                        help="scanne tout l'univers à la recherche des paires tradables")
    parser.add_argument("--top", type=int, default=30,
                        help="nombre de paires analysées par le scan (défaut : 30)")
    parser.add_argument("--equity", help="équité à simuler (sinon lue sur le compte, en lecture)")
    parser.add_argument("--profile", help="profil de risque à simuler (prudent, micro...)")
    parser.add_argument("--symbol", action="append", help="paire à inspecter (répétable)")
    parser.add_argument("--public-live-risk", action="store_true", help=(
        "simulation publique du risque LIVE, sans clé ni fichier de configuration ; "
        "exige --affordable, --equity et --profile"))
    parser.add_argument("--fee-pct", help="frais taker simulés par côté (public-live-risk ; défaut 0.008)")
    parser.add_argument("--slippage-bps", help="glissement simulé par côté (public-live-risk ; défaut 5 pb)")
    args = parser.parse_args()
    if not args.public_live_risk and (args.fee_pct is not None or args.slippage_bps is not None):
        parser.error("--fee-pct et --slippage-bps exigent --public-live-risk")
    if args.public_live_risk and (
        not args.affordable or args.scan or args.permissions or not args.equity
        or not args.profile or args.profile == "progressif"
    ):
        parser.error("--public-live-risk exige --affordable --equity --profile LIVE, sans --scan ni --permissions")
    if not (args.markets or args.permissions or args.affordable or args.scan):
        args.markets = args.permissions = True

    env = {} if args.public_live_risk else load_env()
    api_key, secret = _credentials(env)
    exchange = build_exchange(api_key=api_key, secret=secret)
    if args.public_live_risk:
        # CCXT désactive par défaut les réglages proxy/CA de requests. Ce mode
        # public et sans identifiants respecte ceux de l'environnement courant.
        exchange.session.trust_env = True
    exchange.load_markets()
    symbols = args.symbol or list(DEFAULT_SYMBOLS)
    failures = 0

    if args.markets:
        print("Marchés Kraken (symboles unifiés CCXT)")
        for symbol in symbols:
            if symbol not in exchange.symbols:
                print(f"  ÉCHEC  {symbol} inconnu sur Kraken")
                failures += 1
                continue
            taker = resolve_taker_fee(exchange, symbol, Decimal("0.008"))
            info = market_info(exchange, symbol, taker_fee=taker)
            c = info.constraints
            print(
                f"  OK     {symbol:9} id={info.exchange_symbol:10} {info.base}/{info.quote} "
                f"actif={info.active}"
            )
            print(
                f"         pas={c.amount_step} min_qty={c.min_amount} "
                f"min_notional={c.min_notional} {info.quote} tick={c.price_tick} "
                f"taker={info.taker_fee:.2%}"
            )

    if args.permissions:
        print("\nPermissions de la clé (GetApiKeyInfo)")
        if not api_key or not secret:
            print("  ATTENTE  aucune clé Kraken configurée "
                  "(KRAKEN_API_KEY / KRAKEN_SECRET) — vérification impossible, donc non concluante")
        else:
            print(f"  clé : {len(api_key)} caractères (valeur non affichée)")
            try:
                audit = verify_key_permissions(exchange)
            except KrakenAdapterError as exc:
                print(f"  ÉCHEC  {exc}")
                failures += 1
            else:
                print("  OK     retraits absents, droits de trading présents")
                print("         " + ", ".join(sorted(audit.permissions)))
                if audit.ip_allowlist:
                    print("         IP allowlist : " + ", ".join(audit.ip_allowlist))
                else:
                    print("         ALERTE  aucune IP allowlist : une clé fuitée serait "
                          "utilisable depuis n'importe où")

    settings = None
    equity = None
    quote = None
    if args.affordable or args.scan:
        overrides = {"RISK_PROFILE": args.profile} if args.profile else {}
        if args.public_live_risk:
            overrides.update({
                "EXCHANGE_ID": "kraken", "TRADING_SYMBOL": (args.symbol or list(DEFAULT_SYMBOLS))[0],
                "COST_AWARE": "true", "TAKER_FEE_PCT": args.fee_pct or "0.008",
                "PAPER_SLIPPAGE_BPS": args.slippage_bps or "5",
            })
        try:
            settings = load_settings(overrides=overrides, use_environment=not args.public_live_risk)
        except RuntimeError as exc:
            print(f"  ÉCHEC  profil de risque invalide : {exc}")
            return 1
        if args.public_live_risk:
            settings = replace(settings, risk=replace(settings.risk,
                small_account_max_risk_pct=min(settings.risk.small_account_max_risk_pct,
                                               settings.risk.risk_per_trade_pct)))
            print(f"Simulation publique : frais {settings.risk.execution_fee_pct:.2%}/côté, "
                  f"glissement {settings.risk.execution_slippage_pct * Decimal(10000)} pb/côté ; prix de dernier échange "
                  "sans fraîcheur source vérifiée, ATR sur bougies clôturées ; "
                  "aucun compte ni ordre. Ce diagnostic ne valide pas la stratégie.")
        base = args.symbol or (list(DEFAULT_SYMBOLS) if args.public_live_risk else list(settings.exchange.allowed_symbols)) or list(DEFAULT_SYMBOLS)
        quote = base[0].split("/")[-1].upper()
        equity = _resolve_equity(exchange, quote, api_key, secret, args.equity)
        if equity is None or equity <= 0:
            print("\nÉquité inconnue. Utiliser --equity, ou configurer une clé Kraken en lecture.")
            failures += 1
            equity = None

    if args.affordable and equity is not None:
        # Par défaut, on évalue l'univers configuré ; --symbol teste une paire hors liste.
        assessed = args.symbol or (list(DEFAULT_SYMBOLS) if args.public_live_risk else list(settings.exchange.allowed_symbols)) or list(DEFAULT_SYMBOLS)
        failures += _print_affordability(exchange, assessed, equity, settings.risk, quote)

    if args.scan and equity is not None:
        failures += _print_scan(exchange, equity, settings.risk, quote, args.top)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
