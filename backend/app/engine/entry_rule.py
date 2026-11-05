"""Filtre d'entrée déterministe — le cadre dans lequel le LLM peut acheter.

Le pare-feu dit si un achat est **sûr**. Ce module dit si un achat est **opportun**.
Il ne remplace ni le pare-feu ni le modèle : il réduit les instants où le LLM est
consulté à ceux qui ont la structure d'un repli dans une tendance haussière.

Pourquoi ce filtre existe : le modèle plafonne autour de 0,72 de confiance sur les
BUY. Consulté dans n'importe quelle zone, il produit beaucoup de HOLD et quelques
BUY perdants. Consulté uniquement quand la structure est propre, chaque décision
coûte moins cher et les statistiques deviennent lisibles.

Deux modes, mesurés séparément (D-058) :

- ``EMA50_H4`` : repli sur l'EMA50 H4, tendance confirmée par la pente de l'EMA200.
  Le setup historique ; il produit 2 à 9 trades par paire et par an sur 24 mois.
- ``PULLBACK_H1`` : repli plus fin — la dernière H1 touche l'EMA50 H1 puis referme
  au-dessus, dans une structure H4 haussière. Même pare-feu, même sizing : seul
  l'instant d'interrogation change.

Le mode est une configuration, jamais une décision du modèle.

Le module est PUR : aucune I/O, aucune horloge implicite, aucun appel réseau.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.engine.risk_gate import EntryMode, MarketSnapshot, RiskConfig
from app.market.feed import MarketContext

__all__ = ["EntrySetup", "evaluate_entry"]

SETUP_H4 = "PULLBACK"
SETUP_H1 = "PULLBACK_H1"
SETUP_SWING = "SWING_MACRO_D1"


@dataclass(frozen=True, slots=True)
class EntrySetup:
    """Verdict lisible sur la structure de marché au moment T.

    ``rsi`` et ``distance_to_ema50_atr`` portent les valeurs du mode évalué : RSI et
    écart à l'EMA50 H4 en mode ``EMA50_H4``, RSI et écart à l'EMA50 H1 en mode
    ``PULLBACK_H1``. Les deux modes ne se mélangent pas dans une même mesure.
    """

    triggered: bool
    reason: str
    setup: str  # "PULLBACK" | "PULLBACK_H1" | "NONE"
    rsi: Decimal
    distance_to_ema50_atr: Decimal
    atr_pct: Decimal


def _verdict(triggered: bool, reason: str, rsi: Decimal,
             distance: Decimal, atr_pct: Decimal,
             setup: str = SETUP_H4) -> EntrySetup:
    return EntrySetup(
        triggered=triggered,
        reason=reason,
        setup=setup if triggered else "NONE",
        rsi=rsi,
        distance_to_ema50_atr=distance,
        atr_pct=atr_pct,
    )


def _pullback_h4(snapshot: MarketSnapshot, context: MarketContext,
                 config: RiskConfig) -> EntrySetup:
    """Setup historique : repli sur l'EMA50 H4 dans une tendance haussière."""
    price = snapshot.price
    ema_50 = context.ema_50_h4
    atr = snapshot.atr_14_h4
    rsi = context.rsi_14_h4

    if not ema_50.is_finite():
        return _verdict(False, "EMA50 H4 non finie", rsi, Decimal(0), atr / price)
    if ema_50 <= 0:
        return _verdict(False, "EMA50 H4 non strictement positive", rsi, Decimal(0), atr / price)

    distance = (price - ema_50) / atr

    if config.entry_require_ema50_above_ema200 and ema_50 <= snapshot.ema_200_h4:
        return _verdict(
            False,
            f"EMA50 {ema_50:.4f} sous EMA200 {snapshot.ema_200_h4:.4f} : structure baissière",
            rsi, distance, atr / price,
        )

    if rsi < config.entry_rsi_min:
        return _verdict(
            False,
            f"RSI {rsi:.2f} < {config.entry_rsi_min} : momentum trop faible",
            rsi, distance, atr / price,
        )
    if rsi > config.entry_rsi_max:
        return _verdict(
            False,
            f"RSI {rsi:.2f} > {config.entry_rsi_max} : surchauffe",
            rsi, distance, atr / price,
        )

    if config.entry_require_positive_h1 and snapshot.last_closed_h1_return_atr <= 0:
        return _verdict(
            False,
            f"dernière H1 {snapshot.last_closed_h1_return_atr:+.2f} ATR ≤ 0 : "
            "pas de rebond confirmé",
            rsi, distance, atr / price,
        )

    if distance > config.entry_pullback_atr:
        return _verdict(
            False,
            f"prix {distance:.2f} ATR au-dessus de l'EMA50 (max {config.entry_pullback_atr})",
            rsi, distance, atr / price,
        )
    if distance < -config.entry_pullback_atr:
        return _verdict(
            False,
            f"prix {distance:.2f} ATR sous l'EMA50 (max -{config.entry_pullback_atr})",
            rsi, distance, atr / price,
        )

    return _verdict(
        True,
        f"pullback EMA50 · RSI {rsi:.2f} · distance {distance:+.2f} ATR",
        rsi, distance, atr / price,
    )


def _pullback_h1(snapshot: MarketSnapshot, context: MarketContext,
                 config: RiskConfig) -> EntrySetup:
    """Repli fin : l'EMA50 H1 est touchée, puis refermée par le haut.

    La zone de valeur est l'EMA50 H1. Trois conditions, dans cet ordre :

    1. le bas de la dernière H1 clôturée a atteint ou percé l'EMA50 H1 — le repli
       a bien eu lieu, on n'achète pas une extension ;
    2. la clôture (H4, identique à la clôture H1 à cet instant) est repassée
       au-dessus de l'EMA50 H1 — le repli est refermé, pas un couteau qui tombe ;
    3. l'écart à l'EMA50 H1 reste sous ``entry_pullback_h1_atr`` et le RSI H1 est
       dans la bande de repli — la remontée n'est pas déjà une extension.
    """
    price = snapshot.price
    ema_50 = context.ema_50_h1
    atr_h1 = context.atr_14_h1
    rsi = context.rsi_14_h1
    low = context.last_closed_h1_low
    atr_pct = snapshot.atr_14_h4 / price
    distance_h4 = (price - context.ema_50_h4) / snapshot.atr_14_h4

    if not all(v.is_finite() for v in (ema_50, atr_h1, rsi, low)):
        return _verdict(
            False, "contexte H1 non fini (EMA50, ATR, RSI ou bas)",
            rsi, distance_h4, atr_pct, SETUP_H1,
        )
    if ema_50 <= 0 or atr_h1 <= 0 or low <= 0:
        return _verdict(
            False, "EMA50/ATR/bas H1 indisponibles : repli non évaluable",
            rsi, distance_h4, atr_pct, SETUP_H1,
        )

    if low > ema_50:
        return _verdict(
            False,
            f"dernière H1 n'a pas testé l'EMA50 H1 (bas {low:.4f} > {ema_50:.4f})",
            rsi, distance_h4, atr_pct, SETUP_H1,
        )
    if price <= ema_50:
        return _verdict(
            False,
            f"clôture {price:.4f} sous l'EMA50 H1 {ema_50:.4f} : repli non refermé",
            rsi, distance_h4, atr_pct, SETUP_H1,
        )

    extension = (price - ema_50) / atr_h1
    if extension > config.entry_pullback_h1_atr:
        return _verdict(
            False,
            f"prix {extension:.2f} ATR H1 au-dessus de l'EMA50 après le repli "
            f"(max {config.entry_pullback_h1_atr})",
            rsi, extension, atr_pct, SETUP_H1,
        )

    if rsi < config.entry_h1_rsi_min:
        return _verdict(
            False,
            f"RSI H1 {rsi:.2f} < {config.entry_h1_rsi_min} : momentum trop faible",
            rsi, extension, atr_pct, SETUP_H1,
        )
    if rsi > config.entry_h1_rsi_max:
        return _verdict(
            False,
            f"RSI H1 {rsi:.2f} > {config.entry_h1_rsi_max} : remontée déjà étendue",
            rsi, extension, atr_pct, SETUP_H1,
        )

    return _verdict(
        True,
        f"pullback H1 · bas {low:.4f} → clôture {price:.4f} au-dessus de l'EMA50 "
        f"{ema_50:.4f} · RSI H1 {rsi:.2f} · extension {extension:+.2f} ATR H1",
        rsi, extension, atr_pct, SETUP_H1,
    )


def _swing_macro_d1(snapshot: MarketSnapshot, context: MarketContext,
                    config: RiskConfig) -> EntrySetup:
    """Tendance de fond D1 : cassure de compression ou rebond de valeur.

    Deux déclencheurs, en OU :

    - **Donchian 20** : la clôture dépasse le plus haut des 20 D1 précédentes ;
    - **rebond de valeur** : la D1 précédente a testé l'EMA20, la clôture repasse
      au-dessus et le RSI(14) reste dans 50–65 (momentum sain, hors surachat).

    Le filtre primaire est le même dans les deux cas : clôture au-dessus de
    l'EMA50 **et** de l'EMA200 D1, pente de l'EMA50 positive sur 5 jours.
    """
    d1 = context.d1
    price = snapshot.price
    if d1 is None:
        return _verdict(
            False, "contexte D1 indisponible : setup swing non évaluable",
            Decimal(0), Decimal(0), Decimal(0), SETUP_SWING,
        )
    values = (
        price, d1.ema_20, d1.ema_50, d1.ema_200, d1.ema_50_slope_5d_pct,
        d1.rsi_14, d1.atr_14, d1.donchian_high_20,
    )
    if not all(v.is_finite() for v in values):
        return _verdict(
            False, "valeurs D1 non finies (NaN ou infini)",
            d1.rsi_14, Decimal(0), Decimal(0), SETUP_SWING,
        )
    if price <= 0 or d1.ema_50 <= 0 or d1.ema_200 <= 0 or d1.atr_14 <= 0:
        return _verdict(
            False, "prix/EMA/ATR D1 non strictement positifs",
            d1.rsi_14, Decimal(0), Decimal(0), SETUP_SWING,
        )

    atr_pct = d1.atr_14 / price
    distance = (price - d1.ema_50) / d1.atr_14

    if price <= d1.ema_50:
        return _verdict(
            False,
            f"clôture {price:.4f} sous l'EMA50 D1 {d1.ema_50:.4f} : tendance primaire absente",
            d1.rsi_14, distance, atr_pct, SETUP_SWING,
        )
    if price <= d1.ema_200:
        return _verdict(
            False,
            f"clôture {price:.4f} sous l'EMA200 D1 {d1.ema_200:.4f} : tendance de fond absente",
            d1.rsi_14, distance, atr_pct, SETUP_SWING,
        )
    if d1.ema_50_slope_5d_pct <= 0:
        return _verdict(
            False,
            f"pente EMA50 D1 {d1.ema_50_slope_5d_pct:+.3%} sur 5 jours ≤ 0",
            d1.rsi_14, distance, atr_pct, SETUP_SWING,
        )

    if atr_pct < config.min_atr_pct_of_price:
        return _verdict(
            False,
            f"ATR/prix D1 {atr_pct:.4%} < {config.min_atr_pct_of_price:.2%} : marché atone",
            d1.rsi_14, distance, atr_pct, SETUP_SWING,
        )
    if atr_pct > config.max_atr_pct_of_price:
        return _verdict(
            False,
            f"ATR/prix D1 {atr_pct:.4%} > {config.max_atr_pct_of_price:.2%} : volatilité anormale",
            d1.rsi_14, distance, atr_pct, SETUP_SWING,
        )

    day_return_atr = (d1.last_close - d1.last_open) / d1.atr_14
    if day_return_atr <= -config.falling_knife_atr_multiple:
        return _verdict(
            False,
            f"dernière D1 {day_return_atr:+.2f} ATR : couteau qui tombe",
            d1.rsi_14, distance, atr_pct, SETUP_SWING,
        )

    breakout = d1.donchian_high_20 > 0 and price > d1.donchian_high_20
    rebound = (
        d1.prev_low <= d1.ema_20_prev
        and price > d1.ema_20
        and price > d1.prev_close
        and config.swing_rsi_min <= d1.rsi_14 <= config.swing_rsi_max
    )

    triggers: list[str] = []
    if config.swing_require_donchian and breakout:
        triggers.append(f"cassure Donchian {config.swing_donchian_days} ({d1.donchian_high_20:.4f})")
    if config.swing_require_value_rebound and rebound:
        triggers.append(f"rebond EMA20 D1 (RSI {d1.rsi_14:.2f})")

    if not triggers:
        if not config.swing_require_donchian and not config.swing_require_value_rebound:
            reason = "aucun déclencheur swing activé"
        elif breakout and not config.swing_require_donchian:
            reason = "cassure présente mais déclencheur Donchian désactivé"
        elif rebound and not config.swing_require_value_rebound:
            reason = "rebond présent mais déclencheur EMA20 désactivé"
        else:
            reason = (
                f"aucun déclencheur : clôture {price:.4f} ≤ Donchian "
                f"{d1.donchian_high_20:.4f}, pas de rebond EMA20 refermé "
                f"(RSI {d1.rsi_14:.2f})"
            )
        return _verdict(False, reason, d1.rsi_14, distance, atr_pct, SETUP_SWING)

    return _verdict(
        True,
        f"swing D1 · {' + '.join(triggers)} · pente EMA50 "
        f"{d1.ema_50_slope_5d_pct:+.3%} · distance {distance:+.2f} ATR D1",
        d1.rsi_14, distance, atr_pct, SETUP_SWING,
    )


def evaluate_entry(
    snapshot: MarketSnapshot,
    context: MarketContext,
    config: RiskConfig,
) -> EntrySetup:
    """Répond à : « la structure permet-elle un achat, et pourquoi ? ».

    Le filtre est volontairement plus strict que le pare-feu. Le pare-feu acceptera
    le BUY ensuite ; ce module ne fait que décider s'il vaut la peine d'appeler le
    modèle. Un refus ici n'est pas une décision de risque, c'est une absence de
    setup.

    Les garde-fous communs (structure H4, pente de l'EMA200, bande de volatilité,
    couteau qui tombe) passent avant le mode : le mode ne choisit que la finesse du
    repli, jamais la direction ni le risque.
    """
    price = snapshot.price
    ema_200 = snapshot.ema_200_h4
    atr = snapshot.atr_14_h4
    rsi = context.rsi_14_h4

    if not all(v.is_finite() for v in (price, ema_200, atr, rsi)):
        return _verdict(False, "valeurs non finies (NaN ou infini)", rsi, Decimal(0), Decimal(0))
    if price <= 0 or ema_200 <= 0 or atr <= 0:
        return _verdict(False, "prix/EMA/ATR non strictement positifs", rsi, Decimal(0), Decimal(0))

    atr_pct = atr / price

    trend_floor = ema_200 * (Decimal(1) + config.trend_buffer_pct)
    if price <= trend_floor:
        return _verdict(
            False,
            f"prix {price:.4f} sous EMA200 + marge ({trend_floor:.4f})",
            rsi, (price - context.ema_50_h4) / atr, atr_pct,
        )

    if snapshot.ema_200_slope_atr < config.entry_ema200_slope_min:
        return _verdict(
            False,
            f"EMA200 monte de {snapshot.ema_200_slope_atr:+.2f} ATR sur 10 jours "
            f"(minimum {config.entry_ema200_slope_min:.2f}) : tendance pas assez haussière",
            rsi, (price - context.ema_50_h4) / atr, atr_pct,
        )

    if atr_pct < config.min_atr_pct_of_price:
        return _verdict(
            False,
            f"ATR/prix {atr_pct:.4%} < {config.min_atr_pct_of_price:.2%} : marché atone",
            rsi, (price - context.ema_50_h4) / atr, atr_pct,
        )
    if atr_pct > config.max_atr_pct_of_price:
        return _verdict(
            False,
            f"ATR/prix {atr_pct:.4%} > {config.max_atr_pct_of_price:.2%} : volatilité anormale",
            rsi, (price - context.ema_50_h4) / atr, atr_pct,
        )

    if snapshot.last_closed_h1_return_atr <= -config.falling_knife_atr_multiple:
        return _verdict(
            False,
            f"dernière H1 {snapshot.last_closed_h1_return_atr} ATR : couteau qui tombe",
            rsi, (price - context.ema_50_h4) / atr, atr_pct,
        )

    if config.entry_mode is EntryMode.SWING_MACRO_D1:
        return _swing_macro_d1(snapshot, context, config)
    if config.entry_mode == EntryMode.PULLBACK_H1:
        return _pullback_h1(snapshot, context, config)
    return _pullback_h4(snapshot, context, config)
