"""Mesures rétrospectives du laboratoire macro, jamais utilisées pour acheter."""
from datetime import timedelta
from decimal import Decimal


def bull_traps(trades, states):
    """Cohorte : prix de décision > EMA50 et < EMA200, sans marge ajoutée."""
    cohort = [t for t in trades if (
        states[t.entry_at].snapshot.ema_50_h4
        < states[t.entry_at].snapshot.price
        < states[t.entry_at].snapshot.ema_200_h4
    )]
    immediate = [t for t in cohort if t.reason == "STOP_LOSS"
                 and t.exit_at - t.entry_at == timedelta(hours=4)]
    return {
        "under_ema200_trades": len(cohort),
        "immediate_stops": len(immediate),
        "bull_trap_rate": str(Decimal(len(immediate)) / Decimal(len(cohort))) if cohort else None,
    }


def entry_lag(trades, reference, states, h4):
    """Appariement un-à-un de gains, 30 j maximum, sans invalidation du stop.

    Une reprise est candidate si l'achat anticipé gagnant est sous EMA200 et
    au-dessus d'EMA50. On cherche le premier achat EMA200 ultérieur (gagnant),
    au plus 30 jours après, sans retour au stop initial entre les deux entrées.
    C'est une sélection rétrospective de reprises réussies, pas une estimation
    causale du bénéfice d'un changement de filtre. Les non-appariés sont comptés.
    """
    candidates = [t for t in trades if t.pnl_quote > 0 and (
        states[t.entry_at].snapshot.ema_50_h4
        < states[t.entry_at].snapshot.price
        < states[t.entry_at].snapshot.ema_200_h4
    )]
    used = set()
    pairs = []
    for early in candidates:
        for late in reference:
            if late.entry_at in used or late.entry_at <= early.entry_at:
                continue
            if late.entry_at - early.entry_at > timedelta(days=30):
                break
            # Ne pas sauter une première entrée de référence perdante pour
            # sélectionner opportunément un gain plus tardif.
            if late.pnl_quote <= 0:
                break
            if any(early.entry_at.timestamp()*1000 <= c[0]
                   < late.entry_at.timestamp()*1000
                   and Decimal(str(c[3])) <= early.stop_loss_price for c in h4):
                break
            used.add(late.entry_at)
            pairs.append({
                "early_at": early.entry_at.isoformat(), "reference_at": late.entry_at.isoformat(),
                "price_gap_eur": str(late.entry_price - early.entry_price),
                "price_gap_pct": str(late.entry_price / early.entry_price - 1),
                "hours": str(Decimal(str((late.entry_at-early.entry_at).total_seconds())) / 3600),
            })
            break
    def mean(key):
        return str(sum((Decimal(p[key]) for p in pairs), Decimal(0)) / len(pairs)) if pairs else None
    return {"lag_candidates": len(candidates), "lag_pairs": len(pairs),
            "lag_unmatched": len(candidates)-len(pairs),
            "lag_price_eur": mean("price_gap_eur"), "lag_price_pct": mean("price_gap_pct"),
            "lag_hours": mean("hours"), "lag_matches": pairs}
