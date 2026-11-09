"""Exécution réelle Kraken — un ordre ne part que d'une décision approuvée.

Ce module est le seul endroit qui appelle `create_order()`. Il ne décide rien :
la quantité et le stop viennent d'une `RiskDecision` approuvée par le pare-feu.
Trois règles y sont tenues :

- **Le stop part avec l'achat.** Un bracket Kraken (`close`) est joint à l'ordre
  marché. Après le fill, le stop est relu chez l'exchange ; s'il manque, la
  position est immédiatement revendue et l'entrée échoue.
- **Une sortie annule d'abord ses protections.** Sinon le stop peut se déclencher
  pendant la vente et vendre deux fois. Si la vente échoue après l'annulation,
  le stop est réarmé ; si le réarmement échoue aussi, l'erreur dit que la
  position peut être nue.
- **Les fills réels font foi.** Prix moyen, quantité remplie et frais sont lus
  dans la réponse de l'exchange, jamais estimés à partir du prix demandé.

Aucun test de ce module n'a besoin du réseau : `FakeExchange` dans
`tests/test_live_execution.py` rejoue les réponses CCXT.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Sequence

from app.engine.risk_gate import RiskDecision
from app.exchange.kraken import build_bracket_entry, to_exchange_amount
from app.execution.paper import Fill, PaperBroker

__all__ = [
    "LiveBroker",
    "LiveExecutionError",
    "PanicExit",
    "PanicReport",
    "RemotePositionState",
]

# Tolérance sur la quantité couverte par le stop : Kraken arrondit au pas du
# marché, et un stop à 0,00000001 sous la position n'est pas une position nue.
_AMOUNT_TOLERANCE = Decimal("0.999")

# Un solde résiduel sous 0,1 % de la quantité vendue est de la poussière
# d'arrondi, pas une position nue.
_DUST_RATIO = Decimal("0.001")


class LiveExecutionError(RuntimeError):
    """L'ordre réel n'a pas abouti dans un état sûr. Le bot doit s'arrêter."""


@dataclass(frozen=True, slots=True)
class PanicExit:
    """Résultat d'une liquidation d'urgence sur un symbole.

    ``fill`` est renseigné quand la vente est confirmée par l'exchange ;
    ``error`` quand elle a échoué (la position peut alors être encore ouverte).
    """

    symbol: str
    amount: Decimal
    fill: Fill | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PanicReport:
    """Vue complète d'un panic-sell : ventes, annulations et erreurs.

    Le rapport ne lève pas : une urgence doit tout tenter, puis dire exactement
    ce qui a réussi et ce qui reste exposé.
    """

    exits: tuple[PanicExit, ...]
    canceled_orders: int
    errors: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.errors


@dataclass(frozen=True, slots=True)
class RemotePositionState:
    """Ce que l'exchange dit de la position, avant toute décision locale."""

    symbol: str
    base_total: Decimal
    stop_ok: bool
    stop_order_id: str | None


def _decimal(value: Any, default: Decimal = Decimal(0)) -> Decimal:
    if value is None or value == "":
        return default
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LiveExecutionError(f"valeur numérique illisible : {value!r}") from exc
    if not parsed.is_finite():
        raise LiveExecutionError(f"valeur non finie : {value!r}")
    return parsed


def _status(order: dict) -> str:
    return str(order.get("status") or "").lower()


def _is_final(order: dict) -> bool:
    status = _status(order)
    if status in {"closed", "filled"}:
        return True
    # Un ordre annulé après un fill partiel reste une exécution partielle.
    return status == "canceled" and _decimal(order.get("filled")) > 0


def _is_dead(order: dict) -> bool:
    return _status(order) in {"canceled", "rejected", "expired"}


class LiveBroker:
    """Broker réel, asynchrone, volontairement pessimiste.

    Le constructeur ne contacte pas l'exchange. `load_markets()` doit avoir été
    appelé avant la première entrée (comme pour `build_bracket_entry`).
    """

    def __init__(
        self,
        exchange: Any,
        *,
        quote_currency: str,
        taker_fee: Decimal,
        poll_interval: float = 1.0,
        timeout_seconds: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if taker_fee < 0:
            raise ValueError("taker_fee ne peut pas être négatif")
        if poll_interval < 0:
            raise ValueError("poll_interval ne peut pas être négatif")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds doit être positif")
        self._exchange = exchange
        self._quote = quote_currency
        self._taker = taker_fee
        self._poll = poll_interval
        self._timeout = timeout_seconds
        self._sleep = sleep

    # -- primitives -----------------------------------------------------

    async def _call(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """CCXT est synchrone ; on ne bloque jamais la boucle asyncio."""
        return await asyncio.to_thread(function, *args, **kwargs)

    @property
    def execution_fee_pct(self) -> Decimal:
        return self._taker

    def fee(self, notional_quote: Decimal) -> Decimal:
        """Estimation utilisée seulement si l'exchange n'a pas renvoyé ses frais."""
        return notional_quote * self._taker

    @staticmethod
    def exit_trigger(stop_loss_price: Decimal, take_profit_price: Decimal | None,
                     candle: list) -> tuple[Decimal, str] | None:
        """Le stop et le TP restent surveillés par la boucle, comme en papier."""
        return PaperBroker.exit_trigger(stop_loss_price, take_profit_price, candle)

    async def _wait_for_fill(self, order: dict, symbol: str) -> dict:
        order_id = str(order.get("id") or "")
        if not order_id:
            raise LiveExecutionError("l'exchange n'a pas renvoyé d'identifiant d'ordre")
        deadline = time.monotonic() + self._timeout
        last = order
        while True:
            if _is_final(last):
                if _decimal(last.get("filled")) <= 0:
                    raise LiveExecutionError(
                        f"ordre {order_id} terminé sans quantité remplie"
                    )
                return last
            if _is_dead(last):
                raise LiveExecutionError(
                    f"ordre {order_id} {_status(last)} sans exécution"
                )
            if time.monotonic() >= deadline:
                await self._cancel_quietly(order_id, symbol)
                raise LiveExecutionError(
                    f"ordre {order_id} non rempli après {self._timeout:.1f}s : annulé"
                )
            await self._sleep(self._poll)
            try:
                last = await self._call(self._exchange.fetch_order, order_id, symbol)
            except Exception as exc:  # noqa: BLE001 — un ordre non lisible doit arrêter
                raise LiveExecutionError(
                    f"ordre {order_id} illisible après envoi : {type(exc).__name__}: {exc}"
                ) from exc

    async def _cancel_quietly(self, order_id: str, symbol: str) -> None:
        try:
            await self._call(self._exchange.cancel_order, order_id, symbol)
        except Exception:  # noqa: BLE001 — l'annulation de secours ne masque pas l'erreur d'origine
            return

    async def _open_orders(self, symbol: str) -> list[dict]:
        try:
            orders = await self._call(self._exchange.fetch_open_orders, symbol)
        except Exception as exc:  # noqa: BLE001 — sans lecture, on ne peut pas protéger
            raise LiveExecutionError(
                f"ordres ouverts illisibles pour {symbol} : {type(exc).__name__}: {exc}"
            ) from exc
        return list(orders or [])

    @staticmethod
    def _stop_price(order: dict) -> Decimal | None:
        for key in ("stopLossPrice", "triggerPrice", "stopPrice"):
            value = order.get(key)
            if value not in (None, ""):
                return _decimal(value)
        params = order.get("params") or {}
        for key in ("stopLossPrice", "triggerPrice", "stopPrice"):
            value = params.get(key)
            if value not in (None, ""):
                return _decimal(value)
        return None

    async def _find_stop(self, symbol: str, amount: Decimal,
                         stop_price: Decimal) -> dict | None:
        for order in await self._open_orders(symbol):
            if str(order.get("side") or "").lower() != "sell":
                continue
            remote_stop = self._stop_price(order)
            if remote_stop is None or remote_stop < stop_price:
                continue
            remote_amount = _decimal(order.get("amount") or order.get("remaining"))
            if remote_amount >= amount * _AMOUNT_TOLERANCE:
                return order
        return None

    async def _wait_for_stop(self, symbol: str, amount: Decimal,
                             stop_price: Decimal) -> dict:
        deadline = time.monotonic() + self._timeout
        while True:
            stop = await self._find_stop(symbol, amount, stop_price)
            if stop is not None:
                return stop
            if time.monotonic() >= deadline:
                raise LiveExecutionError(
                    f"stop absent ou insuffisant pour {symbol} après le fill : "
                    "position non protégée"
                )
            await self._sleep(self._poll)

    def _fill_from_order(self, order: dict, market_price: Decimal, side: str) -> Fill:
        amount = _decimal(order.get("filled") or order.get("amount"))
        if amount <= 0:
            raise LiveExecutionError("fill sans quantité positive")
        price = _decimal(order.get("average") or order.get("price"), market_price)
        notional = amount * price
        fee = self._fee_from_order(order, notional)
        slippage = (
            (price - market_price) * amount if side == "buy"
            else (market_price - price) * amount
        )
        return Fill(
            price=price,
            amount=amount,
            notional_quote=notional,
            fee_quote=fee,
            slippage_quote=slippage,
            order_id=str(order.get("id") or "") or None,
        )

    def _fee_from_order(self, order: dict, notional: Decimal) -> Decimal:
        fee = order.get("fee")
        if isinstance(fee, dict) and fee.get("cost") not in (None, ""):
            currency = str(fee.get("currency") or self._quote).upper()
            if currency != self._quote.upper():
                # Sans taux de conversion sûr, on ne convertit pas un frais en base
                # en frais de cotation : on retombe sur le tarif taker configuré.
                return self.fee(notional)
            return _decimal(fee.get("cost"))
        fees = order.get("fees")
        if isinstance(fees, list) and fees:
            total = Decimal(0)
            for item in fees:
                if not isinstance(item, dict) or item.get("cost") in (None, ""):
                    continue
                currency = str(item.get("currency") or self._quote).upper()
                if currency != self._quote.upper():
                    return self.fee(notional)
                total += _decimal(item.get("cost"))
            if total > 0:
                return total
        return self.fee(notional)

    # -- entrée ---------------------------------------------------------

    async def entry(self, decision: RiskDecision, market_price: Decimal, *,
                    symbol: str) -> Fill:
        """Achat marché + stop attaché, puis vérification du stop chez l'exchange."""
        existing = await self.total_base(symbol)
        if decision.amount is not None and existing > decision.amount * Decimal("0.01"):
            raise LiveExecutionError(
                f"solde {symbol.split('/')[0]} déjà présent ({existing}) : "
                "le bot ne peut pas distinguer sa position d'un avoir manuel. "
                "Utiliser un sous-compte dédié."
            )
        args = build_bracket_entry(self._exchange, decision, symbol)
        try:
            order = await self._call(self._exchange.create_order, **args)
        except Exception as exc:  # noqa: BLE001 — aucun ordre n'a été accepté
            raise LiveExecutionError(
                f"achat refusé par l'exchange : {type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(order, dict):
            raise LiveExecutionError("réponse d'ordre illisible")

        filled = await self._wait_for_fill(order, symbol)
        fill = self._fill_from_order(filled, market_price, "buy")

        try:
            stop = await self._wait_for_stop(symbol, fill.amount,
                                             decision.stop_loss_price or Decimal(0))
        except LiveExecutionError:
            # La position existe mais n'est pas protégée : on la solde tout de
            # suite. Si cette vente échoue, l'erreur doit le dire explicitement.
            try:
                await self.emergency_exit(fill.amount, symbol=symbol)
            except LiveExecutionError as unwind_error:
                raise LiveExecutionError(
                    f"stop absent et revente d'urgence impossible : {unwind_error}"
                ) from unwind_error
            raise
        return Fill(
            price=fill.price, amount=fill.amount, notional_quote=fill.notional_quote,
            fee_quote=fill.fee_quote, slippage_quote=fill.slippage_quote,
            order_id=fill.order_id, stop_order_id=str(stop.get("id") or "") or None,
        )

    # -- sortie ---------------------------------------------------------

    async def cancel_open_orders(self, *, symbol: str) -> int:
        """Annule tous les ordres ouverts du symbole. Retourne le nombre annulé."""
        canceled = 0
        for order in await self._open_orders(symbol):
            order_id = str(order.get("id") or "")
            if not order_id:
                continue
            try:
                await self._call(self._exchange.cancel_order, order_id, symbol)
                canceled += 1
            except Exception as exc:  # noqa: BLE001 — un ordre peut avoir disparu
                # Un ordre déjà exécuté n'est plus annulable ; on continue, mais
                # un refus inconnu doit arrêter la sortie avant la vente.
                message = f"{type(exc).__name__}: {exc}".lower()
                if "not found" in message or "unknown order" in message:
                    continue
                raise LiveExecutionError(
                    f"annulation de {order_id} impossible : {type(exc).__name__}: {exc}"
                ) from exc
        return canceled

    async def cancel_all_orders(self, symbols: Sequence[str]) -> int:
        """Annule **tous** les ordres ouverts, pas seulement ceux d'un symbole.

        C'est la première étape d'un panic-sell : un stop oublié peut se
        déclencher pendant la vente au marché et vendre deux fois. On demande la
        liste globale ; si l'exchange exige un symbole, on balaie l'univers connu.
        """
        orders: list[dict] = []
        try:
            orders = list(await self._call(self._exchange.fetch_open_orders) or [])
        except Exception:  # noqa: BLE001 — repli par symbole, pas un abandon
            for symbol in symbols:
                orders.extend(await self._open_orders(symbol))

        canceled = 0
        for order in orders:
            order_id = str(order.get("id") or "")
            symbol = str(order.get("symbol") or "")
            if not order_id or not symbol:
                raise LiveExecutionError(
                    f"ordre ouvert illisible ({order_id!r}, {symbol!r}) : "
                    "annulation impossible"
                )
            try:
                await self._call(self._exchange.cancel_order, order_id, symbol)
                canceled += 1
            except Exception as exc:  # noqa: BLE001 — un ordre peut avoir disparu
                message = f"{type(exc).__name__}: {exc}".lower()
                if "not found" in message or "unknown order" in message:
                    continue
                raise LiveExecutionError(
                    f"annulation de {order_id} ({symbol}) impossible : "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
        return canceled

    def _held_balances(self, balance: dict[str, dict[str, Decimal]],
                       symbols: Sequence[str]) -> list[tuple[str, Decimal]]:
        """Symboles réellement détenus, y compris hors univers configuré.

        Le panic-sell ne doit pas laisser derrière lui un solde manuel oublié :
        un sous-compte dédié n'en a pas, mais une urgence ne suppose rien.
        """
        targets: dict[str, Decimal] = {}
        for symbol in symbols:
            base = symbol.split("/")[0]
            amount = balance.get(base, {}).get("total", Decimal(0))
            if amount > 0:
                targets[symbol] = amount

        markets = getattr(self._exchange, "markets", {}) or {}
        for symbol, market in markets.items():
            if not isinstance(market, dict):
                continue
            if str(market.get("quote") or "").upper() != self._quote.upper():
                continue
            if market.get("spot") is False:
                continue
            base = str(market.get("base") or "")
            amount = balance.get(base, {}).get("total", Decimal(0))
            if amount > 0 and symbol not in targets:
                targets[symbol] = amount
        return list(targets.items())

    async def panic_sell(self, symbols: Sequence[str]) -> PanicReport:
        """Liquidation d'urgence : annule tout, vend tout, vérifie le solde.

        Ne lève pas : chaque échec est consigné pour que l'appelant puisse
        halter le bot et afficher ce qui reste exposé. ``emergency_exit`` annule
        de nouveau les ordres du symbole avant de vendre, donc un échec de
        l'annulation globale ne laisse pas un stop actif pendant la vente.
        """
        errors: list[str] = []
        canceled = 0
        try:
            canceled = await self.cancel_all_orders(symbols)
        except LiveExecutionError as exc:
            errors.append(str(exc))

        try:
            balance = await self.balance()
        except Exception as exc:  # noqa: BLE001 — sans solde, on ne peut rien vendre
            message = f"solde illisible : {type(exc).__name__}: {exc}"
            return PanicReport((), canceled, (*errors, message))

        exits: list[PanicExit] = []
        for symbol, amount in self._held_balances(balance, symbols):
            try:
                fill = await self.emergency_exit(amount, symbol=symbol)
                residual = await self.total_base(symbol)
                if residual > amount * _DUST_RATIO:
                    raise LiveExecutionError(
                        f"reste {residual} après la vente de {amount} : "
                        "position partiellement soldée"
                    )
            except LiveExecutionError as exc:
                message = f"{symbol} : {exc}"
                errors.append(message)
                exits.append(PanicExit(symbol, amount, None, str(exc)))
                continue
            exits.append(PanicExit(symbol, amount, fill, None))
        return PanicReport(tuple(exits), canceled, tuple(errors))

    async def _rearm_stop(self, symbol: str, amount: Decimal,
                          stop_price: Decimal) -> str:
        formatted_amount = to_exchange_amount(self._exchange, symbol, amount)
        price = str(stop_price)
        try:
            order = await self._call(
                self._exchange.create_order, symbol, "stop-loss", "sell",
                formatted_amount, None, {"stopLossPrice": price},
            )
        except Exception as exc:  # noqa: BLE001 — le message doit être actionnable
            raise LiveExecutionError(
                f"réarmement du stop impossible pour {symbol} : "
                f"{type(exc).__name__}: {exc} — la position peut être nue"
            ) from exc
        return str((order or {}).get("id") or "")

    async def _read_order(self, order_id: str, symbol: str) -> dict:
        try:
            order = await self._call(self._exchange.fetch_order, order_id, symbol)
        except Exception as exc:  # noqa: BLE001 — sans lecture, aucun remplacement
            raise LiveExecutionError(
                f"ordre {order_id} illisible pour {symbol} : "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(order, dict):
            raise LiveExecutionError(f"réponse illisible pour l'ordre {order_id}")
        return order

    async def _edit_stop(self, symbol: str, amount: Decimal, old_stop_id: str,
                         new_stop_price: Decimal) -> str | None:
        """Tente ``edit_order`` seulement si CCXT annonce le support natif.

        Un échec d'édition est ambigu : l'exchange a peut-être déjà déplacé le
        stop. On relit donc l'ordre avant de décider. Si le stop est au nouveau
        niveau, l'édition est un succès ; sinon on refuse, sans créer un second
        stop qui doublerait la protection.
        """
        edit_order = getattr(self._exchange, "edit_order", None)
        has = getattr(self._exchange, "has", {}) or {}
        if not callable(edit_order) or has.get("editOrder") is not True:
            return None
        formatted_amount = to_exchange_amount(self._exchange, symbol, amount)
        try:
            await self._call(
                edit_order, old_stop_id, symbol, "stop-loss", "sell",
                formatted_amount, None, {"stopLossPrice": str(new_stop_price)},
            )
        except Exception as exc:  # noqa: BLE001 — relire avant toute décision
            current = await self._read_order(old_stop_id, symbol)
            remote = self._stop_price(current)
            if remote is not None and remote >= new_stop_price and not _is_dead(current):
                return old_stop_id
            raise LiveExecutionError(
                f"édition du stop {old_stop_id} ambiguë : "
                f"{type(exc).__name__}: {exc} — aucun second stop créé"
            ) from exc

        current = await self._read_order(old_stop_id, symbol)
        remote = self._stop_price(current)
        if _is_dead(current) or remote is None or remote < new_stop_price:
            raise LiveExecutionError(
                f"édition du stop {old_stop_id} non confirmée "
                f"(stop distant {remote}, attendu ≥ {new_stop_price})"
            )
        return old_stop_id

    async def update_stop_loss_order(self, symbol: str, amount: Decimal,
                                     old_stop_id: str,
                                     new_stop_price: Decimal) -> str:
        """Remplace un stop sans jamais laisser la position nue.

        Ordre des opérations :
        1. relire l'ancien stop et refuser s'il est terminé ou si le nouveau
           niveau est plus bas (un trailing ne desserre pas la protection) ;
        2. tenter ``edit_order`` natif s'il est réellement supporté ;
        3. sinon créer le nouveau stop, vérifier qu'il est visible et couvre la
           quantité, **puis seulement** annuler l'ancien ;
        4. si l'annulation de l'ancien échoue, annuler le nouveau et lever : une
           erreur explicite vaut mieux qu'un double stop silencieux.

        Retourne l'identifiant du stop qui protège désormais la position.
        """
        if not old_stop_id:
            raise LiveExecutionError("identifiant de l'ancien stop manquant")
        if amount <= 0 or not amount.is_finite():
            raise LiveExecutionError(f"quantité de stop invalide : {amount}")
        if new_stop_price <= 0 or not new_stop_price.is_finite():
            raise LiveExecutionError(f"nouveau stop invalide : {new_stop_price}")

        old = await self._read_order(old_stop_id, symbol)
        if _is_dead(old) or _is_final(old):
            raise LiveExecutionError(
                f"ancien stop {old_stop_id} déjà terminé ({_status(old)}) : "
                "ne pas créer une protection sur une position peut-être soldée"
            )
        old_price = self._stop_price(old)
        if old_price is None:
            raise LiveExecutionError(
                f"ancien stop {old_stop_id} sans prix lisible : remplacement refusé"
            )
        if new_stop_price < old_price:
            raise LiveExecutionError(
                f"le nouveau stop {new_stop_price} est sous l'ancien {old_price} : "
                "un trailing ne desserre jamais la protection"
            )
        if new_stop_price == old_price:
            return old_stop_id

        edited = await self._edit_stop(symbol, amount, old_stop_id, new_stop_price)
        if edited is not None:
            return edited

        new_stop_id = await self._rearm_stop(symbol, amount, new_stop_price)
        if not new_stop_id:
            raise LiveExecutionError(
                f"nouveau stop créé sans identifiant pour {symbol} : "
                "l'ancien stop est conservé"
            )
        try:
            visible = await self._wait_for_stop(symbol, amount, new_stop_price)
            if str(visible.get("id") or "") != new_stop_id:
                raise LiveExecutionError(
                    f"le stop visible {visible.get('id')!r} n'est pas le nouveau "
                    f"{new_stop_id!r} : remplacement ambigu"
                )
            checked = await self._read_order(new_stop_id, symbol)
            if _is_dead(checked) or _is_final(checked):
                raise LiveExecutionError(
                    f"nouveau stop {new_stop_id} terminé avant l'annulation de l'ancien"
                )
        except LiveExecutionError:
            await self._cancel_quietly(new_stop_id, symbol)
            raise

        try:
            await self._call(self._exchange.cancel_order, old_stop_id, symbol)
        except Exception as exc:  # noqa: BLE001 — double stop : nettoyer le nouveau
            await self._cancel_quietly(new_stop_id, symbol)
            raise LiveExecutionError(
                f"ancien stop {old_stop_id} non annulé : {type(exc).__name__}: {exc} — "
                f"le nouveau stop {new_stop_id} a été annulé ; vérifier l'état "
                "de la protection chez l'exchange"
            ) from exc

        # Dernière lecture : si l'ancien est toujours ouvert, on préfère annuler
        # le nouveau et signaler un état à vérifier plutôt que deux stops actifs.
        try:
            still_open = await self._read_order(old_stop_id, symbol)
        except LiveExecutionError:
            return new_stop_id
        if not _is_dead(still_open) and not _is_final(still_open):
            await self._cancel_quietly(new_stop_id, symbol)
            raise LiveExecutionError(
                f"ancien stop {old_stop_id} toujours ouvert après annulation : "
                f"le nouveau stop {new_stop_id} a été annulé"
            )
        return new_stop_id

    async def exit(self, amount: Decimal, market_price: Decimal, *,
                   symbol: str, stop_price: Decimal | None = None) -> Fill:
        """Annule les protections, vend au marché, et réarme si la vente échoue."""
        await self.cancel_open_orders(symbol=symbol)
        formatted_amount = to_exchange_amount(self._exchange, symbol, amount)
        try:
            order = await self._call(
                self._exchange.create_order, symbol, "market", "sell",
                formatted_amount, None, {},
            )
            if not isinstance(order, dict):
                raise LiveExecutionError("réponse de vente illisible")
            filled = await self._wait_for_fill(order, symbol)
        except LiveExecutionError:
            if stop_price is not None:
                await self._rearm_stop(symbol, amount, stop_price)
            raise
        except Exception as exc:  # noqa: BLE001 — la vente n'est pas partie
            if stop_price is not None:
                await self._rearm_stop(symbol, amount, stop_price)
            raise LiveExecutionError(
                f"vente refusée par l'exchange : {type(exc).__name__}: {exc}"
            ) from exc
        return self._fill_from_order(filled, market_price, "sell")

    async def emergency_exit(self, amount: Decimal, *, symbol: str,
                             stop_price: Decimal | None = None,
                             market_price: Decimal | None = None) -> Fill:
        """Liquidation d'urgence : annule tout, vend, et ne réarme rien."""
        await self.cancel_open_orders(symbol=symbol)
        formatted_amount = to_exchange_amount(self._exchange, symbol, amount)
        try:
            order = await self._call(
                self._exchange.create_order, symbol, "market", "sell",
                formatted_amount, None, {},
            )
            if not isinstance(order, dict):
                raise LiveExecutionError("réponse de liquidation illisible")
            filled = await self._wait_for_fill(order, symbol)
        except Exception as exc:  # noqa: BLE001 — une urgence ne doit pas masquer l'échec
            if stop_price is not None:
                await self._rearm_stop(symbol, amount, stop_price)
            if isinstance(exc, LiveExecutionError):
                raise
            raise LiveExecutionError(
                f"liquidation refusée par l'exchange : {type(exc).__name__}: {exc}"
            ) from exc
        fill = self._fill_from_order(
            filled, market_price if market_price is not None else Decimal("0"), "sell"
        )
        if market_price is None:
            return replace(fill, slippage_quote=Decimal(0))
        return fill

    # -- lecture --------------------------------------------------------

    async def position_state(self, symbol: str, amount: Decimal,
                             stop_price: Decimal) -> RemotePositionState:
        """Lit le solde de base et le stop **avant** toute décision locale.

        C'est la barrière contre la double vente : si le stop exchange a déjà
        déclenché, le solde total est nul et la boucle doit enregistrer la sortie,
        pas envoyer une seconde vente. On lit le total, pas le free : un stop
        armé peut réserver le solde disponible sans que la position ait disparu.
        """
        base = await self.total_base(symbol)
        stop = await self._find_stop(symbol, amount, stop_price)
        return RemotePositionState(
            symbol=symbol, base_total=base, stop_ok=stop is not None,
            stop_order_id=str((stop or {}).get("id") or "") or None,
        )

    async def last_sell_fill(self, symbol: str, *, since: int | None = None,
                             limit: int = 20) -> Fill | None:
        """Dernière vente exécutée sur le symbole, lue dans l'historique réel.

        Sert quand le stop exchange a soldé la position pendant que le bot
        dormait : la base est plate, il faut retrouver le prix et les frais
        réels pour clore le ledger sans renvoyer d'ordre.
        """
        try:
            trades = await self._call(
                self._exchange.fetch_my_trades, symbol, since, limit
            )
        except Exception as exc:  # noqa: BLE001 — sans historique, on ne clôt pas
            raise LiveExecutionError(
                f"historique des trades illisible pour {symbol} : "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        sells = [
            trade for trade in (trades or [])
            if str(trade.get("side") or "").lower() == "sell"
            and _decimal(trade.get("amount")) > 0
            and (since is None or trade.get("timestamp") is None
                 or int(trade.get("timestamp")) >= since)
        ]
        if not sells:
            return None
        trade = max(sells, key=lambda item: item.get("timestamp") or 0)
        amount = _decimal(trade.get("amount"))
        price = _decimal(trade.get("price") or trade.get("average"))
        if price <= 0:
            raise LiveExecutionError(f"vente {trade.get('id')} sans prix exploitable")
        notional = amount * price
        return Fill(
            price=price, amount=amount, notional_quote=notional,
            fee_quote=self._fee_from_order(trade, notional),
            slippage_quote=Decimal(0),
            order_id=str(trade.get("order") or trade.get("id") or "") or None,
        )

    async def balance(self) -> dict[str, dict[str, Decimal]]:
        raw = await self._call(self._exchange.fetch_balance)
        free = raw.get("free") or {}
        total = raw.get("total") or {}
        currencies = set(free) | set(total)
        out: dict[str, dict[str, Decimal]] = {}
        for currency in currencies:
            out[str(currency)] = {
                "free": _decimal(free.get(currency)),
                "total": _decimal(total.get(currency)),
            }
        return out

    async def free_quote(self) -> Decimal:
        balances = await self.balance()
        return balances.get(self._quote, {}).get("free", Decimal(0))

    async def free_base(self, symbol: str) -> Decimal:
        base = symbol.split("/")[0]
        balances = await self.balance()
        return balances.get(base, {}).get("free", Decimal(0))

    async def total_base(self, symbol: str) -> Decimal:
        """Solde total de base : un stop armé peut réserver le solde « free »."""
        base = symbol.split("/")[0]
        balances = await self.balance()
        return balances.get(base, {}).get("total", Decimal(0))
