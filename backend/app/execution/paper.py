"""Exécution papier — un fill simulé, jamais un ordre réel.

Le modèle est volontairement pessimiste :

- l'achat paie le prix du marché **plus** un slippage, la vente le paie **moins** ;
- les frais taker sont prélevés des deux côtés ;
- si une bougie touche à la fois le stop et le take-profit, le stop est retenu :
  on ne peut pas savoir lequel est venu en premier sans données tick, et se
  tromper dans ce sens coûte de l'argent réel.

Les fills sont persistés comme des ordres (`status = FILLED`), pour que le
coupe-circuit « max ordres par heure » et l'audit voient la même chose qu'en réel.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.engine.risk_gate import RiskDecision

__all__ = ["Fill", "PaperBroker"]


@dataclass(frozen=True, slots=True)
class Fill:
    price: Decimal
    amount: Decimal
    notional_quote: Decimal
    fee_quote: Decimal
    slippage_quote: Decimal
    order_id: str | None = None
    stop_order_id: str | None = None


class PaperBroker:
    def __init__(self, taker_fee: Decimal, slippage_bps: int = 5):
        if taker_fee < 0:
            raise ValueError("taker_fee ne peut pas être négatif")
        if slippage_bps < 0:
            raise ValueError("slippage_bps ne peut pas être négatif")
        self._taker = taker_fee
        self._slippage = Decimal(slippage_bps) / Decimal(10_000)

    @property
    def execution_fee_pct(self) -> Decimal:
        return self._taker

    def fee(self, notional_quote: Decimal) -> Decimal:
        """Frais taker d'un notionnel donné, pour reconstruire un coût d'entrée."""
        return notional_quote * self._taker

    def entry(self, decision: RiskDecision, market_price: Decimal,
              *, symbol: str | None = None) -> Fill:
        """Achat au marché simulé. La quantité vient de la décision, jamais d'ici."""
        if not decision.approved or decision.amount is None:
            raise ValueError("un fill d'entrée exige une décision approuvée avec quantité")
        price = market_price * (Decimal(1) + self._slippage)
        notional = decision.amount * price
        return Fill(
            price=price,
            amount=decision.amount,
            notional_quote=notional,
            fee_quote=notional * self._taker,
            slippage_quote=(price - market_price) * decision.amount,
        )

    def exit(self, amount: Decimal, market_price: Decimal,
             *, symbol: str | None = None,
             stop_price: Decimal | None = None) -> Fill:
        price = market_price * (Decimal(1) - self._slippage)
        notional = amount * price
        return Fill(
            price=price,
            amount=amount,
            notional_quote=notional,
            fee_quote=notional * self._taker,
            slippage_quote=(market_price - price) * amount,
        )

    @staticmethod
    def exit_trigger(stop_loss_price: Decimal, take_profit_price: Decimal | None,
                     candle: list) -> tuple[Decimal, str] | None:
        """Rend (prix de sortie, raison) si la bougie clôturée a déclenché une sortie."""
        open_price = Decimal(str(candle[1]))
        high = Decimal(str(candle[2]))
        low = Decimal(str(candle[3]))

        if low <= stop_loss_price:
            # Gap sous le stop : on sort à l'ouverture, plus bas que le stop.
            return min(open_price, stop_loss_price), "STOP_LOSS"
        if take_profit_price is not None and high >= take_profit_price:
            # Gap au-dessus du TP : on sort à l'ouverture, plus haut que le TP.
            return max(open_price, take_profit_price), "TAKE_PROFIT"
        return None
