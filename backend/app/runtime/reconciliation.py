"""Réconciliation au démarrage — la base et l'exchange doivent décrire le même état.

Ce module répond à une seule question, mais c'est celle qui coûte cher : **la
position ouverte en base est-elle réellement protégée par un stop chez
l'exchange ?** Une position sans stop distant est une position nue ; elle peut
perdre bien plus que le budget validé pendant que le processus est arrêté.

Trois principes :

- **Lecture seule ici.** `inspect_exchange()` ne modifie ni la base ni l'exchange.
  Il produit un constat et un plan ; l'exécuteur live appliquera le plan.
- **Aucune position distante n'est attendue en papier.** Le mode papier n'envoie
  jamais d'ordre : y lire des ordres ouverts signale un compte partagé, pas une
  position du bot.
- **Fail-closed.** Stop absent, trop petit ou plus bas que le stop validé →
  liquidation demandée. Solde de base insuffisant → alerte critique et arrêt,
  jamais une vente à découvert.

Le format des ordres est celui de CCXT 4.5.91 : un stop de vente est exposé comme
`stopLossPrice`, et un take-profit comme `takeProfitPrice` (jamais
`stopPrice`, qui n'existe pas dans cette version). Le parsing reste tolérant sur
`triggerPrice` pour les versions futures, mais refuse toute valeur non numérique
plutôt que de la convertir en zéro.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from app.db.repository import PositionRow

__all__ = [
    "CRITICAL",
    "OK",
    "WARN",
    "Finding",
    "ReconciliationPlan",
    "RemoteOrder",
    "apply_reconciliation",
    "inspect_exchange",
    "plan_reconciliation",
    "read_account",
    "read_remote_orders",
]

OK = "OK"
WARN = "WARN"
CRITICAL = "CRITICAL"

# Mode base de données qui attend des ordres réels. Les autres modes (PAPER,
# TESTNET) ne doivent jamais voir une position distante attribuée au bot.
LIVE_MODE = "LIVE"


@dataclass(frozen=True, slots=True)
class RemoteOrder:
    """Un ordre ouvert chez l'exchange, réduit à ce que la réconciliation lit."""

    id: str
    symbol: str
    side: str
    order_type: str
    amount: Decimal
    stop_price: Decimal | None
    status: str
    client_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class Finding:
    """Un constat, avec un code stable et une phrase lisible à 3 h du matin."""

    code: str
    severity: str
    symbol: str | None
    details: str


@dataclass(frozen=True, slots=True)
class ReconciliationPlan:
    """Constat + actions minimales. `liquidate` contient des symboles, pas des prix."""

    mode: str
    quote_currency: str
    positions: int
    remote_orders: int
    quote_balance: Decimal | None
    findings: tuple[Finding, ...]
    liquidate: tuple[str, ...] = ()
    halt: bool = False

    @property
    def critical(self) -> tuple[Finding, ...]:
        return tuple(item for item in self.findings if item.severity == CRITICAL)

    @property
    def clean(self) -> bool:
        return not self.critical and not self.halt

    def lines(self) -> list[str]:
        head = (
            f"mode {self.mode} · positions {self.positions} · "
            f"ordres distants {self.remote_orders}"
        )
        if self.quote_balance is not None:
            head += f" · solde {self.quote_balance} {self.quote_currency}"
        out = [head]
        for finding in self.findings:
            prefix = {"OK": "OK", "WARN": "ALERTE", "CRITICAL": "CRITIQUE"}[finding.severity]
            where = f" {finding.symbol}" if finding.symbol else ""
            out.append(f"  {prefix:8} {finding.code}{where} — {finding.details}")
        if self.liquidate:
            out.append(
                "  PLAN     liquidation immédiate : " + ", ".join(self.liquidate)
            )
        if self.halt:
            out.append(
                "  PLAN     arrêt : le bot ne doit pas trader tant que l'écart "
                "base/exchange n'est pas résolu"
            )
        return out


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed


def _finding(code: str, severity: str, symbol: str | None, details: str) -> Finding:
    return Finding(code=code, severity=severity, symbol=symbol, details=details)


def plan_reconciliation(
    positions: Sequence[PositionRow],
    remote_orders: Sequence[RemoteOrder],
    balances: Mapping[str, Decimal],
    *,
    mode: str,
    quote_currency: str,
    quote_balance: Decimal | None = None,
) -> ReconciliationPlan:
    """Construit le constat et le plan, sans I/O et sans horloge.

    `balances` est indexé par symbole unifié (`BTC/EUR`) et contient la quantité
    de base disponible. Une quantité supérieure à la position est normale : le
    compte peut contenir des avoirs manuels ; une quantité inférieure ne l'est
    pas.
    """
    findings: list[Finding] = []
    liquidate: list[str] = []
    halt = False

    if mode != LIVE_MODE:
        findings.append(
            _finding(
                "NO_REMOTE_POSITION_EXPECTED", OK, None,
                f"mode {mode} : aucune position ni aucun ordre distant attendu du bot",
            )
        )
        for order in remote_orders:
            findings.append(
                _finding(
                    "REMOTE_ORDER_OPEN", WARN, order.symbol,
                    f"ordre {order.id} {order.side} {order.order_type} "
                    f"{order.amount} ouvert sur le compte, non géré par le bot "
                    f"(mode {mode})",
                )
            )
        return ReconciliationPlan(
            mode=mode, quote_currency=quote_currency,
            positions=len(positions), remote_orders=len(remote_orders),
            quote_balance=quote_balance, findings=tuple(findings),
        )

    if not positions:
        findings.append(
            _finding("NO_OPEN_POSITION", OK, None, "aucune position ouverte en base")
        )

    matched_order_ids: set[str] = set()
    for position in positions:
        available = balances.get(position.symbol)
        asset_missing = available is None or available < position.amount
        if asset_missing:
            halt = True
            findings.append(
                _finding(
                    "BALANCE_TOO_SMALL", CRITICAL, position.symbol,
                    f"position en base de {position.amount}, solde de base "
                    f"{available if available is not None else 'inconnu'} : "
                    "la position n'est pas couvrable, aucune vente ne doit partir",
                )
            )

        stops = [
            order
            for order in remote_orders
            if order.symbol == position.symbol
            and order.side == "sell"
            and order.stop_price is not None
        ]
        covering = [
            order
            for order in stops
            if order.amount >= position.amount
            and order.stop_price is not None
            and order.stop_price >= position.stop_loss_price
        ]
        if covering:
            best = max(covering, key=lambda order: order.stop_price or Decimal(0))
            matched_order_ids.add(best.id)
            findings.append(
                _finding(
                    "STOP_ARMED", OK, position.symbol,
                    f"stop {best.id} armé à {best.stop_price} pour {best.amount} "
                    f"(stop validé {position.stop_loss_price})",
                )
            )
        elif stops:
            largest = max(stops, key=lambda order: order.amount)
            if largest.amount < position.amount:
                detail = (
                    f"stop {largest.id} ne couvre que {largest.amount} sur "
                    f"{position.amount}"
                )
            else:
                detail = (
                    f"stop {largest.id} à {largest.stop_price}, sous le stop validé "
                    f"{position.stop_loss_price} : la perte dépasserait le budget"
                )
            if not asset_missing:
                liquidate.append(position.symbol)
            findings.append(
                _finding("STOP_INSUFFICIENT", CRITICAL, position.symbol, detail)
            )
        else:
            if not asset_missing:
                liquidate.append(position.symbol)
            findings.append(
                _finding(
                    "STOP_MISSING", CRITICAL, position.symbol,
                    f"position ouverte en base ({position.amount}) sans ordre de "
                    "stop distant : liquidation immédiate",
                )
            )

    for order in remote_orders:
        if order.id in matched_order_ids:
            continue
        if order.side == "sell" and order.stop_price is not None:
            # Un stop vendeur sans position connue est le pire résidu : il peut
            # solder des avoirs manuels quand il se déclenche. On ne l'annule pas
            # ici (audit lecture seule), mais on refuse de trader tant qu'il vit.
            halt = True
            findings.append(
                _finding(
                    "ORPHAN_STOP", CRITICAL, order.symbol,
                    f"stop vendeur {order.id} à {order.stop_price} pour "
                    f"{order.amount} sans position en base : il peut vendre des "
                    "avoirs non suivis",
                )
            )
            continue
        findings.append(
            _finding(
                "UNMATCHED_REMOTE_ORDER", WARN, order.symbol,
                f"ordre {order.id} {order.side} {order.order_type} {order.amount} "
                f"non rattaché à une position en base",
            )
        )

    return ReconciliationPlan(
        mode=mode, quote_currency=quote_currency,
        positions=len(positions), remote_orders=len(remote_orders),
        quote_balance=quote_balance, findings=tuple(findings),
        liquidate=tuple(dict.fromkeys(liquidate)), halt=halt,
    )


def read_remote_orders(exchange: Any, symbols: Sequence[str]) -> list[RemoteOrder]:
    """Lit les ordres ouverts de l'exchange, limités à l'univers du bot.

    `fetch_open_orders()` sans symbole est supporté par Kraken via CCXT. Les
    ordres hors univers sont ignorés : ils appartiennent à une activité manuelle
    et le bot n'a aucun titre à les commenter.
    """
    allowed = set(symbols)
    raw_orders = exchange.fetch_open_orders()
    out: list[RemoteOrder] = []
    for raw in raw_orders:
        symbol = raw.get("symbol")
        if symbol not in allowed:
            continue
        status = str(raw.get("status") or "").lower()
        if status and status not in {"open", "new", "pending"}:
            continue
        stop = raw.get("stopLossPrice")
        if stop is None:
            stop = raw.get("triggerPrice")
        out.append(
            RemoteOrder(
                id=str(raw.get("id") or ""),
                symbol=symbol,
                side=str(raw.get("side") or "").lower(),
                order_type=str(raw.get("type") or ""),
                amount=_decimal(raw.get("amount") or 0),
                stop_price=_optional_decimal(stop),
                status=status or "open",
                client_order_id=(
                    str(raw["clientOrderId"])
                    if raw.get("clientOrderId") is not None
                    else None
                ),
            )
        )
    return out


def read_account(
    exchange: Any, symbols: Sequence[str], quote_currency: str
) -> tuple[dict[str, Decimal], Decimal | None]:
    """Solde de base par symbole et solde de cotation, en `Decimal`."""
    balance = exchange.fetch_balance()
    total = balance.get("total") or {}
    free = balance.get("free") or {}

    def amount(currency: str) -> Decimal | None:
        value = total.get(currency)
        if value is None:
            value = free.get(currency)
        return None if value is None else _decimal(value)

    balances: dict[str, Decimal] = {}
    for symbol in symbols:
        base = symbol.split("/")[0]
        balances[symbol] = amount(base) or Decimal(0)
    return balances, amount(quote_currency)


async def inspect_exchange(
    repo: Any,
    exchange: Any,
    mode: str,
    symbols: Sequence[str],
    *,
    quote_currency: str,
) -> ReconciliationPlan:
    """Lit base et exchange, puis délègue le verdict à `plan_reconciliation`."""
    positions = await repo.open_positions(mode)
    remote_orders = read_remote_orders(exchange, symbols)
    balances, quote_balance = read_account(exchange, symbols, quote_currency)
    return plan_reconciliation(
        positions, remote_orders, balances,
        mode=mode, quote_currency=quote_currency, quote_balance=quote_balance,
    )


async def apply_reconciliation(
    plan: ReconciliationPlan,
    repo: Any,
    exchange: Any,
    broker: Any,
    *,
    starting_equity: Decimal,
    now: Any = None,
) -> list[str]:
    """Applique le plan minimal : liquider les positions signalées, sinon halter.

    Cette fonction est **le seul chemin d'écriture** de la réconciliation. Elle
    ne crée jamais de position et ne déplace jamais un stop : elle solde ce que
    le plan considère comme non protégé, puis laisse le bot arrêté. Le broker
    réel annule les protections avant la vente ; le broker papier ne fait que
    mettre le ledger à jour.
    """
    if plan.mode != LIVE_MODE:
        raise ValueError("apply_reconciliation est réservé au mode LIVE")
    if not plan.liquidate:
        if plan.halt or plan.critical:
            await repo.force_halt(
                plan.mode,
                "réconciliation base/exchange : " + "; ".join(
                    finding.code for finding in plan.findings
                    if finding.severity != OK
                ),
            )
            return ["halte forcée : écart base/exchange"]
        return []

    from app.runtime.recovery import liquidate_open_positions

    results = await liquidate_open_positions(
        repo, exchange, broker, plan.mode,
        quote_currency=plan.quote_currency,
        starting_equity=starting_equity,
        reason="RECONCILIATION",
        now=now,
        only_symbols=set(plan.liquidate),
    )
    return [
        f"liquidé {item.symbol} {item.amount} @ {item.exit_price} "
        f"(net {item.net_pnl_quote})"
        for item in results
    ]
