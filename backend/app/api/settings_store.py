"""Réglages pilotables depuis le dashboard, stockés en base.

Le dashboard écrit dans `runtime_settings` (une ligne par mode) ; le bot relit cette
ligne à chaque cycle et applique les clés autorisées. Le fichier `.env` reste la
base de départ, la base devient la source de vérité des réglages modifiés.

Les valeurs restent des fractions (`0.50` = 50 %) côté API ; le dashboard les
affiche en pourcentage et les convertit.
"""

from __future__ import annotations

from decimal import Decimal

from app.core.settings import RUNTIME_ALLOWED_KEYS, RISK_PRESETS, Settings, load_settings

__all__ = [
    "PROFILE_LABELS",
    "SettingsRefused",
    "build_payload",
    "friendly_settings",
    "validate_payload",
]

PROFILE_LABELS = {
    "progressif": "Progressif (simulation)",
    "prudent": "Prudent",
    "standard": "Équilibré",
    "aggressive": "Audacieux",
    "micro": "Micro",
    "petit_budget": "Petit budget (simulation)",
    "micro_budget": "Micro budget (simulation)",
    "nano_budget": "Nano budget (simulation, exposition élevée)",
    "swing": "Swing Daily",
}

PROFILE_DESCRIPTIONS = {
    "progressif": "Simulation avec une partie du capital mise de côté après les gains. Le bot peut garder les euros sans acheter.",
    "prudent": "Engage une petite part du compte et accepte une faible perte prévue par achat. Avec quelques euros, les achats peuvent rester impossibles.",
    "standard": "Accepte davantage d'argent engagé et une perte prévue plus élevée que Prudent. Aucun gain garanti.",
    "aggressive": "Accepte des pertes plus fortes que Prudent. Ce nom ne signifie pas que le bot gagne davantage.",
    "micro": "Ancien profil conservé pour compatibilité. Ses limites diffèrent du nouveau Micro budget et peuvent refuser les petits ordres.",
    "petit_budget": "Repère : 20 à 100 €. Jusqu'à la moitié du capital engagée et 1,5 % de perte prévue par achat. Simulation uniquement.",
    "micro_budget": "Repère : 8 à 20 €, notamment 9,37 €. Jusqu'à la moitié du capital engagée et 3 % de perte prévue par achat. Simulation uniquement.",
    "nano_budget": "Repère : moins de 8 €. Peut engager presque tout le compte, jusqu'à 100 % sous 50 €, avec 5 % de perte prévue par achat. Concentration élevée. Simulation uniquement.",
    "swing": "Cherche des mouvements sur plusieurs jours avec des bougies quotidiennes. Peut mobiliser tout un petit compte ; la protection peut être dépassée lors d'une forte chute.",
}


class SettingsRefused(ValueError):
    """Réglage refusé : valeur hors bornes ou configuration incohérente."""


def friendly_settings(config: Settings, *, updated_at: str | None = None) -> dict:
    """Vue lisible par le dashboard : labels, valeurs effectives, profils."""
    return {
        "profile": config.risk_profile,
        "profile_label": PROFILE_LABELS.get(config.risk_profile, config.risk_profile),
        "max_per_trade_pct": str(config.risk.max_notional_pct),
        "max_daily_loss_pct": str(config.risk.max_daily_drawdown_pct),
        "max_weekly_loss_pct": str(config.risk.max_weekly_drawdown_pct),
        "max_drawdown_from_hwm_pct": str(config.risk.max_drawdown_from_hwm_pct),
        "max_consecutive_losses": config.risk.max_consecutive_losses,
        "risk_per_trade_pct": str(config.risk.risk_per_trade_pct),
        "cost_aware": config.risk.cost_aware,
        "small_account_full_allocation": config.risk.small_account_full_allocation,
        "small_account_threshold_quote": str(config.risk.small_account_threshold_quote),
        "max_orders_per_hour": config.risk.max_orders_per_hour,
        "confidence_floor": str(config.risk.min_confidence),
        "entry_rule_enabled": config.risk.entry_rule_enabled,
        "halt_auto_resume_hours": config.halt_auto_resume_hours,
        "updated_at": updated_at,
        "profiles": [
            {
                "key": key,
                "label": PROFILE_LABELS.get(key, key),
                "description": PROFILE_DESCRIPTIONS.get(key, ""),
                "max_per_trade_pct": RISK_PRESETS[key]["MAX_NOTIONAL_PCT"],
                "max_daily_loss_pct": RISK_PRESETS[key]["MAX_DAILY_DRAWDOWN_PCT"],
                "max_orders_per_hour": int(RISK_PRESETS[key]["MAX_ORDERS_PER_HOUR"]),
                "confidence_floor": RISK_PRESETS[key]["MIN_CONFIDENCE"],
                "risk_per_trade_pct": RISK_PRESETS[key]["RISK_PER_TRADE_PCT"],
                "cost_aware": RISK_PRESETS[key].get('COST_AWARE', 'false') == 'true',
                "small_account_full_allocation": RISK_PRESETS[key].get('SMALL_ACCOUNT_FULL_ALLOCATION', 'false') == 'true',
                "small_account_threshold_quote": '50',
            }
            for key in RISK_PRESETS
        ],
    }


def _as_text(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _mapping(update: dict) -> dict[str, str]:
    """Traduit les noms du dashboard vers les variables de configuration."""
    mapping: dict[str, str] = {}
    if update.get("profile") is not None:
        mapping["RISK_PROFILE"] = str(update["profile"])
    if update.get("max_per_trade_pct") is not None:
        mapping["MAX_NOTIONAL_PCT"] = _as_text(update["max_per_trade_pct"])
    if update.get("max_daily_loss_pct") is not None:
        mapping["MAX_DAILY_DRAWDOWN_PCT"] = _as_text(update["max_daily_loss_pct"])
    if update.get("max_orders_per_hour") is not None:
        mapping["MAX_ORDERS_PER_HOUR"] = _as_text(update["max_orders_per_hour"])
    if update.get("confidence_floor") is not None:
        mapping["MIN_CONFIDENCE"] = _as_text(update["confidence_floor"])
    if update.get("entry_rule_enabled") is not None:
        mapping["ENTRY_RULE_ENABLED"] = _as_text(update["entry_rule_enabled"])
    if update.get("halt_auto_resume_hours") is not None:
        mapping["HALT_AUTO_RESUME_HOURS"] = _as_text(update["halt_auto_resume_hours"])
    return mapping


def build_payload(current: dict, update: dict) -> dict:
    """Fusionne le payload existant et la modification du dashboard.

    Seules les clés de la liste blanche sont conservées : un payload ne doit pas
    pouvoir porter `TRADING_MODE` ou une clé d'exchange.
    """
    payload = {
        key: _as_text(value)
        for key, value in current.items()
        if key in RUNTIME_ALLOWED_KEYS
    }
    if update.get('profile') is not None and str(update['profile'])!=payload.get('RISK_PROFILE'):
        for key in RUNTIME_ALLOWED_KEYS - {'HALT_AUTO_RESUME_HOURS'}:
            payload.pop(key,None)
        # Matérialise les limites du profil pour écraser aussi un ancien override
        # de démarrage (ex. risque prudent à 0,75 % conservé dans l'environnement).
        payload.update({k: v for k, v in RISK_PRESETS.get(str(update['profile']), {}).items()
                        if k in RUNTIME_ALLOWED_KEYS})
    payload.update(_mapping(update))
    return payload


def validate_payload(payload: dict, *, loader=load_settings) -> Settings:
    """Construit la configuration effective et refuse une combinaison dangereuse."""
    try:
        candidate = loader(overrides={
            key: str(value)
            for key, value in payload.items()
            if key in RUNTIME_ALLOWED_KEYS
        })
    except (RuntimeError, ValueError) as exc:
        raise SettingsRefused(str(exc)) from exc

    # Une seule perte ne doit pas fermer la journée : le seuil journalier reste
    # au moins le double du risque par achat.
    if candidate.risk.max_daily_drawdown_pct < 2 * candidate.risk.risk_per_trade_pct:
        raise SettingsRefused(
            "La perte maximale sur une journée doit rester au moins le double du "
            "risque par achat. Choisis une prudence plus basse ou augmente la perte "
            "journalière."
        )
    return candidate
