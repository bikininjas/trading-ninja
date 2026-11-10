"""Portefeuilles indépendants sur le véritable runtime, données entièrement locales."""

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import random
import uuid

from app.core.settings import load_settings
from app.db.repository import AccountSnapshot, PositionRow
from app.engine.capital import start_capital, allocate_close
from app.execution.paper import PaperBroker
from app.insights.codec import digest, public, encode, code_fingerprint
from app.insights.policy import STRATEGIC
from app.llm.client import LLMSignal
from app.market.feed import TIMEFRAME_MS


class MemoryLedger:
    """Ledger d'expérience isolé, sans connexion SQL et sans transport exchange."""

    def __init__(self, equity, now):
        self.current = "IDLE"
        self.cooldown = None
        self.position = None
        self.cash = equity
        self.total = equity
        self.hwm = equity
        self.reserve = self.growth = D(0)
        self.losses = 0
        self.now = now
        self.snapshots = []
        self.orders = []
        self.closed = []
        self.signals = {}
        self.policies = {}
        self.policies_by_position = {}
        self.capital = None
        self.deferred = D(0)

    @asynccontextmanager
    async def cycle_lock(self, *a):
        yield

    @asynccontextmanager
    async def transaction(self):
        state = deepcopy(self.__dict__)
        try:
            yield
        except BaseException:
            self.__dict__.update(state)
            raise

    async def ensure_mode(self, *a):
        pass

    async def state(self, *a):
        return self.current, self.cooldown

    async def transition(self, mode, expected, target, reason, code=None, context=None):
        if self.current != expected:
            raise ValueError("transition concurrente dans une expérience")
        self.current = target

    async def force_halt(self, mode, reason, code=None, auto_resume_at=None):
        self.current = "EMERGENCY_HALT"
        self.cooldown = auto_resume_at
        self.halt_reason=reason

    async def force_resume(self, *a, **k):
        self.current = "IDLE"
        self.cooldown = None

    async def halt_details(self, *a):
        return getattr(self,"halt_reason","EXPERIMENT_HALT")

    async def set_cooldown(self, mode, until):
        self.cooldown = until

    async def note_error(self, *a):
        pass

    async def runtime_settings(self, *a):
        return {}, None

    async def active_lessons(self, *a):
        return []

    async def open_position(self, *a):
        return self.position

    async def open_positions(self, *a):
        return [self.position] if self.position else []

    async def account(self, mode, starting):
        day = [e for at, e in self.snapshots if at.date() == self.now.date()]
        week = [e for at, e in self.snapshots if at >= self.now - timedelta(days=7)]
        dd = lambda peak: (
            max(D(0), 1 - self.total / max(peak, self.total))
            if max(peak, self.total) > 0
            else D(0)
        )
        hwm_drawdown = dd(self.hwm)
        if self.capital:
            active_peak = self.capital.active_quote + max(
                D(0), self.capital.surplus_hwm - self.capital.realized_pnl
            )
            if active_peak > 0:
                hwm_drawdown = max(
                    hwm_drawdown,
                    1 - (self.total - self.reserve - self.growth) / active_peak,
                )
        return AccountSnapshot(
            self.total,
            self.cash,
            self.hwm,
            dd(max(day, default=self.total)),
            dd(max(week, default=self.total)),
            hwm_drawdown,
            self.losses,
            sum(
                o["created_at"] > self.now - timedelta(hours=1)
                and o.get("status") != "REJECTED"
                for o in self.orders
            ),
            bool(self.position),
            self.reserve,
            self.growth,
            self.capital.promotions if self.capital else 0,
            self.capital.last_promotion_at if self.capital else None,
        )

    async def snapshot_equity(
        self, mode, *, equity, free_quote, position_value_quote, **kw
    ):
        self.total, self.cash = equity, free_quote
        self.hwm = max(self.hwm, equity)
        self.snapshots.append((self.now, equity))

    async def insert_signal(self, mode, symbol, model_name, **kw):
        key = uuid.uuid4()
        self.signals[symbol] = self.now
        return key, self.now

    async def last_signal_at(self, mode, symbol):
        return self.signals.get(symbol)

    async def insert_evaluation(self, *a, **kw):
        return uuid.uuid4(), self.now

    async def insert_shadow_trade(self, *a, **kw):
        return uuid.uuid4()

    async def create_position(self, mode, **kw):
        self.position = PositionRow(
            uuid.uuid4(),
            kw["symbol"],
            kw["entry_price"],
            kw["amount"],
            kw["stop_loss_price"],
            kw["take_profit_price"],
            self.now,
            kw["risk_amount_quote"],
            self.now,
        )
        return self.position.id

    async def insert_order(self, mode, **kw):
        self.orders.append({**kw, "created_at": self.now})
        return uuid.uuid4()

    async def entry_fee(self, position_id):
        return sum(
            (
                o["fee_amount"]
                for o in self.orders
                if o.get("position_id") == position_id and o["purpose"] == "ENTRY"
            ),
            D(0),
        )

    async def close_position(self, pid, **kw):
        if not self.position or self.position.id != pid:
            raise ValueError("position inexistante")
        self.closed.append(
            {
                **kw,
                "symbol": self.position.symbol,
                "opened_at": self.position.opened_at,
                "closed_at": self.now,
                "risk_quote": self.position.risk_amount_quote,
            }
        )
        self.position = None

    async def update_after_close(self, mode, *, consecutive_losses, equity):
        self.losses = consecutive_losses
        self.hwm = max(self.hwm, equity)

    async def update_position_stop(self, pid, stop):
        self.position = replace(
            self.position, stop_loss_price=max(stop, self.position.stop_loss_price)
        )

    async def advance_paper_position(self, pos, stop, close_at):
        self.position = replace(
            pos,
            stop_loss_price=max(stop, pos.stop_loss_price),
            monitor_cursor_at=close_at,
        )

    async def deferred_sweep_quote(self, *a):
        return self.deferred

    async def insert_sweep(self, mode, **kw):
        if kw["status"] in ("SUCCESS", "SIMULATED"):
            self.reserve += kw["swept_amount"]
            self.deferred = D(0)
        elif kw["status"] == "SKIPPED_BELOW_MINIMUM":
            self.deferred += kw["computed_sweep_quote"]

    async def capital_state(self, *a):
        return self.capital

    async def ensure_capital(self, mode, quote, equity, reserve, at):
        if self.capital is None:
            self.capital = start_capital(equity, at, reserve=reserve)
        return self.capital

    async def allocate_capital(self, mode, pid, pnl, at, policy, *, allow_promotion):
        self.capital = allocate_close(
            self.capital, pnl, at, policy, allow_promotion=allow_promotion
        )
        self.reserve, self.growth = (
            self.capital.reserve_quote,
            self.capital.growth_quote,
        )
        return self.capital

    async def save_policy(self, policy):
        self.policies[policy["id"]] = deepcopy(policy)
        return policy["id"]

    async def attach_policy(self, mode, pid, policy):
        self.policies_by_position[pid] = deepcopy(policy)
        if self.position:
            self.position = replace(self.position, policy_id=policy["id"])
        return pid

    async def position_policy(self, pid):
        return self.policies_by_position.get(pid)

    async def entry_control(self, *a):
        return {"enabled": True, "revision": 0}


class HistoricalExchange:
    def __init__(self, dataset, now):
        self.dataset, self.now = dataset, now
        self.markets = {}
        for symbol, item in dataset.items():
            m = item["market"]
            base, quote = symbol.split("/")
            self.markets[symbol] = {
                "symbol": symbol,
                "id": symbol.replace("/", ""),
                "base": base,
                "quote": quote,
                "spot": True,
                "active": True,
                "precision": {"amount": m["amount_step"], "price": m["price_tick"]},
                "limits": {
                    "amount": {"min": m["min_amount"]},
                    "cost": {"min": m["min_notional"]},
                },
                "taker": m["taker_fee"],
                "maker": m.get("maker_fee", m["taker_fee"]),
            }

    def market(self, symbol):
        return self.markets[symbol]

    def milliseconds(self):
        return int(self.now.timestamp() * 1000)

    def fetch_ohlcv(self, symbol, timeframe, limit=720):
        return [
            list(c)
            for c in self.dataset[symbol]["candles"].get(timeframe, [])
            if c[0] + TIMEFRAME_MS[timeframe] <= self.milliseconds()
        ][-limit:]

    def fetch_ticker(self, symbol):
        timeframe = "1h"
        bars = self.fetch_ohlcv(symbol, timeframe)
        if not bars:
            timeframe = "4h"
            bars = self.fetch_ohlcv(symbol, timeframe)
        if not bars:
            raise ValueError("aucun prix historique disponible")
        return {"last": bars[-1][4], "timestamp": bars[-1][0] + TIMEFRAME_MS[timeframe]}

    def fetch_order_book(self, symbol, limit=20):
        # No invented historical depth. A recorded book may be supplied explicitly.
        books = self.dataset[symbol].get("books", [])
        available = [b for b in books if b["timestamp"] <= self.milliseconds()]
        return (
            available[-1] if available else {"bids": [], "asks": [], "timestamp": None}
        )


class ExperimentSignal:
    def __init__(self, variant):
        self.variant = variant
        self.by_time = {
            (r["symbol"], r["at"], bool(r.get("has_position", False))): r
            for r in variant.get("signals", [])
        }
        if len(self.by_time) != len(variant.get("signals", [])):
            raise ValueError("signaux historiques dupliqués")
        self.missing = 0

    async def propose(self, symbol, snapshot, context, *, has_position=False, **kw):
        if self.variant["kind"] == "mechanical":
            record = {
                "action": "HOLD" if has_position else "BUY",
                "confidence": "1",
                "market_regime": "TRENDING_UP",
            }
        else:
            record = self.by_time.get(
                (symbol, snapshot.tick_at.isoformat(), has_position)
            )
            if record is None:
                self.missing += 1
                raise ValueError(
                    f"signal enregistré manquant : {symbol} {snapshot.tick_at.isoformat()} position={has_position}"
                )
        return LLMSignal(
            record["action"],
            D(str(record["confidence"])),
            record["market_regime"],
            record.get("directional_bias", "NEUTRAL"),
            record.get("thesis", "expérience figée"),
            {},
            {},
            "",
            0,
            degraded=bool(record.get("degraded", False)),
        )


def validate_experiment(spec):
    if spec.get("schema_version") != 1:
        raise ValueError("schéma expérience inconnu")
    if set(spec) - {
        "schema_version",
        "variants",
        "data",
        "start_at",
        "end_at",
        "equity",
        "validation_window",
        "hypothesis",
    }:
        raise ValueError("champ d’expérience non autorisé")
    equity = D(str(spec.get("equity", "1000")))
    if not equity.is_finite() or equity <= 0:
        raise ValueError("capital d’expérience fini et positif requis")
    variants = spec.get("variants", [])
    if not 1 <= len(variants) <= 3 or len({v["name"] for v in variants}) != len(
        variants
    ):
        raise ValueError("une à trois variantes de noms distincts requises")
    for v in variants:
        if set(v) - {"name", "kind", "risk_overrides", "signals"}:
            raise ValueError("champ de variante non autorisé")
        if not isinstance(v["name"], str) or not 1 <= len(v["name"]) <= 80:
            raise ValueError("nom de variante invalide")
        if len(v.get("signals", [])) > 20000:
            raise ValueError("trop de signaux enregistrés")
        for signal in v.get("signals", []):
            if set(signal) - {
                "symbol",
                "at",
                "has_position",
                "action",
                "confidence",
                "market_regime",
                "directional_bias",
                "thesis",
                "degraded",
            }:
                raise ValueError("champ de signal non autorisé")
        if v.get("kind") not in ("mechanical", "recorded"):
            raise ValueError("variante non autorisée")
        if set(v.get("risk_overrides", {})) - STRATEGIC:
            raise ValueError("seules les règles stratégiques peuvent varier")
    data = spec.get("data", {})
    if not 1 <= len(data) <= 10:
        raise ValueError("univers limité à dix paires")
    start = datetime.fromisoformat(spec["start_at"])
    end = datetime.fromisoformat(spec["end_at"])
    if (
        not start.tzinfo
        or not end.tzinfo
        or not start < end
        or end - start > timedelta(days=3660)
    ):
        raise ValueError("période invalide")
    for symbol, item in data.items():
        if "/" not in symbol:
            raise ValueError("paire invalide")
        if (
            not {"amount_step", "price_tick", "min_amount", "min_notional", "taker_fee"}
            <= item["market"].keys()
        ):
            raise ValueError("contraintes manquantes")
        for key in (
            "amount_step",
            "price_tick",
            "min_amount",
            "min_notional",
            "taker_fee",
        ):
            value = D(str(item["market"][key]))
            if not value.is_finite() or (
                value < 0 if key == "taker_fee" else value <= 0
            ):
                raise ValueError("contrainte de marché invalide")
        for timeframe, rows in item["candles"].items():
            if timeframe not in TIMEFRAME_MS:
                raise ValueError("cadence inconnue")
            if len(rows) > 100000:
                raise ValueError("historique trop volumineux")
            stamps = [r[0] for r in rows]
            if any(
                not isinstance(t, int) or isinstance(t, bool) or t < 0 for t in stamps
            ):
                raise ValueError("horodatage de bougie invalide")
            if any(
                b - a != TIMEFRAME_MS[timeframe] for a, b in zip(stamps, stamps[1:])
            ):
                raise ValueError(
                    "trou dans l’historique : réconcilier avant comparaison"
                )
            if stamps != sorted(set(stamps)):
                raise ValueError("historique désordonné ou dupliqué")
            for r in rows:
                nums = [D(str(v)) for v in r[1:6]]
                if (
                    len(r) != 6
                    or any(not v.is_finite() for v in nums)
                    or min(nums[:4]) <= 0
                    or nums[4] < 0
                    or nums[2] > min(nums[0], nums[3])
                    or nums[1] < max(nums[0], nums[3])
                ):
                    raise ValueError("bougie invalide")
    window = spec.get("validation_window")
    if window:
        first = datetime.fromisoformat(window["start_at"])
        last = datetime.fromisoformat(window["end_at"])
        if not first.tzinfo or not last.tzinfo or not start <= first < last <= end:
            raise ValueError("fenêtre de validation incohérente")
    return start, end


async def run_experiment(spec, settings=None):
    from app.runtime.orchestrator import Orchestrator
    from app.insights.codec import decode

    start, end = validate_experiment(spec)
    if settings is None:
        symbol = sorted(spec["data"])[0]
        settings = load_settings(
            overrides={
                "TRADING_MODE": "paper",
                "EXCHANGE_SANDBOX_MODE": "true",
                "EXCHANGE_ID": "kraken",
                "RISK_PROFILE": "prudent",
                "TRADING_SYMBOL": symbol,
                "QUOTE_CURRENCY": symbol.split("/")[-1],
                "PAPER_START_EQUITY": str(spec.get("equity", "1000")),
            },
            use_environment=False,
        )
    if settings.mode != "paper":
        raise ValueError("expériences exclusivement papier")
    if any(s.split("/")[-1] != settings.exchange.quote_currency for s in spec["data"]):
        raise ValueError("une seule devise de cotation par expérience")
    symbols = tuple(sorted(spec["data"]))
    timeframe = settings.exchange.daily_timeframe_decision
    grid = sorted(
        {
            c[0] + TIMEFRAME_MS[timeframe]
            for item in spec["data"].values()
            for c in item["candles"].get(timeframe, [])
            if start.timestamp() * 1000
            <= c[0] + TIMEFRAME_MS[timeframe]
            < end.timestamp() * 1000
        }
    )
    if not grid or len(grid) > 10000:
        raise ValueError("grille vide ou supérieure à 10000 cycles")
    for item in spec["data"].values():
        closes = {
            c[0] + TIMEFRAME_MS[timeframe] for c in item["candles"].get(timeframe, [])
        }
        if not set(grid) <= closes:
            raise ValueError(
                "couverture différente entre les paires sur la grille commune"
            )
    variants = []
    for variant in spec["variants"]:
        overrides = {k: decode(v) for k, v in variant.get("risk_overrides", {}).items()}
        # String enums are converted using the existing field's type; decimals never become floats.
        for k, v in list(overrides.items()):
            current = getattr(settings.risk, k)
            if isinstance(current, bool):
                if not isinstance(v, bool):
                    raise ValueError("booléen de variante invalide")
            elif isinstance(current, D):
                overrides[k] = D(str(v))
            elif hasattr(current, "value"):
                overrides[k] = type(current)(v)
        config = replace(
            settings,
            risk=replace(settings.risk, **overrides),
            exchange=replace(settings.exchange, allowed_symbols=symbols),
            paper_start_equity=D(str(spec.get("equity", settings.paper_start_equity))),
        )
        now = start
        ledger = MemoryLedger(config.paper_start_equity, now)
        exchange = HistoricalExchange(spec["data"], now)
        client = ExperimentSignal(variant)
        broker = PaperBroker(config.exchange.taker_fee, config.paper_slippage_bps)
        runtime = Orchestrator(
            config,
            ledger,
            exchange,
            client,
            broker,
            now=lambda: ledger.now,
            insights=False,
            settings_loader=lambda overrides=None: config,
        )
        curve = []
        reports = []
        for stamp in grid:
            ledger.now = exchange.now = datetime.fromtimestamp(
                stamp / 1000, timezone.utc
            )
            report = await runtime.run_once()
            reports.append(report)
            # Actual portfolio mark, including cash and the isolated reserve/growth balances.
            value = D(0)
            if ledger.position:
                mark = broker.exit(
                    ledger.position.amount,
                    D(str(exchange.fetch_ticker(ledger.position.symbol)["last"])),
                )
                value = mark.notional_quote - mark.fee_quote
            equity = ledger.cash + ledger.reserve + ledger.growth + value
            curve.append((ledger.now, equity))
        peak = config.paper_start_equity
        dd = D(0)
        for _, equity in curve:
            peak = max(peak, equity)
            if peak > 0:
                dd = max(dd, 1 - equity / peak)
        profits = sum(
            (t["net_pnl_quote"] for t in ledger.closed if t["net_pnl_quote"] > 0), D(0)
        )
        losses = -sum(
            (t["net_pnl_quote"] for t in ledger.closed if t["net_pnl_quote"] < 0), D(0)
        )
        variants.append(
            {
                "name": variant["name"],
                "kind": variant["kind"],
                "trades": len(ledger.closed),
                "closed": ledger.closed,
                "equity_start": config.paper_start_equity,
                "equity_end": curve[-1][1],
                "net_realized": sum((t["net_pnl_quote"] for t in ledger.closed), D(0)),
                "profit_factor": profits / losses if losses else None,
                "max_drawdown": dd,
                "open_position": ledger.position is not None,
                "reserve": ledger.reserve,
                "growth": ledger.growth,
                "promotions": ledger.capital.promotions if ledger.capital else 0,
                "fees": sum((o["fee_amount"] for o in ledger.orders), D(0)),
                "errors": [e for r in reports for e in r.errors],
                "curve": curve,
                "missing_signals": client.missing,
            }
        )
    baseline = variants[0]
    comparisons = []
    for variant in variants[1:]:
        deltas = [b[1] - a[1] for a, b in zip(baseline["curve"], variant["curve"])]
        # Paired weekly blocks; interval is descriptive, not a deployment decision.
        blocks = {}
        for (at, _), delta in zip(variant["curve"], deltas):
            blocks[at.isocalendar()[:2]] = delta
        values = list(blocks.values())
        interval = None
        if len(values) >= 20:
            rng = random.Random(0)
            samples = sorted(
                sum((rng.choice(values) for _ in values), D(0)) / len(values)
                for _ in range(500)
            )
            interval = [samples[12], samples[487]]
        comparisons.append(
            {
                "against": baseline["name"],
                "variant": variant["name"],
                "final_equity_difference": deltas[-1],
                "weekly_block_count": len(values),
                "mean_equity_difference_interval": interval,
                "evidence": "DESCRIPTIVE_ONLY" if interval else "INSUFFICIENT_BLOCKS",
            }
        )
    return {
        "schema_version": 1,
        "base_settings": encode(settings),
        "code_fingerprint": code_fingerprint(),
        "cost_model": "PAPER_CONFIGURED_FEE_AND_SLIPPAGE",
        "manifest_digest": digest(public(spec)),
        "mode": "OFFLINE_PAPER",
        "variants": variants,
        "comparisons": comparisons,
        "promotion_allowed": False,
        "validation_window_declared": spec.get("validation_window"),
        "data_digest": digest(public(spec["data"])),
    }
