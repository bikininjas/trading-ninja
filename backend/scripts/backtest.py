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
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtest.engine import DECISION_MS, H1_WARMUP, ReplayExchange, run_backtest
from app.backtest.mean_reversion import WARMUP as MR_WARMUP
from app.backtest.mean_reversion import run_mean_reversion_backtest
from app.backtest.swing import run_swing_backtest
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
from app.engine.risk_gate import EntryMode, StopMode, SwingExitMode
from app.exchange.kraken import build_exchange, market_info, resolve_taker_fee
from app.llm.client import SignalClient
from app.market.feed import EMA_PERIOD, TIMEFRAME_MS, build_snapshot
from app.market.history import aggregate, detect_gaps, load_candles

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
    overrides = {}
    if getattr(args, "min_confidence", None) is not None:
        overrides["min_confidence"] = Decimal(str(args.min_confidence))
    if getattr(args, "stop_atr", None) is not None:
        overrides["stop_atr_multiple"] = Decimal(str(args.stop_atr))
    if getattr(args, "take_profit_r", None) is not None:
        overrides["take_profit_r_multiple"] = Decimal(str(args.take_profit_r))
    if getattr(args, "require_positive_h1", False):
        overrides["entry_require_positive_h1"] = True
    if not overrides:
        return settings
    return replace(settings, risk=replace(settings.risk, **overrides))


def fee_grid(args) -> list[Decimal]:
    """Grille de frais par côté, en points de base.

    ``--maker-fees`` seul remplace la grille par 25 pb (frais maker Kraken) ;
    combiné à ``--fee-bps``, il ajoute des points de comparaison. C'est une
    **sensibilité de coût**, pas un modèle d'exécution maker : le fill reste
    celui du marché au prix de clôture + slippage.
    """
    parts: list[str] = []
    raw = getattr(args, "fee_bps", None)
    maker = getattr(args, "maker_fees", None)
    if raw:
        parts.extend(raw.split(","))
    if maker:
        parts.extend(maker.split(","))
    if not parts:
        parts = ["80", "100"]
    values = [Decimal(part.strip()) for part in parts if part.strip()]
    # Dédupliquer en conservant l'ordre demandé.
    return list(dict.fromkeys(values))


async def record(args) -> int:
    settings = with_overrides(load_settings(), args)
    exchange = build_exchange(
        api_key=settings.exchange.api_key, secret=settings.exchange.secret
    )
    exchange.load_markets()
    info = market_info(
        exchange, args.symbol,
        taker_fee=resolve_taker_fee(exchange, args.symbol, settings.exchange.taker_fee),
        maker_fee=settings.exchange.maker_fee,
    )

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

    async def propose(snapshot, context, has_position: bool = False):
        signal = await client.propose(
            args.symbol, snapshot, context,
            has_position=has_position,
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
            "has_position": has_position,
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
    settings_path = Path(args.golden).with_name("backtest_settings.json")
    if settings_path.exists():
        settings = load_settings(
            overrides=json.loads(settings_path.read_text()), use_environment=False,
        )
        print(f"Configuration de régression figée : {settings_path}")
    else:
        settings = load_settings()
    settings = with_overrides(settings, args)
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
        current_risk = risk_fingerprint(settings, info)
        if current_risk != bundle.risk_fingerprint:
            print("\nfingerprint de risque différent, mais métriques identiques.")
            if args.bless:
                bundle.risk_fingerprint = current_risk
                bundle.created_at = datetime.now(timezone.utc).isoformat()
                save_bundle(bundle, args.golden)
                print("golden mis à jour — relire le diff avant de committer.")
                return 0
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


async def scenario(args) -> int:
    """Rejoue le golden avec un autre stop/take-profit, sans le modifier.

    C'est un laboratoire : les décisions du modèle sont figées, seule la géométrie
    de sortie change. Aucune écriture, aucun appel LLM.
    """
    settings = with_overrides(load_settings(), args)
    bundle = load_bundle(args.golden)
    if prompt_fingerprint(settings) != bundle.prompt_fingerprint:
        print("prompt ou modèle différent de l'enregistrement :")
        print(f"  golden  {bundle.prompt_fingerprint[:16]}…")
        print(f"  actuel  {prompt_fingerprint(settings)[:16]}…")
        print("Relancer `record` avant de comparer des scénarios.")
        return 3

    info = market_from_bundle(bundle)
    replay_llm = ReplayLLM(bundle.decisions, default="HOLD")
    result = await run_backtest(
        symbol=bundle.symbol, info=info, h4=bundle.h4, h1=bundle.h1,
        settings=settings, propose=replay_llm,
        start_index=bundle.start_index, end_index=bundle.end_index,
    )
    print(result.summary())
    if replay_llm.missing:
        print(
            f"\ndécisions absentes du golden : {replay_llm.missing} "
            "(HOLD de scénario — la vraie décision du modèle n'est pas connue)"
        )
    if result.trades:
        print("\ndétail des trades :")
        for trade in result.trades:
            take = f"{trade.take_profit_price:.2f}" if trade.take_profit_price else "—"
            print(
                f"  {trade.entry_at:%Y-%m-%d %H:%M} → {trade.exit_at:%Y-%m-%d %H:%M} "
                f"· {trade.reason:12} · PnL {trade.pnl_quote:+.4f} "
                f"· stop {trade.stop_loss_price:.2f} · TP {take}"
            )
    print(
        f"\nscénario : stop {settings.risk.stop_atr_multiple} ATR · "
        f"TP {settings.risk.take_profit_r_multiple} R · "
        f"confiance {settings.risk.min_confidence}"
    )
    return 0


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

        async def propose(snapshot, context, has_position: bool = False):
            signal = await client.propose(
                bundle.symbol, snapshot, context,
                has_position=has_position,
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


def read_lessons(path: str | None) -> list[str] | None:
    """Lit un fichier de leçons (une par ligne) pour un scénario A/B."""
    if not path:
        return None
    text = Path(path).read_text(encoding="utf-8")
    lessons = [
        line.strip().lstrip("-• ").strip()
        for line in text.splitlines()
        if line.strip()
    ]
    return lessons or None


async def measure(args) -> int:
    """Mesure la politique règle + LLM sur plusieurs paires, sans golden.

    Ce n'est pas un portefeuille partagé : chaque paire repart de la même équité
    initiale. C'est une comparaison de comportement, pas une courbe de capital.
    """
    settings = with_overrides(load_settings(), args)
    lessons = read_lessons(args.lessons_file)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        raise SystemExit("--symbols vide")

    exchange = build_exchange(
        api_key=settings.exchange.api_key, secret=settings.exchange.secret
    )
    exchange.load_markets()
    client = SignalClient(settings.llm)
    print(f"préchauffage de {settings.llm.model}…")
    if lessons:
        print(f"leçons actives : {len(lessons)}")
    if not await client.warmup():
        raise SystemExit("modèle injoignable : mesure annulée (pas de résultat dégradé)")

    results = []
    for symbol in symbols:
        info = market_info(
            exchange, symbol,
            taker_fee=resolve_taker_fee(exchange, symbol, settings.exchange.taker_fee),
            maker_fee=settings.exchange.maker_fee,
        )
        h4 = fetch_recent(exchange, symbol, "4h")
        h1 = fetch_recent(exchange, symbol, "1h")
        start = first_index(h4, h1)
        end = min(len(h4) - 1, start + args.max_candles - 1) if args.max_candles else len(h4) - 1
        print(f"\n{symbol} : {end - start + 1} bougies évaluables", flush=True)

        async def propose(snapshot, context, has_position: bool = False, _symbol=symbol):
            return await client.propose(
                _symbol, snapshot, context,
                has_position=has_position,
                trend_buffer_pct=settings.risk.trend_buffer_pct,
                lessons=lessons,
            )

        def progress(index: int, total: int) -> None:
            if index % 25 == 0:
                print(f"  {index}/{total}", flush=True)

        result = await run_backtest(
            symbol=symbol, info=info, h4=h4, h1=h1, settings=settings,
            propose=propose, start_index=start, end_index=end,
            progress=progress, use_entry_rule=True,
        )
        results.append(result)
        print(result.summary())

    print("\n=== Synthèse multi-paires (capital indépendant par paire) ===")
    print(f"{'paire':10} {'déc.':>5} {'trades':>7} {'réussite':>9} {'PnL':>10} "
          f"{'retour':>9} {'dd max':>8} {'B&H':>8}")
    total_pnl = Decimal(0)
    total_trades = 0
    total_wins = 0
    for r in results:
        total_pnl += r.net_pnl
        total_trades += len(r.trades)
        total_wins += len(r.wins)
        print(f"{r.symbol:10} {r.decisions:5d} {len(r.trades):7d} {r.win_rate:9.1%} "
              f"{r.net_pnl:+10.4f} {r.return_pct:+9.2%} {r.max_drawdown_pct:8.2%} "
              f"{r.buy_and_hold_pct:+8.2%}")
    win_rate = Decimal(total_wins) / Decimal(total_trades) if total_trades else Decimal(0)
    print(f"\n{total_trades} trades cumulés · réussite {win_rate:.1%} · "
          f"PnL cumulé {total_pnl:+.4f} EUR")
    print("Chaque paire repart de la même équité : ce n'est pas un portefeuille partagé.")
    print("Le backtest n'est pas une preuve de rentabilité : c'est une mesure de comportement.")
    return 0


@dataclass(frozen=True, slots=True)
class _MechanicalSignal:
    """Réponse fixe qui remplace le LLM dans le balayage.

    Le mode `mechanical=True` de `run_backtest` remplit `baseline_trades` et
    laisse `trades` vide : pratique pour comparer deux séries, inutilisable pour
    lire des métriques. Un `propose` constant traverse exactement le chemin de
    production (règle d'entrée → `evaluate()` → `PaperBroker`) sans appeler le
    modèle, et les trades atterrissent dans `trades`.
    """

    action: str = "BUY"
    confidence: float = 1.0
    market_regime: str = "TRENDING_UP"
    directional_bias: str = ""
    thesis: str = "mécanique (balayage)"
    inference_ms: int = 0
    degraded: bool = False
    degradation_reason: str = ""


async def _always_buy(snapshot, context, has_position: bool = False):
    return _MechanicalSignal()


async def sweep(args) -> int:
    """Balaye le filtre de pente EMA200 sur l'historique multi-années, sans LLM.

    Objectif : répondre à « le filtre de pente est-il un avantage ou un
    sur-ajustement ? » sur ≥ 100 trades et plusieurs régimes, ce qu'une fenêtre
    de 25 jours ne peut pas faire. Le mode mécanique achète dès que la règle
    d'entrée est ouverte — le LLM est hors du champ, puisqu'il ne dimensionne
    rien et que son appel coûterait une seconde par bougie.

    Aucun ordre, aucune écriture : lecture de `market_candles_history` et
    simulation `PaperBroker`.
    """
    import asyncpg

    from app.execution.paper import PaperBroker

    settings = with_overrides(load_settings(), args)
    equity = Decimal(str(args.equity))
    settings = replace(settings, paper_start_equity=equity)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    slopes = [Decimal(s.strip()) for s in args.slopes.split(",") if s.strip()]
    fees = fee_grid(args)
    try:
        modes = [EntryMode(m.strip()) for m in args.entry_modes.split(",") if m.strip()]
    except ValueError as exc:
        raise SystemExit(
            f"--entry-modes invalide : {args.entry_modes!r} "
            f"(connus : {', '.join(m.value for m in EntryMode)})"
        ) from exc

    # Coupe-circuits permanents : en production, ils arrêtent le bot jusqu'à une
    # intervention humaine. Pour mesurer un filtre sur 24 mois, il faut pouvoir les
    # lever — sinon la fenêtre s'arrête au premier mauvais mois et N ne décrit que
    # ce mois-là. Les compteurs restent affichés pour que le gel ne passe pas
    # inaperçu (D-059).
    breaker: dict[str, object] = {}
    if args.max_consecutive_losses:
        breaker["max_consecutive_losses"] = args.max_consecutive_losses
    if args.max_hwm_drawdown is not None:
        breaker["max_drawdown_from_hwm_pct"] = Decimal(str(args.max_hwm_drawdown))

    exchange = build_exchange(
        api_key=settings.exchange.api_key, secret=settings.exchange.secret
    )
    exchange.load_markets()

    conn = await asyncpg.connect(**settings.database().asyncpg_kwargs())
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=int(args.months * 30.44))
    print(f"Fenêtre : {start:%Y-%m-%d} → {now:%Y-%m-%d} ({args.months} mois) · "
          f"équité initiale {equity} EUR · {len(symbols)} paires")
    print(f"Pente EMA200 testée (ATR sur 10 j) : "
          f"{', '.join(str(s) for s in slopes)} · frais par côté : "
          f"{', '.join(str(f) + ' pb' for f in fees)}")
    print(f"Mode(s) de la règle d'entrée : {', '.join(m.value for m in modes)}")
    if breaker:
        print(f"Coupe-circuits levés pour la mesure : {breaker}")
    print()

    rows: list[dict] = []
    try:
        for symbol in symbols:
            h4 = await load_candles(conn, symbol, "4h", start=start, end=now)
            h1 = await load_candles(
                conn, symbol, "1h", start=start - timedelta(days=10), end=now
            )
            if len(h4) < 400 or len(h1) < 300:
                print(f"{symbol} : historique insuffisant ({len(h4)} H4, {len(h1)} H1) — ignoré")
                continue
            info = market_info(
                exchange, symbol,
                taker_fee=resolve_taker_fee(exchange, symbol, settings.exchange.taker_fee),
                maker_fee=settings.exchange.maker_fee,
            )
            window_gaps = detect_gaps(h4, "4h")
            start_index = first_index(h4, h1)
            print(
                f"{symbol} : {len(h4)} H4 ({h4[0][0] and datetime.fromtimestamp(h4[0][0]/1000, tz=timezone.utc):%Y-%m-%d} → "
                f"{datetime.fromtimestamp(h4[-1][0]/1000, tz=timezone.utc):%Y-%m-%d}) · "
                f"{len(h1)} H1 · {len(window_gaps)} trou(s) H4 dans la fenêtre"
            )
            for mode in modes:
                for slope in slopes:
                    for fee_bps in fees:
                        scenario = replace(
                            settings,
                            risk=replace(
                                settings.risk,
                                entry_rule_enabled=True,
                                entry_ema200_slope_min=slope,
                                entry_mode=mode,
                                **breaker,
                            ),
                        )
                        broker = PaperBroker(
                            fee_bps / Decimal(10_000), settings.paper_slippage_bps
                        )
                        result = await run_backtest(
                            symbol=symbol,
                            info=info,
                            h4=h4,
                            h1=h1,
                            settings=scenario,
                            propose=_always_buy,
                            broker=broker,
                            mechanical=False,
                            counterfactuals=False,
                            start_index=start_index,
                        )
                        rows.append({
                            "symbol": symbol,
                            "mode": str(mode),
                            "slope": str(slope),
                            "fee_bps": str(fee_bps),
                            "trades": len(result.trades),
                            "win_rate": str(result.win_rate),
                            "profit_factor": (
                                str(result.profit_factor)
                                if result.profit_factor is not None else None
                            ),
                            "expectancy": str(result.expectancy_quote),
                            "net_pnl": str(result.net_pnl),
                            "return_pct": str(result.return_pct),
                            "max_drawdown_pct": str(result.max_drawdown_pct),
                            # Coupe-circuits permanents : s'ils ont rejeté, la
                            # fenêtre mesurée est plus courte que demandée.
                            "gel": int(
                                result.rejections.get("CIRCUIT_BREAKER_CONSECUTIVE_LOSSES", 0)
                                + result.rejections.get("CIRCUIT_BREAKER_MAX_DRAWDOWN", 0)
                            ),
                            "last_exit": (
                                result.trades[-1].exit_at.strftime("%Y-%m-%d")
                                if result.trades else "—"
                            ),
                            "result": result,
                        })
    finally:
        await conn.close()

    _print_sweep(rows)
    if args.json:
        payload = [
            {key: value for key, value in row.items() if key != "result"}
            for row in rows
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


async def swing_sweep(args) -> int:
    """Mesure SWING_MACRO_D1 sur l'historique multi-années, sans LLM.

    Le Daily est agrégé depuis le H4 en base (6 bougies complètes par jour) : aucun
    trou n'est interpolé, une journée incomplète est ignorée. Le pare-feu et le
    broker papier sont ceux de la production ; seule la cadence change.
    """
    import asyncpg

    from app.execution.paper import PaperBroker
    from app.market.feed import D1_WARMUP

    settings = with_overrides(load_settings(), args)
    equity = Decimal(str(args.equity))
    settings = replace(settings, paper_start_equity=equity)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    fees = fee_grid(args)
    triggers = [t.strip().lower() for t in args.swing_triggers.split(",") if t.strip()]
    valid = {"both", "donchian", "value"}
    if not triggers or set(triggers) - valid:
        raise SystemExit(
            f"--swing-triggers invalide : {args.swing_triggers!r} "
            f"(connus : {', '.join(sorted(valid))})"
        )
    raw_exit_modes = [m.strip().lower() for m in args.exit_mode.split(",") if m.strip()]
    exit_modes: list[SwingExitMode] = []
    for raw in raw_exit_modes:
        try:
            exit_modes.append(SwingExitMode(raw.upper()))
        except ValueError as exc:
            raise SystemExit(
                f"--exit-mode invalide : {raw!r} "
                f"(connus : {', '.join(m.value.lower() for m in SwingExitMode)})"
            ) from exc
    if not exit_modes:
        raise SystemExit("--exit-mode ne peut pas être vide")

    exchange = build_exchange(
        api_key=settings.exchange.api_key, secret=settings.exchange.secret
    )
    exchange.load_markets()

    conn = await asyncpg.connect(**settings.database().asyncpg_kwargs())
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=int(args.months * 30.44)) if args.months > 0 else None
    print(f"Fenêtre : {start:%Y-%m-%d} → {now:%Y-%m-%d}" if start else "Fenêtre : tout l'historique")
    print(f"Équité initiale {equity} EUR · frais par côté : "
          f"{', '.join(str(f) + ' pb' for f in fees)} · "
          f"déclencheurs : {', '.join(triggers)} · "
          f"sorties : {', '.join(m.value.lower() for m in exit_modes)}")
    if args.small_account:
        print("Petit compte : allocation 100 % autorisée sous "
              f"{settings.risk.small_account_threshold_quote} EUR")
    breaker: dict[str, object] = {}
    if args.max_consecutive_losses:
        breaker["max_consecutive_losses"] = args.max_consecutive_losses
    if args.max_hwm_drawdown is not None:
        breaker["max_drawdown_from_hwm_pct"] = Decimal(str(args.max_hwm_drawdown))
    if breaker:
        print(f"Coupe-circuits levés pour la mesure : {breaker}")
    print()

    rows: list[dict] = []
    try:
        for symbol in symbols:
            h4 = await load_candles(conn, symbol, "4h", start=start, end=now)
            if not h4:
                print(f"{symbol} : aucun H4 en base — ignoré")
                continue
            d1 = aggregate(h4, "4h", "1d")
            if len(d1) < D1_WARMUP + 30:
                print(f"{symbol} : {len(d1)} D1 seulement — ignoré")
                continue
            info = market_info(
                exchange, symbol,
                taker_fee=resolve_taker_fee(exchange, symbol, settings.exchange.taker_fee),
                maker_fee=settings.exchange.maker_fee,
            )
            print(
                f"{symbol} : {len(h4)} H4 → {len(d1)} D1 "
                f"({datetime.fromtimestamp(d1[0][0]/1000, tz=timezone.utc):%Y-%m-%d} → "
                f"{datetime.fromtimestamp(d1[-1][0]/1000, tz=timezone.utc):%Y-%m-%d})"
            )
            for trigger in triggers:
                flags = {
                    "both": (True, True),
                    "donchian": (True, False),
                    "value": (False, True),
                }[trigger]
                for exit_mode in exit_modes:
                    for fee_bps in fees:
                        scenario = replace(
                            settings,
                            risk=replace(
                                settings.risk,
                                entry_mode=EntryMode.SWING_MACRO_D1,
                                stop_mode=StopMode.STRUCTURAL_D1,
                                take_profit_r_multiple=Decimal(0),
                                swing_require_donchian=flags[0],
                                swing_require_value_rebound=flags[1],
                                swing_exit_mode=exit_mode,
                                small_account_full_allocation=args.small_account,
                                **breaker,
                            ),
                        )
                        broker = PaperBroker(
                            fee_bps / Decimal(10_000), settings.paper_slippage_bps
                        )
                        result = run_swing_backtest(
                            symbol=symbol, info=info, d1=d1, settings=scenario,
                            broker=broker,
                        )
                        rows.append({
                            "symbol": symbol,
                            "trigger": trigger,
                            "exit_mode": exit_mode.value.lower(),
                            "fee_bps": str(fee_bps),
                            "d1": len(d1),
                            "trades": len(result.trades),
                            "per_year": str(result.trades_per_year),
                            "win_rate": str(result.win_rate),
                            "payoff": (
                                str(result.payoff_ratio)
                                if result.payoff_ratio is not None else None
                            ),
                            "profit_factor": (
                                str(result.profit_factor)
                                if result.profit_factor is not None else None
                            ),
                            "expectancy": str(result.expectancy_quote),
                            "net_pnl": str(result.net_pnl),
                            "return_pct": str(result.return_pct),
                            "max_drawdown_pct": str(result.max_drawdown_pct),
                            "buy_and_hold_pct": str(result.buy_and_hold_pct),
                            "rejections": dict(result.rejections),
                            "gel": int(
                                result.rejections.get(
                                    "CIRCUIT_BREAKER_CONSECUTIVE_LOSSES", 0
                                )
                                + result.rejections.get(
                                    "CIRCUIT_BREAKER_MAX_DRAWDOWN", 0
                                )
                            ),
                            "last_exit": (
                                result.trades[-1].exit_at.strftime("%Y-%m-%d")
                                if result.trades else "—"
                            ),
                            "result": result,
                        })
    finally:
        await conn.close()

    _print_swing(rows)
    if args.json:
        payload = [
            {key: value for key, value in row.items() if key != "result"}
            for row in rows
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _swing_verdict(row: dict) -> str:
    """Critères swing D-061 : N, payoff, PF et drawdown, jamais le winrate.

    Un trend-following à fort payoff gagne rarement : imposer 40 % de réussite
    sélectionne des stratégies de moyenne inverse, pas cette famille de sorties.
    """
    reasons = []
    if int(row["trades"]) < 50:
        reasons.append("N<50")
    payoff = row["payoff"]
    if payoff is None or Decimal(payoff) < Decimal("3.5"):
        reasons.append("payoff<3.5")
    pf = row["profit_factor"]
    if pf is None or Decimal(pf) < Decimal("1.35"):
        reasons.append("PF<1.35")
    if Decimal(row["max_drawdown_pct"]) > Decimal("0.20"):
        reasons.append("DD>20%")
    if Decimal(row["net_pnl"]) <= 0:
        reasons.append("net≤0")
    return "OK" if not reasons else "KO " + ",".join(reasons)


def _print_swing(rows: list[dict]) -> None:
    if not rows:
        print("Aucun scénario mesuré.")
        return
    header = (
        f"{'paire':10s} {'déclencheur':>11s} {'sortie':>11s} {'frais':>7s} {'D1':>5s} "
        f"{'trades':>7s} {'N/an':>6s} {'réussite':>9s} {'payoff':>7s} {'PF':>6s} "
        f"{'net':>10s} {'retour':>9s} {'dd max':>8s} {'B&H':>9s} {'gel':>4s} "
        f"{'dernier':>10s}  verdict"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        payoff = "n/a" if row["payoff"] is None else f"{Decimal(row['payoff']):.2f}"
        pf = "n/a" if row["profit_factor"] is None else f"{Decimal(row['profit_factor']):.2f}"
        print(
            f"{row['symbol']:10s} {row['trigger']:>11s} {row['exit_mode']:>11s} "
            f"{row['fee_bps'] + 'pb':>7s} "
            f"{row['d1']:5d} {row['trades']:7d} {Decimal(row['per_year']):6.1f} "
            f"{Decimal(row['win_rate']):9.1%} {payoff:>7s} {pf:>6s} "
            f"{Decimal(row['net_pnl']):+10.4f} {Decimal(row['return_pct']):+9.2%} "
            f"{Decimal(row['max_drawdown_pct']):8.2%} "
            f"{Decimal(row['buy_and_hold_pct']):+9.2%} {row['gel']:4d} "
            f"{row['last_exit']:>10s}  {_swing_verdict(row)}"
        )
    print()
    print("Critères D-061 : N ≥ 50 trades · payoff ≥ 3,5 · PF ≥ 1,35 net de frais · "
          "drawdown max ≤ 20 % · net > 0. La réussite et le nombre de trades par an "
          "restent affichés à titre informatif, pas comme critères.")
    print("D1 est agrégé depuis le H4 en base ; une journée incomplète est ignorée, "
          "jamais interpolée.")
    print("Chaque paire repart de la même équité : ce n'est pas un portefeuille partagé.")


def _verdict(row: dict, *, min_trades: int, min_pf: Decimal,
             max_dd: Decimal) -> str:
    """Verdict par scénario, critères du brief : N, PF, espérance, drawdown."""
    reasons = []
    if row["trades"] < min_trades:
        reasons.append(f"N<{min_trades}")
    pf = row["profit_factor"]
    if pf is None or Decimal(pf) < min_pf:
        reasons.append(f"PF<{min_pf}")
    if Decimal(row["expectancy"]) <= 0:
        reasons.append("E≤0")
    if Decimal(row["max_drawdown_pct"]) > max_dd:
        reasons.append(f"DD>{max_dd:.0%}")
    if row.get("gel", 0) > 0:
        reasons.append("GEL")
    return "OK" if not reasons else "KO " + ",".join(reasons)


def _print_sweep(rows: list[dict]) -> None:
    if not rows:
        print("Aucun scénario mesuré.")
        return
    min_pf = Decimal("1.30")
    max_dd = Decimal("0.15")
    header = (
        f"{'paire':10s} {'mode':>12s} {'pente':>6s} {'frais':>7s} {'trades':>7s} "
        f"{'réussite':>9s} {'PF':>6s} {'espérance':>11s} {'net':>10s} {'dd max':>8s} "
        f"{'gel':>4s} {'dernier':>10s}  verdict"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        pf = row["profit_factor"]
        label = "n/a" if pf is None else f"{Decimal(pf):.2f}"
        print(
            f"{row['symbol']:10s} {row['mode']:>12s} {row['slope']:>6s} "
            f"{row['fee_bps'] + 'pb':>7s} "
            f"{row['trades']:7d} {Decimal(row['win_rate']):9.1%} {label:>6s} "
            f"{Decimal(row['expectancy']):+11.4f} {Decimal(row['net_pnl']):+10.4f} "
            f"{Decimal(row['max_drawdown_pct']):8.2%} "
            f"{row['gel']:4d} {row['last_exit']:>10s}  "
            f"{_verdict(row, min_trades=100, min_pf=min_pf, max_dd=max_dd)}"
        )
    print()
    print("Critères du brief : N ≥ 100 trades · PF ≥ 1.30 · espérance > 0 · "
          "drawdown max ≤ 15 %.")
    print("gel = rejets par coupe-circuit permanent (pertes consécutives ou drawdown "
          "depuis le plus haut) : la fenêtre mesurée s'arrête là. Lever les plafonds "
          "avec --max-consecutive-losses / --max-hwm-drawdown pour mesurer le filtre "
          "sur toute la fenêtre.")
    print("Le mode mécanique achète dès que la règle d'entrée s'ouvre : il mesure "
          "le filtre, pas la qualité des décisions du modèle.")


async def mean_reversion_sweep(args) -> int:
    """Mesure MEAN_REVERSION_BB, sans LLM, sur H4 et D1 agrégé.

    Deux variantes par scénario :

    - ``gated`` : la configuration exécutable (filtre EMA200 du pare-feu inclus) ;
    - ``raw``   : le même filtre neutralisé, pour mesurer l'hypothèse elle-même.
      **Cette variante n'est pas activable en production.**
    """
    import asyncpg

    from app.execution.paper import PaperBroker

    settings = with_overrides(load_settings(), args)
    equity = Decimal(str(args.equity))
    settings = replace(settings, paper_start_equity=equity)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    fees = fee_grid(args)
    timeframes = [t.strip().lower() for t in args.timeframes.split(",") if t.strip()]
    for timeframe in timeframes:
        if timeframe not in {"4h", "1d"}:
            raise SystemExit(f"--timeframes invalide : {timeframe!r} (connus : 4h, 1d)")
    exit_modes = [m.strip().lower() for m in args.mr_exit.split(",") if m.strip()]
    for mode in exit_modes:
        if mode not in {"touch", "close"}:
            raise SystemExit(f"--mr-exit invalide : {mode!r} (connus : touch, close)")
    variants = [v.strip().lower() for v in args.mr_variants.split(",") if v.strip()]
    for variant in variants:
        if variant not in {"gated", "raw"}:
            raise SystemExit(f"--mr-variants invalide : {variant!r} (connus : gated, raw)")
    rsi_values = [Decimal(v.strip()) for v in args.mr_rsi.split(",") if v.strip()]
    if not (timeframes and exit_modes and variants and rsi_values and fees):
        raise SystemExit("balayage mean-reversion vide : vérifier les listes")

    exchange = build_exchange(
        api_key=settings.exchange.api_key, secret=settings.exchange.secret
    )
    exchange.load_markets()

    conn = await asyncpg.connect(**settings.database().asyncpg_kwargs())
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=int(args.months * 30.44)) if args.months > 0 else None
    print(f"Fenêtre : {start:%Y-%m-%d} → {now:%Y-%m-%d}" if start else "Fenêtre : tout l'historique")
    print(f"Équité initiale {equity} EUR · frais par côté : "
          f"{', '.join(str(f) + ' pb' for f in fees)} · "
          f"timeframes : {', '.join(timeframes)} · sorties : {', '.join(exit_modes)} · "
          f"RSI max : {', '.join(str(v) for v in rsi_values)}")
    print("Variantes : gated (exécutable) · raw (filtre EMA200 neutralisé, laboratoire)")
    print()

    rows: list[dict] = []
    try:
        for symbol in symbols:
            h4 = await load_candles(conn, symbol, "4h", start=start, end=now)
            if not h4:
                print(f"{symbol} : aucun H4 en base — ignoré")
                continue
            series_by_timeframe = {
                "4h": h4,
                "1d": aggregate(h4, "4h", "1d"),
            }
            info = market_info(
                exchange, symbol,
                taker_fee=resolve_taker_fee(exchange, symbol, settings.exchange.taker_fee),
                maker_fee=settings.exchange.maker_fee,
            )
            for timeframe in timeframes:
                candles = series_by_timeframe[timeframe]
                if len(candles) < MR_WARMUP + 30:
                    print(f"{symbol} {timeframe} : {len(candles)} bougies seulement — ignoré")
                    continue
                print(
                    f"{symbol} {timeframe} : {len(candles)} bougies "
                    f"({datetime.fromtimestamp(candles[0][0]/1000, tz=timezone.utc):%Y-%m-%d} → "
                    f"{datetime.fromtimestamp(candles[-1][0]/1000, tz=timezone.utc):%Y-%m-%d})"
                )
                for variant in variants:
                    for rsi_max in rsi_values:
                        for exit_mode in exit_modes:
                            for fee_bps in fees:
                                broker = PaperBroker(
                                    fee_bps / Decimal(10_000),
                                    settings.paper_slippage_bps,
                                )
                                result = run_mean_reversion_backtest(
                                    symbol=symbol, info=info, candles=candles,
                                    settings=settings, broker=broker,
                                    rsi_max=rsi_max, exit_mode=exit_mode,
                                    allow_counter_trend=variant == "raw",
                                )
                                rows.append({
                                    "symbol": symbol,
                                    "timeframe": timeframe,
                                    "variant": variant,
                                    "rsi_max": str(rsi_max),
                                    "exit_mode": exit_mode,
                                    "fee_bps": str(fee_bps),
                                    "candles": len(candles),
                                    "trades": len(result.trades),
                                    "per_year": str(result.trades_per_year),
                                    "win_rate": str(result.win_rate),
                                    "payoff": (
                                        str(result.payoff_ratio)
                                        if result.payoff_ratio is not None else None
                                    ),
                                    "profit_factor": (
                                        str(result.profit_factor)
                                        if result.profit_factor is not None else None
                                    ),
                                    "expectancy": str(result.expectancy_quote),
                                    "net_pnl": str(result.net_pnl),
                                    "return_pct": str(result.return_pct),
                                    "max_drawdown_pct": str(result.max_drawdown_pct),
                                    "buy_and_hold_pct": str(result.buy_and_hold_pct),
                                    "rejections": dict(result.rejections),
                                    "counter_trend": result.rejections.get(
                                        "REJECTED_COUNTER_TREND_EMA200", 0
                                    ),
                                    "last_exit": (
                                        result.trades[-1].exit_at.strftime("%Y-%m-%d")
                                        if result.trades else "—"
                                    ),
                                    "result": result,
                                })
    finally:
        await conn.close()

    _print_mean_reversion(rows)
    if args.json:
        payload = [
            {key: value for key, value in row.items() if key != "result"}
            for row in rows
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _meanrev_verdict(row: dict) -> str:
    """Verdict de laboratoire : PF ≥ 1,30, N ≥ 30, net > 0, DD ≤ 20 %.

    Le seuil d'activation réel reste PF ≥ 1,30 **et** N ≥ 100 sur la fenêtre :
    un scénario « OK » ici est un candidat à mesurer davantage, pas un feu vert.
    """
    reasons = []
    if int(row["trades"]) < 30:
        reasons.append("N<30")
    pf = row["profit_factor"]
    if pf is None or Decimal(pf) < Decimal("1.30"):
        reasons.append("PF<1.30")
    if Decimal(row["net_pnl"]) <= 0:
        reasons.append("net≤0")
    if Decimal(row["max_drawdown_pct"]) > Decimal("0.20"):
        reasons.append("DD>20%")
    return "OK" if not reasons else "KO " + ",".join(reasons)


def _print_mean_reversion(rows: list[dict]) -> None:
    if not rows:
        print("Aucun scénario mesuré.")
        return
    header = (
        f"{'paire':10s} {'tf':>3s} {'variante':>8s} {'rsi':>4s} {'sortie':>6s} "
        f"{'frais':>7s} {'bougies':>7s} {'trades':>7s} {'N/an':>6s} {'réussite':>9s} "
        f"{'payoff':>7s} {'PF':>6s} {'net':>10s} {'retour':>9s} {'dd max':>8s} "
        f"{'rejets':>7s} {'B&H':>9s}  verdict"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        payoff = "n/a" if row["payoff"] is None else f"{Decimal(row['payoff']):.2f}"
        pf = "n/a" if row["profit_factor"] is None else f"{Decimal(row['profit_factor']):.2f}"
        print(
            f"{row['symbol']:10s} {row['timeframe']:>3s} {row['variant']:>8s} "
            f"{row['rsi_max']:>4s} {row['exit_mode']:>6s} {row['fee_bps'] + 'pb':>7s} "
            f"{row['candles']:7d} {row['trades']:7d} {Decimal(row['per_year']):6.1f} "
            f"{Decimal(row['win_rate']):9.1%} {payoff:>7s} {pf:>6s} "
            f"{Decimal(row['net_pnl']):+10.4f} {Decimal(row['return_pct']):+9.2%} "
            f"{Decimal(row['max_drawdown_pct']):8.2%} {row['counter_trend']:7d} "
            f"{Decimal(row['buy_and_hold_pct']):+9.2%}  {_meanrev_verdict(row)}"
        )
    print()
    print("Variante `raw` : filtre EMA200 neutralisé — mesure de l'hypothèse, "
          "jamais exécutable. `gated` : configuration réelle du pare-feu.")
    print("Critères de laboratoire : N ≥ 30 · PF ≥ 1,30 · net > 0 · DD ≤ 20 %. "
          "L'activation réelle exige en plus N ≥ 100 sur 2021-2026.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest historique Trading Ninja")
    parser.add_argument("command", choices=(
        "record", "replay", "scenario", "watch", "measure", "sweep", "swing",
        "meanrev",
    ))
    parser.add_argument("--golden", default=FIXTURE)
    parser.add_argument("--symbol", default="BTC/EUR")
    parser.add_argument(
        "--symbols",
        default="BTC/EUR,ETH/EUR,SOL/EUR,ADA/EUR,DOGE/EUR,PAXG/EUR,HBAR/EUR,BNB/EUR",
        help="paires de la commande measure (séparées par des virgules)",
    )
    parser.add_argument("--max-candles", type=int, default=0,
                        help="limite la fenêtre (utile pour un essai rapide)")
    parser.add_argument("--bless", action="store_true",
                        help="met à jour les métriques attendues du golden")
    parser.add_argument("--min-confidence", type=float, default=None,
                        help="scénario : seuil de confiance du pare-feu (défaut .env)")
    parser.add_argument("--stop-atr", type=float, default=None,
                        help="scénario : multiple ATR du stop (défaut .env)")
    parser.add_argument("--take-profit-r", type=float, default=None,
                        help="scénario : multiple R du take-profit, 0 = pas de TP (défaut .env)")
    parser.add_argument("--require-positive-h1", action="store_true",
                        help="scénario : n'acheter que si la dernière H1 clôturée est positive")
    parser.add_argument("--lessons-file", default=None,
                        help="mesure : fichier de leçons (une par ligne) injecté dans le prompt")
    parser.add_argument("--months", type=int, default=24,
                        help="sweep : profondeur de la fenêtre en mois (défaut 24)")
    parser.add_argument("--equity", type=float, default=1000.0,
                        help="sweep : équité virtuelle de départ en EUR (défaut 1000)")
    parser.add_argument("--slopes", default="0,1.0,1.5,2.0",
                        help="sweep : pentes EMA200 testées, en ATR sur 10 jours")
    parser.add_argument("--entry-modes", default="EMA50_H4",
                        help="sweep : modes de la règle d'entrée testés "
                             "(EMA50_H4, PULLBACK_H1)")
    parser.add_argument("--max-consecutive-losses", type=int, default=0,
                        help="sweep : plafond de pertes consécutives (0 = valeur du .env)")
    parser.add_argument("--max-hwm-drawdown", type=float, default=None,
                        help="sweep : drawdown max depuis le plus haut, en fraction "
                             "(1.0 lève le coupe-circuit)")
    parser.add_argument("--fee-bps", default=None,
                        help="sweep : frais par côté en points de base "
                             "(défaut 80,100 = taker Kraken réel)")
    parser.add_argument("--maker-fees", nargs="?", const="25", default=None,
                        help="sweep/swing/meanrev : frais maker par côté en points de base "
                             "(25 si l'option est passée sans valeur). Seul, remplace la "
                             "grille ; avec --fee-bps, l'ajoute. Sensibilité de coût, pas "
                             "un modèle d'exécution maker.")
    parser.add_argument("--swing-triggers", default="both,donchian,value",
                        help="swing : déclencheurs testés (both, donchian, value)")
    parser.add_argument("--exit-mode", default="ema20",
                        help="swing : sorties testées (ema20, chandelier), "
                             "séparées par des virgules")
    parser.add_argument("--small-account", action="store_true",
                        help="swing : autoriser 100 %% de l'équité sous le seuil petit compte")
    parser.add_argument("--timeframes", default="4h,1d",
                        help="meanrev : timeframes testés (4h, 1d)")
    parser.add_argument("--mr-rsi", default="30,32,35",
                        help="meanrev : seuils RSI max testés (défaut 30,32,35)")
    parser.add_argument("--mr-exit", default="touch,close",
                        help="meanrev : sorties testées (touch = bande médiane touchée, "
                             "close = clôture au-dessus)")
    parser.add_argument("--mr-variants", default="gated,raw",
                        help="meanrev : variantes (gated = pare-feu complet, "
                             "raw = filtre EMA200 neutralisé)")
    parser.add_argument("--json", action="store_true",
                        help="sweep : sortie JSON en plus du tableau")
    args = parser.parse_args()
    if args.command == "record":
        return asyncio.run(record(args))
    if args.command == "replay":
        return asyncio.run(replay(args))
    if args.command == "scenario":
        return asyncio.run(scenario(args))
    if args.command == "measure":
        return asyncio.run(measure(args))
    if args.command == "sweep":
        return asyncio.run(sweep(args))
    if args.command == "swing":
        return asyncio.run(swing_sweep(args))
    if args.command == "meanrev":
        return asyncio.run(mean_reversion_sweep(args))
    return asyncio.run(watch(args))


if __name__ == "__main__":
    raise SystemExit(main())
