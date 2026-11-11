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
    "progressif": "Petit budget, perte prévue frais compris, gains laissés au stop suiveur. Peut rester en euros.",
    "prudent": "Peu d'achats, petite part du capital, arrêt rapide.",
    "standard": "Un peu plus d'opportunités, risque modéré.",
    "aggressive": "Plus d'opportunités, variations plus fortes.",
    "micro": "Pour un très petit compte : le minimum de l'exchange impose des tickets plus gros.",
    "petit_budget": "Repère 20–100 EUR : risque 1,5 %, exposition 50 %. Minima et frais peuvent refuser l'achat.",
    "micro_budget": "Repère 8–20 EUR : risque 3 %, exposition 50 %. Objectif 3,5 R, sans garantie de l'atteindre.",
    "nano_budget": "Sous 8 EUR : risque 5 %, exposition jusqu'à 100 % sous 50 EUR. Concentration élevée ; un minimum trop cher reste refusé.",
    "swing": "Stratégie Daily avec stop structurel.",
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
