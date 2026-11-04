"""Backtest historique — enregistrement, rejeu de régression, veille de dérive.

    python scripts/backtest.py record --golden tests/fixtures/backtest_golden.json
    python scripts/backtest.py replay --golden tests/fixtures/backtest_golden.json
    python scripts/backtest.py watch  --golden tests/fixtures/backtest_golden.json

`record` interroge le vrai modèle sur une fenêtre récente et fige les décisions.
`replay` rejoue ces décisions sans LLM : c'est la régression déterministe.
`watch` ne fait rien si le prompt et le modèle n'ont pas changé ; sinon il rejoue
une décision témoin (ou la fenêtre entière) et signale la dérive.

Aucun ordre, aucune écriture en base, aucun argent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtest.engine import DECISION_MS, H1_WARMUP, ReplayExchange, run_backtest
from app.backtest.golden import (
    GoldenBundle,
    ReplayLLM,
    diff_metrics,
    load_bundle,
    market_from_bundle,
    metrics_of,
    prompt_fingerprint,
    risk_fingerprint,
    save_bundle,
)
from app.core.settings import load_settings
from app.exchange.kraken import build_exchange, market_info
from app.llm.client import SignalClient
from app.market.feed import EMA_PERIOD, TIMEFRAME_MS, build_snapshot

FIXTURE = "tests/fixtures/backtest_golden.json"


def fetch_recent(exchange, symbol: str, timeframe: str) -> list[list]:
    """Les 720 bougies les plus récentes, clôturées uniquement.

    Kraken plafonne à 720 points par appel et ignore un `since` plus ancien :
    inutile de paginer, la fenêtre maximale est celle-ci.
    """
    now = exchange.milliseconds()
    duration = TIMEFRAME_MS[timeframe]
    batch = exchange.fetch_ohlcv(symbol, timeframe, limit=720)
    candles = {c[0]: c for c in batch if c[0] + duration <= now}
    return [candles[key] for key in sorted(candles)]


def first_index(h4: list[list], h1: list[list]) -> int:
    """Premier indice où l'EMA200 et les 120 H1 nécessaires existent."""
    if not h1:
        raise SystemExit("historique H1 vide : impossible de construire le contexte")
    earliest = h1[0][0] + H1_WARMUP * 3_600_000
    for i, candle in enumerate(h4):
        if i >= EMA_PERIOD + 1 and candle[0] + DECISION_MS >= earliest:
            return i
    raise SystemExit("aucune bougie H4 ne dispose de 120 H1 antérieures")


def with_overrides(settings, args):
    """Scénario de backtest : ne modifie jamais `.env`, seulement cette exécution."""
    if getattr(args, "min_confidence", None) is None:
        return settings
    return replace(
        settings,
        risk=replace(settings.risk, min_confidence=Decimal(str(args.min_confidence))),
    )


async def record(args) -> int:
    settings = with_overrides(load_settings(), args)
    exchange = build_exchange()
    exchange.load_markets()
    info = market_info(exchange, args.symbol)

    h4 = fetch_recent(exchange, args.symbol, "4h")
    h1 = fetch_recent(exchange, args.symbol, "1h")
    start = first_index(h4, h1)
    end = min(len(h4) - 1, start + args.max_candles - 1) if args.max_candles else len(h4) - 1
    print(f"{args.symbol} : {len(h4)} H4 · {len(h1)} H1 · fenêtre {start}..{end} "
          f"({end - start + 1} bougies)")

    client = SignalClient(settings.llm)
    print(f"préchauffage de {settings.llm.model}…")
    if not await client.warmup():
        raise SystemExit("modèle injoignable : enregistrement annulé (pas de golden dégradé)")

    decisions: list[dict] = []

    async def propose(snapshot, context):
        signal = await client.propose(
            args.symbol, snapshot, context,
            has_position=False,
            trend_buffer_pct=settings.risk.trend_buffer_pct,
        )
        decisions.append({
            "at": snapshot.tick_at.isoformat(),
            "action": signal.action,
            "confidence": str(signal.confidence),
            "market_regime": signal.market_regime,
            "directional_bias": signal.directional_bias,
            "thesis": signal.thesis,
            "prompt_payload": dict(signal.prompt_payload),
            "prompt_sha256": signal.prompt_sha256,
            "inference_ms": signal.inference_ms,
            "degraded": signal.degraded,
            "degradation_reason": signal.degradation_reason or "",
        })
        return signal

    def progress(index: int, total: int) -> None:
        if index % 10 == 0:
            print(f"  {index}/{total} bougies · {len(decisions)} décisions", flush=True)

    result = await run_backtest(
        symbol=args.symbol, info=info, h4=h4, h1=h1, settings=settings,
        propose=propose, start_index=start, end_index=end, progress=progress,
    )
    print()
    print(result.summary())

    bundle = GoldenBundle(
        symbol=args.symbol,
        model=settings.llm.model,
        prompt_fingerprint=prompt_fingerprint(settings),
        risk_fingerprint=risk_fingerprint(settings, info),
        start_index=start,
        end_index=end,
        h4=h4,
        h1=h1,
        decisions=decisions,
        expected=metrics_of(result),
        market={
            "exchange_symbol": info.exchange_symbol,
            "base": info.base,
            "quote": info.quote,
            "taker_fee": str(info.taker_fee),
            "maker_fee": str(info.maker_fee),
            "amount_step": str(info.constraints.amount_step),
            "min_amount": str(info.constraints.min_amount),
            "min_notional": str(info.constraints.min_notional),
            "price_tick": str(info.constraints.price_tick),
        },
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    save_bundle(bundle, args.golden)
    print(f"\ngolden écrit : {args.golden} ({len(decisions)} décisions)")
    if result.degraded:
        print(f"ATTENTION : {result.degraded} replis techniques dans le golden")
    return 0


async def replay(args) -> int:
    settings = with_overrides(load_settings(), args)
    bundle = load_bundle(args.golden)
    current_prompt = prompt_fingerprint(settings)
    if current_prompt != bundle.prompt_fingerprint and not args.bless:
        print("prompt ou modèle différent de l'enregistrement :")
        print(f"  golden  {bundle.prompt_fingerprint[:16]}…")
        print(f"  actuel  {current_prompt[:16]}…")
        print("Relancer `record` pour de nouvelles décisions, puis relire le diff.")
        return 3

    info = market_from_bundle(bundle)

    result = await run_backtest(
        symbol=bundle.symbol, info=info, h4=bundle.h4, h1=bundle.h1,
        settings=settings, propose=ReplayLLM(bundle.decisions),
        start_index=bundle.start_index, end_index=bundle.end_index,
    )
    actual = metrics_of(result)
    print(result.summary())

    differences = diff_metrics(bundle.expected, actual)
    if not differences:
        if risk_fingerprint(settings, info) != bundle.risk_fingerprint:
            print("\nfingerprint de risque différent, mais métriques identiques.")
            print("Mettre le golden à jour :`replay --bless`.")
            return 1
        print("\nrejeu identique au golden.")
        return 0

    print("\nÉCART avec le golden :")
    print("\n".join(differences))
    if args.bless:
        bundle.expected = actual
        bundle.risk_fingerprint = risk_fingerprint(settings, info)
        bundle.created_at = datetime.now(timezone.utc).isoformat()
        save_bundle(bundle, args.golden)
        print("\ngolden mis à jour — relire le diff avant de committer.")
        return 0
    print("\nSi l'écart est voulu :`replay --bless`, puis relire le diff du golden.")
    return 1


def _snapshot_at(bundle: GoldenBundle, info, index: int):
    replay_exchange = ReplayExchange(bundle.h4, bundle.h1)
    replay_exchange.set_now(bundle.h4[index][0] + DECISION_MS)
    at = datetime.fromtimestamp(
        (bundle.h4[index][0] + DECISION_MS) / 1000, tz=timezone.utc
    )
    state = build_snapshot(
        replay_exchange, bundle.symbol, info=info, now=at, with_order_book=False
    )
    return state


async def watch(args) -> int:
    settings = with_overrides(load_settings(), args)
    bundle = load_bundle(args.golden)
    info = market_from_bundle(bundle)
    client = SignalClient(settings.llm)

    changed = prompt_fingerprint(settings) != bundle.prompt_fingerprint
    risk_changed = risk_fingerprint(settings, info) != bundle.risk_fingerprint
    print(f"veille {bundle.symbol} · prompt {'MODIFIÉ' if changed else 'inchangé'}"
          f" · risque {'MODIFIÉ' if risk_changed else 'inchangé'}")

    if not await client.warmup():
        print("modèle injoignable — veille impossible")
        return 2

    if changed:
        decisions: list[dict] = []

        async def propose(snapshot, context):
            signal = await client.propose(
                bundle.symbol, snapshot, context,
                has_position=False,
                trend_buffer_pct=settings.risk.trend_buffer_pct,
            )
            decisions.append({"at": snapshot.tick_at.isoformat(),
                              "action": signal.action,
                              "confidence": str(signal.confidence)})
            return signal

        result = await run_backtest(
            symbol=bundle.symbol, info=info, h4=bundle.h4, h1=bundle.h1,
            settings=settings, propose=propose,
            start_index=bundle.start_index, end_index=bundle.end_index,
        )
        old = {row["at"]: row["action"] for row in bundle.decisions}
        new = {row["at"]: row["action"] for row in decisions}
        common = sorted(set(old) & set(new))
        same = sum(old[key] == new[key] for key in common)
        rate = same / len(common) if common else 0
        print(f"accord des décisions : {same}/{len(common)} ({rate:.0%})")
        print(result.summary())
        if rate < 0.90:
            for key in common:
                if old[key] != new[key]:
                    print(f"  {key}: {old[key]} → {new[key]}")
            print("DÉRIVE : prompt ou modèle a changé le comportement. Relire le diff, "
                  "puis ré-enregistrer le golden.")
            return 2
        print("comportement compatible avec le golden (à la variabilité du modèle près) — "
              "ré-enregistrer le golden pour figer le nouveau prompt.")
        return 2

    if risk_changed:
        result = await run_backtest(
            symbol=bundle.symbol, info=info, h4=bundle.h4, h1=bundle.h1,
            settings=settings, propose=ReplayLLM(bundle.decisions),
            start_index=bundle.start_index, end_index=bundle.end_index,
        )
        actual = metrics_of(result)
        print(result.summary())
        differences = diff_metrics(bundle.expected, actual)
        if differences:
            print("IMPACT du profil de risque :")
            print("\n".join(differences))
            print("Relire ces changements, puis `replay --bless` si le nouvel "
                  "équilibre est voulu.")
        else:
            print("profil de risque modifié sans impact mesuré — `replay --bless` "
                  "pour figer le nouveau fingerprint.")
        return 2

    # Prompt et risque inchangés : canari sur quelques décisions, sans tout rejouer.
    sample = [
        bundle.decisions[i]
        for i in range(0, len(bundle.decisions), max(1, len(bundle.decisions) // 12))
    ][:12]
    mismatches = []
    index_by_close = {
        datetime.fromtimestamp((c[0] + DECISION_MS) / 1000, tz=timezone.utc).isoformat(): i
        for i, c in enumerate(bundle.h4)
    }
    for row in sample:
        index = index_by_close.get(row["at"])
        if index is None:
            continue
        state = _snapshot_at(bundle, info, index)
        signal = await client.propose(
            bundle.symbol, state.snapshot, state.context,
            has_position=False, trend_buffer_pct=settings.risk.trend_buffer_pct,
        )
        if signal.action != row["action"]:
            mismatches.append((row["at"], row["action"], signal.action))
    matches = len(sample) - len(mismatches)
    rate = matches / len(sample) if sample else 1
    print(f"canari : {matches}/{len(sample)} décisions identiques ({rate:.0%})")
    # Le modèle n'est pas reproductible d'un processus à l'autre : à température 0,
    # une décision isolée peut basculer sur une quasi-égalité. En dessous de 90 %
    # d'accord, ce n'est plus du bruit.
    if rate < 0.90:
        for at, old, new in mismatches:
            print(f"  {at}: {old} → {new}")
        print("DÉRIVE : mêmes entrées, décisions différentes. Vérifier le modèle "
              "(mise à jour silencieuse ?).")
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest historique Trading Ninja")
    parser.add_argument("command", choices=("record", "replay", "watch"))
    parser.add_argument("--golden", default=FIXTURE)
    parser.add_argument("--symbol", default="BTC/EUR")
    parser.add_argument("--max-candles", type=int, default=0,
                        help="limite la fenêtre (utile pour un essai rapide)")
    parser.add_argument("--bless", action="store_true",
                        help="met à jour les métriques attendues du golden")
    parser.add_argument("--min-confidence", type=float, default=None,
                        help="scénario : seuil de confiance du pare-feu (défaut .env)")
    args = parser.parse_args()
    if args.command == "record":
        return asyncio.run(record(args))
    if args.command == "replay":
        return asyncio.run(replay(args))
    return asyncio.run(watch(args))


if __name__ == "__main__":
    raise SystemExit(main())
