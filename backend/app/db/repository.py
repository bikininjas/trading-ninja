"""Persistance PostgreSQL — état, signaux, décisions, positions, ordres.

Une seule connexion asyncpg (le bot est mono-processus). Les énumérations sont
castées explicitement (`$n::system_state`) : asyncpg ne devine pas les types
personnalisés, et une erreur de cast doit échouer au premier essai, pas en
production.

Aucune requête de ce module ne décide quoi que ce soit : le pare-feu décide, la
base enregistre.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal
from typing import Any

import asyncpg

from app.core.db import DatabaseConfig
from app.insights.store import InsightsStore
from app.learning.store import LearningStore
from app.engine.capital import CapitalState, start_capital, allocate_close

__all__ = [
    "AccountSnapshot",
    "PostgresRepository",
    "PositionRow",
    "StateTransitionError",
]


class StateTransitionError(RuntimeError):
    """La transition demandée ne correspond pas à l'état réellement stocké."""


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    equity: Decimal
    free_quote: Decimal
    hwm: Decimal
    daily_drawdown_pct: Decimal
    weekly_drawdown_pct: Decimal
    from_hwm_drawdown_pct: Decimal
    consecutive_losses: int
    orders_last_hour: int
    has_open_position: bool
    reserve_quote: Decimal = Decimal(0)
    growth_quote: Decimal = Decimal(0)
    promotions: int = 0
    last_promotion_at: datetime | None = None

    @property
    def trading_equity(self) -> Decimal:
        return max(Decimal(0), self.equity - self.reserve_quote - self.growth_quote)

    @property
    def peak(self) -> Decimal:
        """Plus haut connu, pour les mises à jour de HWM."""
        return max(self.hwm, self.equity)


@dataclass(frozen=True, slots=True)
class PositionRow:
    id: uuid.UUID
    symbol: str
    entry_price: Decimal
    amount: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal | None
    opened_at: datetime
    risk_amount_quote: Decimal
    monitor_cursor_at: datetime | None = None
    policy_id: str | None = None
    initial_amount: Decimal | None = None


def _drawdown(peak: Decimal, equity: Decimal) -> Decimal:
    if peak <= 0:
        return Decimal(0)
    return max(Decimal(0), Decimal(1) - equity / peak)


class PostgresRepository(InsightsStore, LearningStore):
    def __init__(self, connection: asyncpg.Connection, *, lock_connection_factory=None,
                 reconnect_factory=None):
        self._conn = connection
        self._lock_connection_factory = lock_connection_factory
        self._reconnect_factory = reconnect_factory

    @classmethod
    async def connect(cls, config: DatabaseConfig) -> "PostgresRepository":
        connection = await asyncpg.connect(**config.asyncpg_kwargs())
        factory = lambda: asyncpg.connect(**config.asyncpg_kwargs())
        return cls(connection, lock_connection_factory=factory, reconnect_factory=factory)

    async def ensure_connected(self) -> bool:
        """Ping la connexion du ledger et la recrée après une coupure idle/pooler."""
        if self._reconnect_factory is None:
            return False
        try:
            if not self._conn.is_closed():
                await asyncio.wait_for(self._conn.fetchval("SELECT 1"), timeout=5)
                return False
        except (asyncpg.PostgresConnectionError, asyncpg.InterfaceError, OSError, TimeoutError):
            pass
        try:
            await self._conn.close(timeout=1)
        except Exception:
            pass
        self._conn = await asyncio.wait_for(self._reconnect_factory(), timeout=10)
        return True

    async def prepare_request(self, mode, symbol, request):
        from app.core.coordination import assert_financial_guard
        await assert_financial_guard(self, mode)
        if self._conn.is_in_transaction():
            raise StateTransitionError("requête exchange refusée : le reçu doit être validé avant l'envoi")
        client_id = "tn" + uuid.uuid4().hex[:16]
        async with self._conn.transaction():
            if request.get('side')=='buy' and request.get('type')=='market':
                permission=await self._conn.fetchrow("SELECT c.enabled,c.revision,b.state::text FROM entry_controls c JOIN bot_state b ON b.mode=c.mode WHERE c.mode=$1::execution_mode FOR UPDATE OF c,b",mode)
                if not permission or not permission['enabled'] or permission['state']!='EXECUTING':
                    raise StateTransitionError('achat non autorisé : entrées suspendues ou état modifié')
                request={**request,'authorized_revision':permission['revision']}
            await self._conn.execute(
                "INSERT INTO execution_requests(client_id,mode,symbol,request,state) VALUES($1,$2::execution_mode,$3,$4::jsonb,'PREPARED')",
                client_id,mode,symbol,json.dumps(request,default=str))
        return client_id


    async def update_request(self, client_id, state, *, response=None, error=None):
        await self._conn.execute(
            "UPDATE execution_requests SET state=$2, response=COALESCE($3::jsonb,response), "
            "exchange_order_id=COALESCE($4,exchange_order_id), error=$5, "
            "updated_at=CLOCK_TIMESTAMP() WHERE client_id=$1",
            client_id, state, json.dumps(response, default=str) if response else None,
            str(response.get('id') or '') or None if response else None, error,
        )


    async def pending_requests(self, mode):
        rows = await self._conn.fetch(
            "SELECT * FROM execution_requests WHERE mode=$1::execution_mode "
            "AND state IN ('PREPARED','ACKNOWLEDGED','FILLED','UNKNOWN') ORDER BY created_at", mode,
        )
        out = []
        for row in rows:
            item = dict(row)
            for field in ('request', 'response'):
                if isinstance(item[field], str):
                    item[field] = json.loads(item[field])
            out.append(item)
        return out


    async def book_request(self, mode, exchange_order_id):
        if exchange_order_id:
            await self._conn.execute(
                "UPDATE execution_requests SET state='BOOKED', updated_at=CLOCK_TIMESTAMP() "
                "WHERE mode=$1::execution_mode AND exchange_order_id=$2 AND request->>'type'='market'", mode, exchange_order_id,
            )


    async def fill_recorded(self, mode, exchange_order_id):
        return bool(exchange_order_id and await self._conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM orders WHERE mode=$1::execution_mode "
            "AND exchange_order_id=$2 AND status IN ('FILLED','PARTIALLY_FILLED'))", mode, exchange_order_id,
        ))


    async def account_exit(self, mode, symbol, fill, *, purpose, reason, quote, taker_fee, emergency=False):
        """Une transaction par fill confirmé : quantité résiduelle, PnL, ordre, reçu.

        Le même ID exchange n'est jamais imputé deux fois. Les frais d'entrée
        sont alloués au prorata de la quantité initiale, pas de la quantité restante.
        """
        async with self._conn.transaction():
            existing = await self._conn.fetchrow(
                "SELECT * FROM orders WHERE mode=$1::execution_mode AND exchange_order_id=$2 "
                "AND status IN ('FILLED','PARTIALLY_FILLED') FOR UPDATE", mode, fill.order_id,
            ) if fill.order_id else None
            cumulative = fill
            if existing:
                if existing['filled_amount'] >= fill.amount:
                    await self.book_request(mode, fill.order_id)
                    return None
                from dataclasses import replace
                delta = fill.amount - existing['filled_amount']
                cost = fill.notional_quote - existing['filled_amount'] * existing['average_fill_price']
                fee = fill.fee_quote - existing['fee_amount']
                if cost <= 0 or fee < 0:
                    raise StateTransitionError("fill cumulatif incohérent")
                fill = replace(fill, amount=delta, notional_quote=cost, price=cost/delta, fee_quote=fee)
            row = await self._conn.fetchrow(
                "SELECT * FROM positions WHERE mode=$1::execution_mode AND symbol=$2 AND status='OPEN' FOR UPDATE",
                mode, symbol,
            )
            net = gross = Decimal(0)
            fees = fill.fee_quote
            remaining = Decimal(0)
            if row:
                available = row['initial_amount'] - row['closed_amount']
                if fill.amount > available * Decimal('1.001'):
                    raise StateTransitionError("fill supérieur à la quantité suivie : rapprochement requis")
                remaining = max(Decimal(0), available - fill.amount)
                entry_fees = await self.entry_fee(row['id'])
                if entry_fees == 0:
                    entry_fees = row['entry_price'] * row['initial_amount'] * taker_fee
                fees = entry_fees * fill.amount / row['initial_amount'] + fill.fee_quote
                gross = (fill.price - row['entry_price']) * fill.amount
                net = gross - fees
                terminal = remaining == 0
                status = ('EMERGENCY_LIQUIDATED' if emergency else 'CLOSED') if terminal else 'OPEN'
                await self._conn.execute(
                    "UPDATE positions SET closed_amount=closed_amount+$2, gross_pnl_quote=gross_pnl_quote+$3, "
                    "total_fees_quote=total_fees_quote+$4, net_pnl_quote=net_pnl_quote+$5, "
                    "status=$6::position_status, exit_price=$7, exit_reason=$8, protective_order_ok=FALSE, "
                    "closed_at=CASE WHEN $6::text='OPEN' THEN NULL ELSE CLOCK_TIMESTAMP() END WHERE id=$1",
                    row['id'], fill.amount, gross, fees, net, status, fill.price, reason,
                )
                if terminal:
                    await self._conn.execute(
                        "UPDATE bot_state SET consecutive_losses=CASE WHEN $2::numeric < 0 THEN consecutive_losses+1 ELSE 0 END, "
                        "last_trade_at=CLOCK_TIMESTAMP() WHERE mode=$1::execution_mode", mode, row['net_pnl_quote'] + net,
                    )
                await self._conn.execute(
                    "UPDATE orders SET status='CANCELED', updated_at=CLOCK_TIMESTAMP() WHERE position_id=$1 "
                    "AND status IN ('SUBMITTED','PENDING','PARTIALLY_FILLED')", row['id'],
                )
            if existing:
                await self._conn.execute(
                    "UPDATE orders SET filled_amount=$2, average_fill_price=$3, fee_amount=$4, status='FILLED', "
                    "updated_at=CLOCK_TIMESTAMP() WHERE id=$1", existing['id'], cumulative.amount, cumulative.price, cumulative.fee_quote,
                )
            else:
                await self.insert_order(
                    mode, client_order_id='book-' + uuid.uuid4().hex, symbol=symbol, side='SELL',
                    order_type='MARKET', purpose=purpose, status='FILLED', requested_amount=fill.amount,
                    filled_amount=fill.amount, requested_price=None, stop_price=None, average_fill_price=fill.price,
                    fee_amount=fill.fee_quote, fee_currency=quote, slippage_quote=fill.slippage_quote,
                    position_id=row['id'] if row else None, exchange_order_id=fill.order_id,
                )
            await self.book_request(mode, fill.order_id)
            return dict(net=net, fees=fees, remaining=remaining, tracked=row is not None,
                        position_id=row['id'] if row else None,
                        total_net=(row['net_pnl_quote'] + net) if row else net)


    async def close(self) -> None:
        await self._conn.close()

    def transaction(self):
        """Transaction explicite, utile aux vérifications qui doivent être annulées."""
        return self._conn.transaction()

    # -- état ------------------------------------------------------------

    async def ensure_mode(self, mode: str) -> None:
        await self._conn.execute(
            "INSERT INTO bot_state (mode) VALUES ($1::execution_mode) "
            "ON CONFLICT (mode) DO NOTHING",
            mode,
        )

    async def state(self, mode: str) -> tuple[str, datetime | None]:
        row = await self._conn.fetchrow(
            "SELECT state::text AS state, cooldown_until FROM bot_state "
            "WHERE mode = $1::execution_mode",
            mode,
        )
        if row is None:
            raise StateTransitionError(f"aucune ligne bot_state pour le mode {mode}")
        return row["state"], row["cooldown_until"]

    async def runtime_heartbeat(self, mode: str) -> datetime | None:
        return await self._conn.fetchval(
            "SELECT last_heartbeat_at FROM bot_state WHERE mode=$1::execution_mode", mode,
        )

    async def transition(self, mode: str, expected: str, target: str, reason: str,
                         code: str | None = None, context: dict | None = None) -> None:
        async with self._conn.transaction():
            result = await self._conn.fetchval(
                "UPDATE bot_state SET state = $3::system_state, since = CLOCK_TIMESTAMP(), "
                "updated_at = CLOCK_TIMESTAMP() "
                "WHERE mode = $1::execution_mode AND state = $2::system_state "
                "RETURNING state::text",
                mode, expected, target,
            )
            if result is None:
                current = await self._conn.fetchval(
                    "SELECT state::text FROM bot_state WHERE mode = $1::execution_mode", mode
                )
                raise StateTransitionError(
                    f"transition {expected} → {target} refusée : état courant {current}"
                )
            await self._conn.execute(
                "INSERT INTO system_state_logs "
                "(mode, previous_state, current_state, trigger_reason, trigger_code, context_data) "
                "VALUES ($1::execution_mode, $2::system_state, $3::system_state, $4, "
                "$5::risk_rejection_code, $6::jsonb)",
                mode, expected, target, reason, code,
                json.dumps(context) if context else None,
            )

    async def set_cooldown(self, mode: str, until: datetime) -> None:
        await self._conn.execute(
            "UPDATE bot_state SET cooldown_until = $2, updated_at = CLOCK_TIMESTAMP() "
            "WHERE mode = $1::execution_mode",
            mode, until,
        )

    async def note_error(self, mode: str, message: str) -> None:
        await self._conn.execute(
            "UPDATE bot_state SET last_error = $2, updated_at = CLOCK_TIMESTAMP() "
            "WHERE mode = $1::execution_mode",
            mode, message[:2000],
        )

    # -- compte ----------------------------------------------------------

    async def account(self, mode: str, starting_equity: Decimal) -> AccountSnapshot:
        # Le dashboard ne doit pas lire un ancien snapshot avec une réserve
        # déjà mise à jour par une clôture concurrente.
        if self._conn.is_in_transaction():
            return await self._account(mode, starting_equity)
        async with self._conn.transaction(isolation="repeatable_read", readonly=True):
            return await self._account(mode, starting_equity)

    async def _account(self, mode: str, starting_equity: Decimal) -> AccountSnapshot:
        last = await self._conn.fetchrow(
            "SELECT equity_quote, free_quote FROM equity_snapshots "
            "WHERE mode = $1::execution_mode ORDER BY taken_at DESC LIMIT 1",
            mode,
        )
        equity = last["equity_quote"] if last else starting_equity
        free = last["free_quote"] if last else starting_equity

        counters = await self._conn.fetchrow(
            "SELECT equity_hwm_quote, consecutive_losses FROM bot_state "
            "WHERE mode = $1::execution_mode",
            mode,
        )
        hwm = counters["equity_hwm_quote"] if counters else Decimal(0)

        daily_peak = await self._conn.fetchval(
            "SELECT COALESCE(MAX(equity_quote), 0) FROM equity_snapshots "
            "WHERE mode = $1::execution_mode AND taken_at >= date_trunc('day', now())",
            mode,
        )
        weekly_peak = await self._conn.fetchval(
            "SELECT COALESCE(MAX(equity_quote), 0) FROM equity_snapshots "
            "WHERE mode = $1::execution_mode AND taken_at >= now() - interval '7 days'",
            mode,
        )
        orders_last_hour = await self._conn.fetchval(
            "SELECT COUNT(*) FROM orders WHERE mode = $1::execution_mode "
            "AND created_at > now() - interval '1 hour' AND status <> 'REJECTED'",
            mode,
        )
        has_position = await self._conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM positions "
            "WHERE mode = $1::execution_mode AND status = 'OPEN')",
            mode,
        )
        reserve = await self._conn.fetchval(
            "SELECT COALESCE(SUM(swept_amount), 0) FROM profit_sweeps "
            "WHERE mode = $1::execution_mode "
            "AND transfer_status IN ('SUCCESS', 'SIMULATED')",
            mode,
        )
        capital = await self.capital_state(mode)
        growth = capital.growth_quote if capital else Decimal(0)
        if capital:
            reserve = capital.reserve_quote
            active_peak = capital.active_quote + max(Decimal(0), capital.surplus_hwm - capital.realized_pnl)
            hwm_drawdown = _drawdown(active_peak, equity - reserve - growth)
        else:
            hwm_drawdown = Decimal(0)
        return AccountSnapshot(
            equity=equity,
            free_quote=free,
            hwm=hwm,
            daily_drawdown_pct=_drawdown(max(daily_peak or Decimal(0), equity), equity),
            weekly_drawdown_pct=_drawdown(max(weekly_peak or Decimal(0), equity), equity),
            from_hwm_drawdown_pct=max(hwm_drawdown, _drawdown(max(hwm, equity), equity)),
            consecutive_losses=int(counters["consecutive_losses"]) if counters else 0,
            orders_last_hour=int(orders_last_hour or 0),
            has_open_position=bool(has_position),
            reserve_quote=reserve or Decimal(0),
            growth_quote=growth,
            promotions=capital.promotions if capital else 0,
            last_promotion_at=capital.last_promotion_at if capital else None,
        )

    async def snapshot_equity(self, mode: str, *, equity: Decimal, free_quote: Decimal,
                              position_value_quote: Decimal, symbol: str | None = None,
                              source: str = "ORCHESTRATOR") -> None:
        async with self._conn.transaction():
            await self._conn.execute(
                "INSERT INTO equity_snapshots "
                "(mode, symbol, equity_quote, free_quote, position_value_quote, source) "
                "VALUES ($1::execution_mode, $2, $3, $4, $5, $6)",
                mode, symbol, equity, free_quote, position_value_quote, source,
            )
            await self._conn.execute(
                "UPDATE bot_state SET equity_hwm_quote = GREATEST(equity_hwm_quote, $2), "
                "updated_at = CLOCK_TIMESTAMP() "
                "WHERE mode = $1::execution_mode",
                mode, equity,
            )

    async def set_paper_balance(self, balance: Decimal, current: AccountSnapshot) -> Decimal:
        """Ajuste le solde simulé en conservant chaque ancien instantané."""
        async with self._conn.transaction():
            position = await self.open_position("PAPER")
            if position is not None:
                raise ValueError("ferme la position PAPER avant d’ajuster le solde")
            delta = balance - current.equity
            capital = await self.capital_state("PAPER")
            if capital is not None:
                updated_active = capital.active_quote + delta
                updated_initial = capital.initial_quote + delta
                if updated_active < 0 or updated_initial <= 0:
                    raise ValueError("le nouvel ajustement dépasserait le capital PAPER disponible")
                await self._conn.execute(
                    "UPDATE capital_accounts SET active_quote=$2, initial_quote=$3 "
                    "WHERE mode='PAPER'", updated_active, updated_initial,
                )
            await self._conn.execute(
                "INSERT INTO equity_snapshots (mode,equity_quote,free_quote,source) "
                "VALUES ('PAPER',$1,$1,'ADMIN_ADJUSTMENT')", balance,
            )
            await self._conn.execute(
                "UPDATE bot_state SET equity_hwm_quote=GREATEST(equity_hwm_quote,$1), "
                "updated_at=CLOCK_TIMESTAMP() WHERE mode='PAPER'", balance,
            )
        return balance

    # -- signaux et décisions --------------------------------------------

    async def insert_signal(self, mode: str, symbol: str, model_name: str, *,
                            action: str, confidence: Decimal, market_regime: str,
                            thesis: str, prompt_payload: dict, raw_response: dict,
                            inference_time_ms: int, prompt_sha256: str | None,
                            temperature: float) -> tuple[uuid.UUID, datetime]:
        row = await self._conn.fetchrow(
            "INSERT INTO llm_signals (mode, symbol, model_name, action, confidence, "
            "market_regime, thesis, prompt_payload, raw_response, inference_time_ms, "
            "prompt_sha256, temperature) "
            "VALUES ($1::execution_mode, $2, $3, $4::signal_action, $5, "
            "$6::market_regime, $7, $8::jsonb, $9::jsonb, $10, $11, $12) "
            "RETURNING id, created_at",
            mode, symbol, model_name, action, confidence, market_regime, thesis,
            json.dumps(prompt_payload, ensure_ascii=False, default=str),
            json.dumps(raw_response, ensure_ascii=False, default=str),
            inference_time_ms, prompt_sha256, Decimal(str(temperature)),
        )
        return row["id"], row["created_at"]

    async def last_signal_at(self, mode: str, symbol: str) -> datetime | None:
        """Heure du dernier signal pour une paire — sert à ne pas réévaluer une bougie.

        La comparaison se fait avec la clôture de la bougie de décision : un signal
        plus récent que cette clôture prouve que la bougie a déjà été traitée, y
        compris par un processus précédent.
        """
        return await self._conn.fetchval(
            "SELECT MAX(created_at) FROM llm_signals "
            "WHERE mode = $1::execution_mode AND symbol = $2",
            mode, symbol,
        )

    async def insert_evaluation(self, mode: str, symbol: str, *, signal_id: uuid.UUID,
                                signal_created_at: datetime, decision: Any, snapshot: Any,
                                account: AccountSnapshot, evaluation_time_ms: int,
                                decision_timeframe: str) -> tuple[uuid.UUID, datetime]:
        row = await self._conn.fetchrow(
            "INSERT INTO risk_evaluations (mode, symbol, signal_id, signal_created_at, "
            "approved, rejection_code, rejection_details, market_price, ema_200_h4, "
            "atr_14_h4, atr_pct_of_price, prev_candle_return_atr, decision_timeframe, "
            "account_equity_quote, free_quote_balance, daily_drawdown_pct, "
            "weekly_drawdown_pct, drawdown_from_hwm_pct, consecutive_losses, "
            "computed_amount, computed_stop_loss, computed_take_profit, "
            "intended_risk_quote, effective_risk_quote, effective_risk_pct, "
            "checks_passed, evaluation_time_ms) "
            "VALUES ($1::execution_mode, $2, $3, $4, $5, $6::risk_rejection_code, $7, "
            "$8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, "
            "$23, $24, $25, $26, $27) "
            "RETURNING id, created_at",
            mode, symbol, signal_id, signal_created_at,
            decision.approved, decision.rejection_code.value, decision.rejection_details,
            snapshot.price, snapshot.ema_200_h4, snapshot.atr_14_h4,
            snapshot.atr_14_h4 / snapshot.price, snapshot.last_closed_h1_return_atr,
            decision_timeframe,
            account.equity, account.free_quote,
            account.daily_drawdown_pct, account.weekly_drawdown_pct,
            account.from_hwm_drawdown_pct, account.consecutive_losses,
            decision.amount, decision.stop_loss_price, decision.take_profit_price,
            decision.intended_risk_quote, decision.effective_risk_quote,
            decision.effective_risk_pct, list(decision.checks_passed),
            evaluation_time_ms,
        )
        return row["id"], row["created_at"]

    # -- shadow trades : les achats que le bot n'a pas pris -----------------

    async def insert_shadow_trade(
        self, mode: str, symbol: str, *,
        kind: str, action: str, signal_id: uuid.UUID,
        evaluation_id: uuid.UUID | None,
        rejection_code: str, rejection_details: str | None,
        decision_price: Decimal,
        amount: Decimal | None,
        notional_quote: Decimal | None,
        stop_loss_price: Decimal | None,
        take_profit_price: Decimal | None,
        risk_amount_quote: Decimal | None,
        plan_status: str,
        simulation_context: dict | None = None,
    ) -> uuid.UUID:
        row = await self._conn.fetchrow(
            "INSERT INTO shadow_trades (mode, symbol, kind, action, signal_id, "
            "evaluation_id, rejection_code, rejection_details, decision_price, amount, "
            "notional_quote, stop_loss_price, take_profit_price, risk_amount_quote, "
            "plan_status,simulation_context) VALUES ($1::execution_mode, $2, $3, $4::signal_action, $5, "
            "$6, $7::risk_rejection_code, $8, $9, $10, $11, $12, $13, $14, $15,$16::jsonb) "
            "RETURNING id",
            mode, symbol, kind, action, signal_id, evaluation_id,
            rejection_code, rejection_details, decision_price, amount,
            notional_quote, stop_loss_price, take_profit_price, risk_amount_quote,
            plan_status, json.dumps(simulation_context or {}),
        )
        return row["id"]

    async def open_shadow_trades(self, mode: str, limit: int = 200) -> list[dict]:
        rows = await self._conn.fetch(
            "SELECT id, created_at, symbol, kind, plan_status, decision_price, amount, "
            "stop_loss_price, take_profit_price FROM shadow_trades "
            "WHERE mode = $1::execution_mode AND plan_status = 'OPEN' "
            "AND simulation_context->>'version' IS DISTINCT FROM 'forward-v1' "
            "ORDER BY created_at LIMIT $2",
            mode, limit,
        )
        return [dict(row) for row in rows]

    async def close_shadow_trade(
        self, shadow_id: uuid.UUID, *,
        status: str, exit_price: Decimal, pnl_quote: Decimal,
        outcome_reason: str, evaluated_at: datetime,
    ) -> None:
        await self._conn.execute(
            "UPDATE shadow_trades SET plan_status = $2, exit_price = $3, "
            "pnl_quote = $4, outcome_reason = $5, evaluated_at = $6 "
            "WHERE id = $1",
            shadow_id, status, exit_price, pnl_quote, outcome_reason, evaluated_at,
        )

    async def shadow_summary(self, mode: str) -> dict:
        rows = await self._conn.fetch(
            "SELECT kind, plan_status, COUNT(*) AS count "
            "FROM shadow_trades WHERE mode = $1::execution_mode "
            "GROUP BY kind, plan_status",
            mode,
        )
        total = planned = no_plan = open_count = closed = 0
        by_kind: dict[str, int] = {}
        for row in rows:
            count = int(row["count"])
            total += count
            by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + count
            if row["plan_status"] == "NO_PLAN":
                no_plan += count
            else:
                planned += count
            if row["plan_status"] == "OPEN":
                open_count += count
            elif row["plan_status"] in ("STOP_LOSS", "TAKE_PROFIT", "END_OF_DATA"):
                closed += count

        outcome = await self._conn.fetchrow(
            "SELECT COUNT(*) FILTER (WHERE pnl_quote > 0) AS wins, "
            "COUNT(*) FILTER (WHERE pnl_quote <= 0) AS losses, "
            "COALESCE(SUM(pnl_quote), 0) AS net_pnl "
            "FROM shadow_trades WHERE mode = $1::execution_mode "
            "AND plan_status IN ('STOP_LOSS', 'TAKE_PROFIT', 'END_OF_DATA')",
            mode,
        )
        rejections = await self._conn.fetch(
            "SELECT rejection_code, COUNT(*) AS count "
            "FROM shadow_trades WHERE mode = $1::execution_mode "
            "AND kind = 'GATE_REJECTED_BUY' "
            "GROUP BY rejection_code ORDER BY count DESC",
            mode,
        )
        return {
            "total": total,
            "planned": planned,
            "no_plan": no_plan,
            "open": open_count,
            "closed": closed,
            "wins": int(outcome["wins"] or 0),
            "losses": int(outcome["losses"] or 0),
            "net_pnl": outcome["net_pnl"] or Decimal(0),
            "by_kind": by_kind,
            "by_rejection": [
                {"code": row["rejection_code"], "count": int(row["count"])}
                for row in rejections
            ],
        }

    # -- mémoire de leçons ------------------------------------------------

    async def active_lessons(self, mode: str) -> list[str]:
        row = await self._conn.fetchrow(
            "SELECT lessons FROM lessons WHERE mode = $1::execution_mode AND active "
            "AND (valid_until IS NULL OR valid_until > CLOCK_TIMESTAMP()) "
            "ORDER BY version DESC LIMIT 1",
            mode,
        )
        return list(row["lessons"]) if row else []

    async def insert_lessons(
        self, mode: str, *,
        lessons: list[str],
        source_trade_ids: list[uuid.UUID],
        source_count: int,
        model_name: str,
        prompt_version: str,
        valid_until: datetime | None,
        review_sha256: str,
        expected_version: int | None = None,
    ) -> tuple[uuid.UUID, int]:
        async with self._conn.transaction():
            await self._conn.execute('SELECT pg_advisory_xact_lock(1414416963,$1)',
                                     {'PAPER':1,'TESTNET':2,'LIVE':3}[mode])
            current = int(await self._conn.fetchval(
                'SELECT COALESCE(MAX(version),0) FROM lessons WHERE mode=$1::execution_mode',mode))
            if expected_version is not None and current != expected_version:
                raise StateTransitionError('mémoire modifiée pendant la relecture')
            await self._conn.execute(
                "UPDATE lessons SET active = FALSE "
                "WHERE mode = $1::execution_mode AND active",
                mode,
            )
            version = await self._conn.fetchval(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM lessons "
                "WHERE mode = $1::execution_mode",
                mode,
            )
            row = await self._conn.fetchrow(
                "INSERT INTO lessons (mode, version, lessons, source_trade_ids, "
                "source_count, model_name, prompt_version, valid_until, review_sha256) "
                "VALUES ($1::execution_mode, $2, $3::text[], $4::uuid[], $5, $6, $7, "
                "$8, $9) RETURNING id",
                mode, version, lessons, source_trade_ids, source_count,
                model_name, prompt_version, valid_until, review_sha256,
            )
        return row["id"], int(version)

    async def recent_lessons(self, mode: str, limit: int = 10) -> list[dict]:
        rows = await self._conn.fetch(
            "SELECT id, created_at, version, lessons, source_count, model_name, "
            "prompt_version, valid_until, active FROM lessons "
            "WHERE mode = $1::execution_mode ORDER BY version DESC LIMIT $2",
            mode, limit,
        )
        return [dict(row) for row in rows]

    async def deactivate_lessons(self, mode: str) -> None:
        await self._conn.execute(
            "UPDATE lessons SET active = FALSE "
            "WHERE mode = $1::execution_mode AND active",
            mode,
        )

    async def closed_positions_for_lessons(self, mode: str, limit: int = 30) -> list[dict]:
        rows = await self._conn.fetch(
            "SELECT p.id, p.symbol, p.closed_at, p.net_pnl_quote, p.exit_reason, "
            "p.entry_price, p.exit_price, p.risk_amount_quote, "
            "s.confidence, s.market_regime, s.thesis "
            "FROM positions p LEFT JOIN llm_signals s ON s.id = p.entry_signal_id "
            "WHERE p.mode = $1::execution_mode AND p.status = 'CLOSED' "
            "ORDER BY p.closed_at DESC NULLS LAST LIMIT $2",
            mode, limit,
        )
        return [dict(row) for row in rows]

    async def shadow_outcomes_for_lessons(self, mode: str, limit: int = 30) -> list[dict]:
        rows = await self._conn.fetch(
            "SELECT id, symbol, kind, action, rejection_code, plan_status, "
            "decision_price, pnl_quote, outcome_reason, created_at "
            "FROM shadow_trades WHERE mode = $1::execution_mode "
            "AND plan_status IN ('STOP_LOSS', 'TAKE_PROFIT', 'END_OF_DATA') "
            "AND (simulation_context->>'version' IS DISTINCT FROM 'forward-v1' OR simulation_context->>'eligible'='true') "
            "ORDER BY created_at DESC LIMIT $2",
            mode, limit,
        )
        return [dict(row) for row in rows]

    # -- positions et ordres ---------------------------------------------

    async def open_positions(self, mode: str) -> list[PositionRow]:
        rows = await self._conn.fetch(
            "SELECT id, symbol, entry_price, initial_amount-closed_amount AS initial_amount, stop_loss_price, "
            "take_profit_price, opened_at, risk_amount_quote, monitor_cursor_at, policy_id, initial_amount AS original_amount FROM positions "
            "WHERE mode = $1::execution_mode AND status = 'OPEN' "
            "ORDER BY opened_at DESC",
            mode,
        )
        return [
            PositionRow(
                id=row["id"], symbol=row["symbol"], entry_price=row["entry_price"],
                amount=row["initial_amount"], stop_loss_price=row["stop_loss_price"],
                take_profit_price=row["take_profit_price"], opened_at=row["opened_at"],
                risk_amount_quote=row["risk_amount_quote"],
                monitor_cursor_at=row["monitor_cursor_at"], policy_id=row["policy_id"], initial_amount=row["original_amount"],
            )
            for row in rows
        ]

    async def open_position(self, mode: str) -> PositionRow | None:
        positions = await self.open_positions(mode)
        return positions[0] if positions else None

    async def stop_order_id(self, position_id) -> str | None:
        return await self._conn.fetchval(
            "SELECT COALESCE(exchange_order_id,client_order_id,id::text) FROM orders WHERE position_id = $1 "
            "AND purpose = 'STOP_LOSS' ORDER BY created_at DESC LIMIT 1",
            position_id,
        )

    async def storage_state(self) -> dict:
        db_bytes = int(await self._conn.fetchval("SELECT pg_database_size(current_database())"))
        limit = 500 * 1024 * 1024
        free = max(0, limit - db_bytes)
        return {
            "db_size_bytes": db_bytes,
            "db_limit_bytes": limit,
            "free_space_bytes": free,
            "quota_alert_triggered": free < 100 * 1024 * 1024,
        }

    async def llm_signal_prompt(self, mode: str, signal_id: str):
        import uuid as _uuid
        try:
            parsed = _uuid.UUID(str(signal_id))
        except (ValueError, TypeError):
            return None
        row = await self._conn.fetchrow(
            "SELECT prompt_payload FROM llm_signals "
            "WHERE mode = $1::execution_mode AND id = $2",
            mode, parsed,
        )
        if row is None:
            return None
        payload = row["prompt_payload"]
        if isinstance(payload, str):
            import json as _json
            try:
                payload = _json.loads(payload)
            except Exception:  # noqa: BLE001
                payload = {"raw": payload}
        return payload

    async def insert_order(self, mode: str, *, client_order_id: str, symbol: str,
                           side: str, order_type: str, purpose: str, status: str,
                           requested_amount: Decimal, filled_amount: Decimal,
                           requested_price: Decimal | None, stop_price: Decimal | None,
                           average_fill_price: Decimal, fee_amount: Decimal,
                           fee_currency: str, slippage_quote: Decimal | None,
                           position_id: uuid.UUID | None = None,
                           risk_evaluation_id: uuid.UUID | None = None,
                           error_message: str | None = None,
                           exchange_order_id: str | None = None) -> uuid.UUID:
        return await self._conn.fetchval(
            "INSERT INTO orders (position_id, risk_evaluation_id, client_order_id, symbol, "
            "mode, side, order_type, purpose, status, requested_amount, filled_amount, "
            "requested_price, stop_price, average_fill_price, fee_amount, fee_currency, "
            "slippage_quote, error_message, exchange_order_id) "
            "VALUES ($1, $2, $3, $4, $5::execution_mode, $6::order_side, $7::order_type, "
            "$8::order_purpose, $9::order_status, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19) "
            "RETURNING id",
            position_id, risk_evaluation_id, client_order_id, symbol, mode, side,
            order_type, purpose, status, requested_amount, filled_amount,
            requested_price, stop_price, average_fill_price, fee_amount, fee_currency,
            slippage_quote, error_message, exchange_order_id,
        )


    async def create_position(self, mode: str, *, symbol: str, entry_price: Decimal,
                              amount: Decimal, notional_quote: Decimal,
                              stop_loss_price: Decimal, take_profit_price: Decimal | None,
                              risk_amount_quote: Decimal, r_multiple_at_tp: Decimal | None,
                              signal_id: uuid.UUID, evaluation_id: uuid.UUID,
                              protective_order_id: str | None, opened_at: datetime | None = None) -> uuid.UUID:
        return await self._conn.fetchval(
            "INSERT INTO positions (symbol, mode, entry_signal_id, entry_risk_evaluation_id, "
            "entry_price, initial_amount, initial_notional_quote, stop_loss_price, "
            "take_profit_price, risk_amount_quote, r_multiple_at_tp, protective_order_id, "
            "protective_order_ok, protective_checked_at, monitor_cursor_at, opened_at) "
            "VALUES ($1, $2::execution_mode, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, "
            "TRUE, CLOCK_TIMESTAMP(), COALESCE($13,CLOCK_TIMESTAMP()), COALESCE($13,CLOCK_TIMESTAMP())) RETURNING id",
            symbol, mode, signal_id, evaluation_id, entry_price, amount, notional_quote,
            stop_loss_price, take_profit_price, risk_amount_quote, r_multiple_at_tp,
            protective_order_id, opened_at,
        )

    async def update_protective_stop(self,position_id,price,exchange_id):
        async with self._conn.transaction():
            changed=await self._conn.fetchval("UPDATE positions SET protective_order_id=$2,stop_loss_price=$3,protective_order_ok=TRUE,protective_checked_at=CLOCK_TIMESTAMP() WHERE id=$1 AND status='OPEN' AND stop_loss_price<=$3 RETURNING id",position_id,exchange_id,price)
            if not changed:raise StateTransitionError('déplacement de stop incompatible avec la position')
            await self._conn.execute("UPDATE orders SET exchange_order_id=$2,stop_price=$3,updated_at=CLOCK_TIMESTAMP() WHERE position_id=$1 AND purpose='STOP_LOSS' AND status IN ('SUBMITTED','PENDING')",position_id,exchange_id,price)

    async def update_position_stop(self, position_id: uuid.UUID,
                                   stop_loss_price: Decimal) -> None:
        """Déplace le stop local (seuil de rentabilité). Jamais au-dessus du prix."""
        await self._conn.execute(
            "UPDATE positions SET stop_loss_price = $2 "
            "WHERE id = $1 AND status = 'OPEN'",
            position_id, stop_loss_price,
        )

    async def close_position(self, position_id: uuid.UUID, *, exit_price: Decimal,
                             closed_amount: Decimal, gross_pnl_quote: Decimal,
                             total_fees_quote: Decimal, net_pnl_quote: Decimal,
                             exit_reason: str, status: str = "CLOSED") -> None:
        await self._conn.execute(
            "UPDATE positions SET status = $8::position_status, "
            "closed_at = CLOCK_TIMESTAMP(), exit_price = $2, closed_amount = $3, "
            "gross_pnl_quote = $4, total_fees_quote = $5, net_pnl_quote = $6, "
            "exit_reason = $7 WHERE id = $1",
            position_id, exit_price, closed_amount, gross_pnl_quote,
            total_fees_quote, net_pnl_quote, exit_reason, status,
        )

    async def entry_fee(self, position_id: uuid.UUID) -> Decimal:
        """Frais taker réellement enregistrés sur l'ordre d'entrée d'une position.

        En live, le coût vient de `order['fee']` ; en papier, du fill simulé.
        Cette lecture évite de reconstruire un frais avec le prix courant, qui
        serait faux après un slippage ou un changement de tarif.
        """
        value = await self._conn.fetchval(
            "SELECT COALESCE(SUM(fee_amount), 0) FROM orders "
            "WHERE position_id = $1 AND side = 'BUY' AND purpose = 'ENTRY'",
            position_id,
        )
        return value or Decimal(0)

    async def insert_sweep(self, mode: str, *, position_id: uuid.UUID,
                           quote_currency: str, realized_net_pnl: Decimal,
                           sweep_ratio: Decimal, computed_sweep_quote: Decimal,
                           swept_amount: Decimal, retained_trading_capital: Decimal,
                           status: str, next_retry_at: datetime | None = None,
                           error_message: str | None = None) -> uuid.UUID:
        return await self._conn.fetchval(
            "INSERT INTO profit_sweeps (position_id, mode, quote_currency, "
            "realized_net_pnl, sweep_ratio, computed_sweep_quote, swept_amount, "
            "retained_trading_capital, from_account, to_account, transfer_status, "
            "next_retry_at, error_message) "
            "VALUES ($1, $2::execution_mode, $3, $4, $5, $6, $7, $8, 'spot', "
            "'reserve', $9::sweep_status, $10, $11) RETURNING id",
            position_id, mode, quote_currency, realized_net_pnl, sweep_ratio,
            computed_sweep_quote, swept_amount, retained_trading_capital, status,
            next_retry_at, error_message,
        )

    async def deferred_sweep_quote(self, mode: str) -> Decimal:
        """Somme des écréma­ges reportés depuis le dernier écrémage exécuté.

        Le `created_at > MAX(...)` évite d'ajouter une colonne de consommation : dès
        qu'un écrémage est exécuté, tous les reports antérieurs sont soldés par
        construction.
        """
        value = await self._conn.fetchval(
            "SELECT COALESCE(SUM(computed_sweep_quote), 0) FROM profit_sweeps "
            "WHERE mode = $1::execution_mode "
            "AND transfer_status = 'SKIPPED_BELOW_MINIMUM' "
            "AND created_at > COALESCE(("
            "  SELECT MAX(created_at) FROM profit_sweeps "
            "  WHERE mode = $1::execution_mode "
            "  AND transfer_status IN ('SUCCESS', 'SIMULATED')"
            "), 'epoch'::timestamptz)",
            mode,
        )
        return value or Decimal(0)

    async def open_orders(self, mode: str) -> list[dict]:
        rows = await self._conn.fetch(
            "SELECT id, position_id, symbol, purpose::text AS purpose, "
            "status::text AS status FROM orders WHERE mode = $1::execution_mode "
            "AND status IN ('PENDING', 'SUBMITTED', 'PARTIALLY_FILLED') "
            "ORDER BY created_at",
            mode,
        )
        return [dict(row) for row in rows]

    async def update_order_status(self, order_id: uuid.UUID, status: str,
                                  error_message: str | None = None) -> None:
        await self._conn.execute(
            "UPDATE orders SET status = $2::order_status, updated_at = CLOCK_TIMESTAMP(), "
            "error_message = COALESCE($3, error_message) WHERE id = $1",
            order_id, status, error_message,
        )

    async def force_lock(self, mode: str, reason: str) -> None:
        """Pause administrative : le bot cesse d'évaluer jusqu'à reprise explicite."""
        async with self._conn.transaction():
            previous = await self._conn.fetchval(
                "SELECT state::text FROM bot_state WHERE mode = $1::execution_mode FOR UPDATE", mode
            )
            if previous not in ('IDLE','COOLDOWN'):
                raise StateTransitionError('pause refusée : urgence ou opération en cours')
            await self._conn.execute(
                "UPDATE bot_state SET state = 'LOCKED_CIRCUIT_BREAKER'::system_state, "
                "since = CLOCK_TIMESTAMP(), locked_at = CLOCK_TIMESTAMP(), "
                "lock_details = $2, updated_at = CLOCK_TIMESTAMP() "
                "WHERE mode = $1::execution_mode",
                mode, reason,
            )
            await self._conn.execute(
                "INSERT INTO system_state_logs (mode, previous_state, current_state, "
                "trigger_reason) VALUES ($1::execution_mode, $2::system_state, "
                "'LOCKED_CIRCUIT_BREAKER'::system_state, $3)",
                mode, previous, reason[:150],
            )

    async def force_resume(self, mode: str, reason: str, who: str = "admin") -> None:
        from app.core.coordination import financial_guard
        async with financial_guard(self,mode,wait=True):
            if await self.pending_requests(mode):raise StateTransitionError('exécutions non rapprochées : reprise refusée')
            return await self._force_resume(mode,reason,who)

    async def _force_resume(self, mode: str, reason: str, who: str = "admin") -> None:
        """Reprise humaine : uniquement depuis un état bloqué, jamais depuis IDLE.

        Reprendre un bot qui n'est pas arrêté masquerait un bug de gestion d'état ;
        on refuse plutôt que de réécrire l'état courant.
        """
        async with self._conn.transaction():
            previous = await self._conn.fetchval(
                "SELECT state::text FROM bot_state WHERE mode = $1::execution_mode", mode
            )
            if previous not in ("LOCKED_CIRCUIT_BREAKER", "EMERGENCY_HALT"):
                raise StateTransitionError(
                    f"reprise refusée : l'état courant est {previous}, pas un état bloqué"
                )
            await self._conn.execute(
                "UPDATE bot_state SET state = 'IDLE'::system_state, "
                "since = CLOCK_TIMESTAMP(), cooldown_until = NULL, "
                "unlocked_by = $2, unlocked_at = CLOCK_TIMESTAMP(), "
                "lock_details = NULL, updated_at = CLOCK_TIMESTAMP() "
                "WHERE mode = $1::execution_mode",
                mode, who,
            )
            await self._conn.execute(
                "INSERT INTO system_state_logs (mode, previous_state, current_state, "
                "trigger_reason, resolved_at, resolved_by_admin) "
                "VALUES ($1::execution_mode, $2::system_state, 'IDLE'::system_state, "
                "$3, CLOCK_TIMESTAMP(), $4)",
                mode, previous, reason[:150], who,
            )

    async def reset_circuit_breaker(self, mode: str, who: str = "admin") -> None:
        """Remet à zéro les compteurs de circuit-breaker et repasse en IDLE."""
        async with self._conn.transaction():
            previous = await self._conn.fetchval(
                "SELECT state::text FROM bot_state WHERE mode = $1::execution_mode", mode,
            )
            if previous != "LOCKED_CIRCUIT_BREAKER":
                raise StateTransitionError(
                    f"reset refusé : état courant {previous}, pas LOCKED_CIRCUIT_BREAKER"
                )
            await self._conn.execute(
                "UPDATE bot_state SET state = 'IDLE'::system_state, "
                "consecutive_losses = 0, cooldown_until = NULL, lock_details = NULL, "
                "unlocked_by = $2, unlocked_at = CLOCK_TIMESTAMP(), "
                "updated_at = CLOCK_TIMESTAMP() "
                "WHERE mode = $1::execution_mode",
                mode, who,
            )
            await self._conn.execute(
                "INSERT INTO system_state_logs (mode, previous_state, current_state, "
                "trigger_reason, resolved_at, resolved_by_admin) "
                "VALUES ($1::execution_mode, $2::system_state, 'IDLE'::system_state, "
                "'reset circuit-breaker', CLOCK_TIMESTAMP(), $3)",
                mode, previous, who,
            )

    async def force_halt(self, mode: str, reason: str, code: str | None = None,
                         auto_resume_at: datetime | None = None) -> None:
        """Arrêt d'urgence déclenché par un humain, depuis n'importe quel état.

        Contourne volontairement le graphe : un kill-switch qui refuserait de
        fonctionner parce que l'état courant ne prévoit pas la transition serait
        inutile le jour où on en a besoin.

        `auto_resume_at` est renseigné uniquement pour un arrêt demandé par le
        modèle : le bot repart alors tout seul à cette heure-là. Un panic-sell
        humain laisse la valeur à NULL et exige une reprise explicite.
        """
        async with self._conn.transaction():
            previous = await self._conn.fetchval(
                "SELECT state::text FROM bot_state WHERE mode = $1::execution_mode", mode
            )
            await self._conn.execute(
                "UPDATE bot_state SET state = 'EMERGENCY_HALT'::system_state, "
                "since = CLOCK_TIMESTAMP(), locked_at = CLOCK_TIMESTAMP(), "
                "lock_reason = $2::risk_rejection_code, "
                "lock_details = CASE WHEN state='EMERGENCY_HALT' AND cooldown_until IS NULL AND $4::timestamptz IS NOT NULL THEN lock_details ELSE $3 END, "
                "cooldown_until = CASE WHEN state='EMERGENCY_HALT' AND cooldown_until IS NULL THEN NULL ELSE $4::timestamptz END, updated_at = CLOCK_TIMESTAMP() "
                "WHERE mode = $1::execution_mode",
                mode, code, reason, auto_resume_at,
            )
            await self._conn.execute(
                "INSERT INTO system_state_logs (mode, previous_state, current_state, "
                "trigger_reason, trigger_code) "
                "VALUES ($1::execution_mode, $2::system_state, "
                "'EMERGENCY_HALT'::system_state, $3, $4::risk_rejection_code)",
                mode, previous, reason[:150], code,
            )

    async def halt_details(self, mode: str) -> str | None:
        """Raison textuelle du dernier arrêt, pour distinguer LLM et humain."""
        return await self._conn.fetchval(
            "SELECT lock_details FROM bot_state WHERE mode = $1::execution_mode", mode
        )

    # -- réglages runtime (dashboard) -------------------------------------

    async def runtime_settings(self, mode: str) -> tuple[dict, datetime | None]:
        """Payload de réglages et date de dernière modification, depuis la base."""
        row = await self._conn.fetchrow(
            "SELECT payload, updated_at FROM runtime_settings "
            "WHERE mode = $1::execution_mode",
            mode,
        )
        if row is None:
            return {}, None
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return dict(payload or {}), row["updated_at"]

    async def update_runtime_settings(self, mode: str, payload: dict, *, updated_by: str = "dashboard", expected_revision: int | None = None) -> datetime:
        async with self._conn.transaction():
            await self._conn.execute("INSERT INTO runtime_settings(mode,payload,updated_by) VALUES($1::execution_mode,'{}',$2) ON CONFLICT DO NOTHING",mode,updated_by)
            result=await self._conn.fetchrow('UPDATE runtime_settings SET payload=$2::jsonb,updated_by=$3,updated_at=CLOCK_TIMESTAMP(),revision=revision+1 '
                'WHERE mode=$1::execution_mode AND ($4::bigint IS NULL OR revision=$4) RETURNING updated_at',mode,json.dumps(payload),updated_by,expected_revision)
            if result is None:raise StateTransitionError('révision des réglages obsolète')
            return result['updated_at']

    async def update_after_close(self, mode: str, *, consecutive_losses: int,
                                 equity: Decimal) -> None:
        await self._conn.execute(
            "UPDATE bot_state SET consecutive_losses = $2, "
            "equity_hwm_quote = GREATEST(equity_hwm_quote, $3), "
            "last_trade_at = CLOCK_TIMESTAMP(), updated_at = CLOCK_TIMESTAMP() "
            "WHERE mode = $1::execution_mode",
            mode, consecutive_losses, equity,
        )

    # -- statistiques du dashboard ---------------------------------------

    async def equity_curve(self, mode: str, limit: int = 200) -> list[dict]:
        rows = await self._conn.fetch(
            "SELECT taken_at, equity_quote, free_quote, position_value_quote "
            "FROM equity_snapshots WHERE mode = $1::execution_mode "
            "ORDER BY taken_at DESC LIMIT $2",
            mode, limit,
        )
        return [dict(row) for row in reversed(rows)]

    async def dashboard_metrics(self, mode: str) -> dict:
        row = await self._conn.fetchrow(
            "SELECT COUNT(*) AS total_trades, "
            "COUNT(*) FILTER (WHERE net_pnl_quote > 0) AS wins, "
            "COUNT(*) FILTER (WHERE net_pnl_quote <= 0) AS losses, "
            "COALESCE(SUM(net_pnl_quote), 0) AS net_pnl, "
            "COALESCE(SUM(total_fees_quote), 0) AS fees, "
            "COALESCE(SUM(net_pnl_quote) FILTER (WHERE net_pnl_quote > 0), 0) AS gross_profit, "
            "COALESCE(SUM(net_pnl_quote) FILTER (WHERE net_pnl_quote < 0), 0) AS gross_loss, "
            "COALESCE(AVG(net_pnl_quote) FILTER (WHERE net_pnl_quote > 0), 0) AS avg_win, "
            "COALESCE(AVG(net_pnl_quote) FILTER (WHERE net_pnl_quote < 0), 0) AS avg_loss, "
            "COALESCE(SUM(net_pnl_quote) / NULLIF(SUM(risk_amount_quote), 0), 0) AS r_multiple "
            "FROM positions WHERE mode = $1::execution_mode "
            "AND status IN ('CLOSED', 'EMERGENCY_LIQUIDATED')",
            mode,
        )
        return dict(row) if row else {}

    async def daily_pnl(self, mode: str, days: int = 14) -> list[dict]:
        rows = await self._conn.fetch(
            "SELECT date_trunc('day', closed_at)::date AS day, "
            "COUNT(*) AS trades, COUNT(*) FILTER (WHERE net_pnl_quote > 0) AS wins, "
            "COALESCE(SUM(net_pnl_quote), 0) AS net_pnl "
            "FROM positions WHERE mode = $1::execution_mode "
            "AND status IN ('CLOSED', 'EMERGENCY_LIQUIDATED') "
            "AND closed_at >= now() - ($2::int * interval '1 day') "
            "GROUP BY 1 ORDER BY 1",
            mode, days,
        )
        return [dict(row) for row in rows]

    async def rejection_stats(self, mode: str) -> list[dict]:
        rows = await self._conn.fetch(
            "SELECT rejection_code::text AS code, COUNT(*) AS count "
            "FROM risk_evaluations WHERE mode = $1::execution_mode AND approved = FALSE "
            "GROUP BY rejection_code ORDER BY count DESC",
            mode,
        )
        return [dict(row) for row in rows]

    async def llm_stats(self, mode: str, hours: int = 24) -> dict:
        counts = await self._conn.fetch(
            "SELECT action::text AS action, COUNT(*) AS count FROM llm_signals "
            "WHERE mode = $1::execution_mode "
            "AND created_at >= now() - ($2::int * interval '1 hour') "
            "GROUP BY action",
            mode, hours,
        )
        last = await self._conn.fetchrow(
            "SELECT created_at, symbol, action::text AS action, confidence, "
            "market_regime::text AS market_regime, model_name, inference_time_ms, thesis "
            "FROM llm_signals WHERE mode = $1::execution_mode "
            "ORDER BY created_at DESC LIMIT 1",
            mode,
        )
        total = await self._conn.fetchval(
            "SELECT COUNT(*) FROM llm_signals WHERE mode = $1::execution_mode "
            "AND created_at >= now() - ($2::int * interval '1 hour')",
            mode, hours,
        )
        degraded = await self._conn.fetchval(
            "SELECT COUNT(*) FROM llm_signals WHERE mode = $1::execution_mode "
            "AND created_at >= now() - ($2::int * interval '1 hour') "
            "AND model_name LIKE '%repli%'",
            mode, hours,
        )
        avg_ms = await self._conn.fetchval(
            "SELECT COALESCE(AVG(inference_time_ms), 0) FROM llm_signals "
            "WHERE mode = $1::execution_mode "
            "AND created_at >= now() - ($2::int * interval '1 hour')",
            mode, hours,
        )
        return {
            "counts": {row["action"]: int(row["count"]) for row in counts},
            "last": dict(last) if last else None,
            "total": int(total or 0),
            "degraded": int(degraded or 0),
            "avg_inference_ms": int(avg_ms or 0),
        }

    # -- lecture pour le CLI ---------------------------------------------

    async def recent(self, table: str, mode: str, limit: int = 5) -> list[dict]:
        if table not in {"llm_signals", "risk_evaluations", "positions", "orders"}:
            raise ValueError(f"table non exposée : {table}")
        order_column = "opened_at" if table == "positions" else "created_at"
        rows = await self._conn.fetch(
            f"SELECT * FROM {table} WHERE mode = $1::execution_mode "
            f"ORDER BY {order_column} DESC LIMIT $2",
            mode, limit,
        )
        return [dict(row) for row in rows]


    @asynccontextmanager
    async def financial_lock(self,mode,*,wait=False):
        if self._lock_connection_factory is None:raise StateTransitionError('connexion dédiée requise')
        connection=await self._lock_connection_factory()
        try:
            async with connection.transaction():
                key={'PAPER':1,'TESTNET':2,'LIVE':3}[mode]
                if wait:await connection.execute('SELECT pg_advisory_xact_lock(1414416961,$1)',key)
                elif not await connection.fetchval('SELECT pg_try_advisory_xact_lock(1414416961,$1)',key):
                    raise StateTransitionError('un autre cycle détient le verrou de ce mode')
                yield connection
        finally:await connection.close()

    @asynccontextmanager
    async def cycle_lock(self,mode):
        from app.core.coordination import financial_guard
        async with financial_guard(self,mode):yield

    async def capital_state(self, mode: str) -> CapitalState | None:
        row = await self._conn.fetchrow("SELECT * FROM capital_accounts WHERE mode=$1::execution_mode", mode)
        if row is None:
            return None
        return CapitalState(**{key: row[key] for key in CapitalState.__dataclass_fields__})

    async def ensure_capital(self, mode: str, quote: str, equity: Decimal, reserve: Decimal,
                             at: datetime) -> CapitalState:
        row = await self.capital_state(mode)
        if row is not None:
            currency = await self._conn.fetchval("SELECT quote_currency FROM capital_accounts WHERE mode=$1::execution_mode", mode)
            if currency != quote:
                raise ValueError("devise du capital différente de la configuration")
            return row
        if await self.open_position(mode) is not None:
            raise ValueError("initialisation des paliers exige un compte sans position ouverte")
        state = start_capital(equity, at, reserve=reserve)
        values = asdict(state)
        columns = ', '.join(values)
        placeholders = ', '.join(f'${i}' for i in range(3, 3 + len(values)))
        await self._conn.execute(
            f"INSERT INTO capital_accounts (mode, quote_currency, {columns}) "
            f"VALUES ($1::execution_mode,$2,{placeholders})", mode, quote, *values.values())
        return state

    async def allocate_capital(self, mode: str, position_id: uuid.UUID, pnl: Decimal,
                               at: datetime, policy, *, allow_promotion: bool) -> CapitalState:
        # À appeler dans la transaction de clôture : verrou + clé idempotente.
        await self._conn.fetchrow("SELECT mode FROM capital_accounts WHERE mode=$1::execution_mode FOR UPDATE", mode)
        previous = await self.capital_state(mode)
        if previous is None:
            raise ValueError("capital non initialisé")
        if await self._conn.fetchval("SELECT EXISTS(SELECT 1 FROM capital_events WHERE position_id=$1)", position_id):
            raise ValueError("clôture déjà affectée au capital")
        current = allocate_close(previous, pnl, at, policy, allow_promotion=allow_promotion)
        values = asdict(current)
        assignments = ', '.join(f'{key}=${i}' for i, key in enumerate(values, 2))
        await self._conn.execute(f"UPDATE capital_accounts SET {assignments} WHERE mode=$1::execution_mode", mode, *values.values())
        # Le montant promu se retrouve aussi comme diminution de la cagnotte.
        surplus = max(Decimal(0), current.realized_pnl - previous.surplus_hwm)
        promoted = surplus - (current.reserve_quote - previous.reserve_quote) - (current.growth_quote - previous.growth_quote)
        await self._conn.execute(
            "INSERT INTO capital_events (position_id,mode,closed_at,pnl_quote,active_delta,reserve_delta,growth_delta,promoted_quote) "
            "VALUES ($1,$2::execution_mode,$3,$4,$5,$6,$7,$8)", position_id, mode, at, pnl,
            current.active_quote-previous.active_quote, current.reserve_quote-previous.reserve_quote,
            current.growth_quote-previous.growth_quote, promoted)
        return current

    async def advance_paper_position(self, position: PositionRow, stop: Decimal, close_at: datetime) -> None:
        result = await self._conn.fetchval(
            "UPDATE positions SET stop_loss_price=GREATEST(stop_loss_price,$2), monitor_cursor_at=$3 "
            "WHERE id=$1 AND status='OPEN' AND monitor_cursor_at IS NOT DISTINCT FROM $4 RETURNING id",
            position.id, stop, close_at, position.monitor_cursor_at)
        if result is None:
            raise StateTransitionError("curseur de position modifié par un autre cycle")

    async def pending_exit(self, position_id: uuid.UUID):
        return await self._conn.fetchrow("SELECT * FROM pending_exits WHERE position_id=$1", position_id)

    async def prepare_exit(self, mode: str, position_id: uuid.UUID, reason: str, purpose: str, halt: bool):
        await self._conn.execute(
            "INSERT INTO pending_exits(position_id,mode,reason,purpose,halt) VALUES($1,$2::execution_mode,$3,$4,$5)",
            position_id, mode, reason, purpose, halt)

    async def confirm_exit(self, position_id: uuid.UUID, fill) -> None:
        await self._conn.execute("UPDATE pending_exits SET fill=$2::jsonb WHERE position_id=$1",
                                 position_id, json.dumps({k: str(v) if isinstance(v, Decimal) else v for k,v in asdict(fill).items()}))

    async def clear_exit(self, position_id: uuid.UUID) -> None:
        await self._conn.execute("DELETE FROM pending_exits WHERE position_id=$1", position_id)
