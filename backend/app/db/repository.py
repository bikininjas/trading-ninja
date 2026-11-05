"""Persistance PostgreSQL — état, signaux, décisions, positions, ordres.

Une seule connexion asyncpg (le bot est mono-processus). Les énumérations sont
castées explicitement (`$n::system_state`) : asyncpg ne devine pas les types
personnalisés, et une erreur de cast doit échouer au premier essai, pas en
production.

Aucune requête de ce module ne décide quoi que ce soit : le pare-feu décide, la
base enregistre.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import asyncpg

from app.core.db import DatabaseConfig

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


def _drawdown(peak: Decimal, equity: Decimal) -> Decimal:
    if peak <= 0:
        return Decimal(0)
    return max(Decimal(0), Decimal(1) - equity / peak)


class PostgresRepository:
    def __init__(self, connection: asyncpg.Connection):
        self._conn = connection

    @classmethod
    async def connect(cls, config: DatabaseConfig) -> "PostgresRepository":
        connection = await asyncpg.connect(**config.asyncpg_kwargs())
        return cls(connection)

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
        return AccountSnapshot(
            equity=equity,
            free_quote=free,
            hwm=hwm,
            daily_drawdown_pct=_drawdown(max(daily_peak or Decimal(0), equity), equity),
            weekly_drawdown_pct=_drawdown(max(weekly_peak or Decimal(0), equity), equity),
            from_hwm_drawdown_pct=_drawdown(max(hwm, equity), equity),
            consecutive_losses=int(counters["consecutive_losses"]) if counters else 0,
            orders_last_hour=int(orders_last_hour or 0),
            has_open_position=bool(has_position),
            reserve_quote=reserve or Decimal(0),
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
                "last_heartbeat_at = CLOCK_TIMESTAMP(), updated_at = CLOCK_TIMESTAMP() "
                "WHERE mode = $1::execution_mode",
                mode, equity,
            )

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
    ) -> uuid.UUID:
        row = await self._conn.fetchrow(
            "INSERT INTO shadow_trades (mode, symbol, kind, action, signal_id, "
            "evaluation_id, rejection_code, rejection_details, decision_price, amount, "
            "notional_quote, stop_loss_price, take_profit_price, risk_amount_quote, "
            "plan_status) VALUES ($1::execution_mode, $2, $3, $4::signal_action, $5, "
            "$6, $7::risk_rejection_code, $8, $9, $10, $11, $12, $13, $14, $15) "
            "RETURNING id",
            mode, symbol, kind, action, signal_id, evaluation_id,
            rejection_code, rejection_details, decision_price, amount,
            notional_quote, stop_loss_price, take_profit_price, risk_amount_quote,
            plan_status,
        )
        return row["id"]

    async def open_shadow_trades(self, mode: str, limit: int = 200) -> list[dict]:
        rows = await self._conn.fetch(
            "SELECT id, created_at, symbol, kind, plan_status, decision_price, amount, "
            "stop_loss_price, take_profit_price FROM shadow_trades "
            "WHERE mode = $1::execution_mode AND plan_status = 'OPEN' "
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
    ) -> tuple[uuid.UUID, int]:
        async with self._conn.transaction():
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
            "ORDER BY created_at DESC LIMIT $2",
            mode, limit,
        )
        return [dict(row) for row in rows]

    # -- positions et ordres ---------------------------------------------

    async def open_positions(self, mode: str) -> list[PositionRow]:
        rows = await self._conn.fetch(
            "SELECT id, symbol, entry_price, initial_amount, stop_loss_price, "
            "take_profit_price, opened_at, risk_amount_quote FROM positions "
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
            )
            for row in rows
        ]

    async def open_position(self, mode: str) -> PositionRow | None:
        positions = await self.open_positions(mode)
        return positions[0] if positions else None

    async def insert_order(self, mode: str, *, client_order_id: str, symbol: str,
                           side: str, order_type: str, purpose: str, status: str,
                           requested_amount: Decimal, filled_amount: Decimal,
                           requested_price: Decimal | None, stop_price: Decimal | None,
                           average_fill_price: Decimal, fee_amount: Decimal,
                           fee_currency: str, slippage_quote: Decimal | None,
                           position_id: uuid.UUID | None = None,
                           risk_evaluation_id: uuid.UUID | None = None,
                           error_message: str | None = None) -> uuid.UUID:
        return await self._conn.fetchval(
            "INSERT INTO orders (position_id, risk_evaluation_id, client_order_id, symbol, "
            "mode, side, order_type, purpose, status, requested_amount, filled_amount, "
            "requested_price, stop_price, average_fill_price, fee_amount, fee_currency, "
            "slippage_quote, error_message) "
            "VALUES ($1, $2, $3, $4, $5::execution_mode, $6::order_side, $7::order_type, "
            "$8::order_purpose, $9::order_status, $10, $11, $12, $13, $14, $15, $16, $17, $18) "
            "RETURNING id",
            position_id, risk_evaluation_id, client_order_id, symbol, mode, side,
            order_type, purpose, status, requested_amount, filled_amount,
            requested_price, stop_price, average_fill_price, fee_amount, fee_currency,
            slippage_quote, error_message,
        )

    async def create_position(self, mode: str, *, symbol: str, entry_price: Decimal,
                              amount: Decimal, notional_quote: Decimal,
                              stop_loss_price: Decimal, take_profit_price: Decimal | None,
                              risk_amount_quote: Decimal, r_multiple_at_tp: Decimal | None,
                              signal_id: uuid.UUID, evaluation_id: uuid.UUID,
                              protective_order_id: str) -> uuid.UUID:
        return await self._conn.fetchval(
            "INSERT INTO positions (symbol, mode, entry_signal_id, entry_risk_evaluation_id, "
            "entry_price, initial_amount, initial_notional_quote, stop_loss_price, "
            "take_profit_price, risk_amount_quote, r_multiple_at_tp, protective_order_id, "
            "protective_order_ok, protective_checked_at) "
            "VALUES ($1, $2::execution_mode, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, "
            "TRUE, CLOCK_TIMESTAMP()) RETURNING id",
            symbol, mode, signal_id, evaluation_id, entry_price, amount, notional_quote,
            stop_loss_price, take_profit_price, risk_amount_quote, r_multiple_at_tp,
            protective_order_id,
        )

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
                "SELECT state::text FROM bot_state WHERE mode = $1::execution_mode", mode
            )
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
                "lock_reason = $2::risk_rejection_code, lock_details = $3, "
                "cooldown_until = $4, updated_at = CLOCK_TIMESTAMP() "
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

    async def update_runtime_settings(self, mode: str, payload: dict,
                                      *, updated_by: str = "dashboard") -> datetime:
        """Écrit les réglages du dashboard. Une ligne par mode, jamais partagée."""
        row = await self._conn.fetchrow(
            "INSERT INTO runtime_settings (mode, payload, updated_by) "
            "VALUES ($1::execution_mode, $2::jsonb, $3) "
            "ON CONFLICT (mode) DO UPDATE SET payload = EXCLUDED.payload, "
            "updated_by = EXCLUDED.updated_by, updated_at = CLOCK_TIMESTAMP() "
            "RETURNING updated_at",
            mode, json.dumps(payload, ensure_ascii=False), updated_by,
        )
        return row["updated_at"]

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
