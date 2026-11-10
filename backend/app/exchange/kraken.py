"""Passerelle Kraken spot — préparation de l'étape 1.6.

Ce module ne remplace rien : aucun exécuteur n'existait avant lui. Il pose les
trois morceaux qui doivent être faux-proofs avant qu'un ordre puisse partir :

1. **Lire l'exchange** : marchés unifiés, limites et précisions → `ExchangeConstraints`
   en `Decimal`, jamais en flottant.
2. **Construire l'ordre bracket** : achat marché + stop-loss conditionnel attaché
   (`close`), dont le prix est formaté sans jamais descendre sous le stop validé.
3. **Vérifier la clé** : interroger `GetApiKeyInfo` et refuser si `withdraw` apparaît.

Règles tenues par construction :

- Aucune fonction d'envoi n'existe ici. `build_bracket_entry()` retourne les arguments
  d'un `create_order` ; l'exécuteur qui appellera CCXT devra recevoir l'objet
  `RiskDecision`, pas des nombres libres — impossible de contourner le pare-feu.
- CCXT ne voit que des chaînes : les `Decimal` restent côté application.
- Le bracket Kraken ne porte qu'**une** protection conditionnelle. Le take-profit est
  un ordre séparé, dont l'annulation du frère reste à écrire (voir `docs/KRAKEN.md`).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

import ccxt

from app.engine.risk_gate import ExchangeConstraints, RiskDecision

__all__ = [
    "KeyAudit",
    "KeyPermissionError",
    "KrakenAdapterError",
    "KrakenMarketInfo",
    "OrderPlanError",
    "bracket_params",
    "build_bracket_entry",
    "build_exchange",
    "market_info",
    "resolve_taker_fee",
    "to_exchange_amount",
    "to_exchange_price",
    "to_exchange_stop_price",
    "verify_key_permissions",
]


class KrakenAdapterError(RuntimeError):
    """Toute anomalie de la passerelle qui doit arrêter le bot, pas être ignorée."""


class KeyPermissionError(KrakenAdapterError):
    """La clé n'a pas les droits requis, ou ses droits n'ont pas pu être vérifiés."""


class OrderPlanError(KrakenAdapterError):
    """La décision approuvée ne se traduit pas en ordre sans casser le budget."""


# ---------------------------------------------------------------------------
# Permissions de la clé
# ---------------------------------------------------------------------------

# Kraken renvoie des libellés en kebab-case (query-funds, modify-trades...), alors
# que son interface affiche des libellés espacés (Query Funds, Create & Modify
# Orders...). On normalise les deux et on teste des intentions, pas des chaînes
# exactes : un renommage côté Kraken doit produire un refus lisible, jamais un
# « probablement bon ».
#
# Chaque exigence accepte plusieurs formulations : Kraken a renommé « Cancel/Close
# Orders » en `close-trades` dans son nouveau modèle de permissions, et une clé
# existante peut encore renvoyer `cancel-orders`.
_REQUIRED_PERMISSIONS: dict[str, tuple[frozenset[str], ...]] = {
    "consulter les fonds": (frozenset({"query", "fund"}),),
    "consulter les ordres ouverts": (frozenset({"query", "open"}),),
    "consulter les ordres clôturés": (frozenset({"query", "closed"}),),
    "créer et modifier des ordres": (frozenset({"modify"}),),
    "annuler des ordres": (frozenset({"cancel"}), frozenset({"close", "trade"})),
}

# Le seul droit réellement dangereux pour une clé de trading. Testé en premier :
# une clé qui peut retirer est refusée même si tout le reste est correct.
_FORBIDDEN_TOKENS: frozenset[str] = frozenset({"withdraw"})


def _tokens(permission: str) -> frozenset[str]:
    """Découpe un libellé en jetons comparables.

    Comparer des sous-chaînes faisait passer `query-closed-trades` pour un droit
    d'annulation (« closed » contient « close »), donc une clé sans droit de clôture
    était acceptée. Les jetons sont comparés exactement, au pluriel près.
    """
    normalised = (
        permission.strip()
        .lower()
        .replace("-", " ")
        .replace("_", " ")
        .replace("&", " ")
        .replace("/", " ")
    )
    return frozenset(word.rstrip("s") for word in normalised.split() if word)


@dataclass(frozen=True, slots=True)
class KeyAudit:
    """Ce que Kraken dit de la clé utilisée, au-delà des seules permissions."""

    permissions: frozenset[str]
    ip_allowlist: tuple[str, ...]
    nonce_window: int | None


def verify_key_permissions(exchange: Any) -> KeyAudit:
    """Lit `GetApiKeyInfo` et refuse tout ce qui n'est pas explicitement autorisé.

    Fail-closed : endpoint indisponible, réponse vide ou illisible → exception.
    On ne suppose jamais qu'une clé est saine parce qu'on n'a pas pu la vérifier.
    """
    try:
        payload = exchange.privatePostGetApiKeyInfo()
    except Exception as exc:  # noqa: BLE001 — toute panne devient un refus explicite
        raise KeyPermissionError(f"permissions de clé illisibles : {exc}") from exc

    if not isinstance(payload, Mapping):
        raise KeyPermissionError(f"réponse GetApiKeyInfo inattendue : {type(payload).__name__}")
    result = payload.get("result", payload)
    raw = result.get("permissions") if isinstance(result, Mapping) else None
    if not raw:
        raise KeyPermissionError(
            "GetApiKeyInfo n'a retourné aucune permission : impossible de vérifier la clé, "
            "donc refus (fail-closed)."
        )

    observed = frozenset(str(item) for item in raw)
    token_sets = {item: _tokens(item) for item in observed}

    forbidden = sorted(
        item for item, tokens in token_sets.items() if tokens & _FORBIDDEN_TOKENS
    )
    if forbidden:
        raise KeyPermissionError(
            "DANGER : la clé autorise les retraits (" + ", ".join(forbidden) + "). "
            "Remplacer la clé par une clé de trading sans droit de retrait."
        )

    missing = [
        label
        for label, alternatives in _REQUIRED_PERMISSIONS.items()
        if not any(alternative <= tokens for tokens in token_sets.values() for alternative in alternatives)
    ]
    if missing:
        raise KeyPermissionError(
            "permissions manquantes : " + ", ".join(missing)
            + ". Permissions vues : " + ", ".join(sorted(observed))
        )

    # Kraken répond en snake_case (`ip_allowlist`) alors que sa documentation montre
    # du camelCase (`ipAllowlist`). Lire un seul des deux fait croire à tort que la
    # restriction d'IP est absente — vérifié sur une vraie clé.
    ip_raw = result.get("ip_allowlist") or result.get("ipAllowlist") or ()
    if isinstance(ip_raw, str):
        ip_raw = (ip_raw,)
    nonce_window = result.get("nonce_window", result.get("nonceWindow"))

    return KeyAudit(
        permissions=observed,
        ip_allowlist=tuple(str(item) for item in ip_raw),
        nonce_window=int(nonce_window) if isinstance(nonce_window, int) else None,
    )


# ---------------------------------------------------------------------------
# Marchés, précision, limites
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KrakenMarketInfo:
    """Ce que le pare-feu a besoin de savoir d'un marché, en `Decimal`."""

    symbol: str          # symbole unifié CCXT, ex. BTC/EUR
    exchange_symbol: str  # identifiant Kraken, ex. XXBTZEUR
    base: str
    quote: str
    active: bool
    taker_fee: Decimal
    maker_fee: Decimal
    constraints: ExchangeConstraints


def build_exchange(*, api_key: str = "", secret: str = "", enable_rate_limit: bool = True) -> Any:
    """Instancie CCXT pour Kraken.

    `enableRateLimit` n'est pas optionnel en pratique : Kraken applique un compteur
    d'appels avec décroissance. `adjustForTimeDifference` corrige l'horloge, il ne
    résout pas les nonces (voir `docs/KRAKEN.md`).
    """
    exchange = ccxt.kraken(
        {
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": enable_rate_limit,
            "options": {"adjustForTimeDifference": True},
        }
    )
    # Respecter le proxy et la chaîne de certificats fournis par l'hôte.
    # Requests conserve la vérification TLS et NO_PROXY pour les accès locaux.
    exchange.session.trust_env = True
    return exchange


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def resolve_taker_fee(exchange: Any, symbol: str, fallback: Decimal) -> Decimal:
    """Tarif taker **du compte** pour ce marché, ou le repli si l'API privée échoue.

    CCXT expose `market["taker"]` = 0,26 % pour Kraken, un tarif historique. Le
    palier 1 actuel est 0,80 % (depuis le 2026-07-09). Quand la clé est présente,
    `fetch_trading_fee` renvoie le tarif réel du compte ; sinon on utilise le repli
    configuré, jamais le tarif périmé du marché.
    """
    try:
        fee = exchange.fetch_trading_fee(symbol)
        taker = _decimal(fee.get("taker") or 0)
        if taker > 0:
            return taker
    except Exception:  # noqa: BLE001 — lecture privée, repli explicite
        pass
    return fallback


def market_info(exchange: Any, symbol: str, *,
                taker_fee: Decimal | None = None,
                maker_fee: Decimal | None = None) -> KrakenMarketInfo:
    """Traduit un marché CCXT en contraintes utilisables par `evaluate()`.

    `taker_fee`/`maker_fee` permettent de passer le tarif réel du compte ; sans
    override, on garde la valeur du marché CCXT (utile aux tests et aux scans
    publics).
    """
    market = exchange.market(symbol)
    if market.get("spot") is not True:
        raise KrakenAdapterError(f"{symbol} n'est pas un marché spot")
    limits = market.get("limits") or {}
    precision = market.get("precision") or {}
    amount_limits = limits.get("amount") or {}
    cost_limits = limits.get("cost") or {}

    constraints = ExchangeConstraints(
        amount_step=_decimal(precision["amount"]),
        min_amount=_decimal(amount_limits["min"]),
        min_notional=_decimal(cost_limits["min"]),
        price_tick=_decimal(precision["price"]),
    )
    return KrakenMarketInfo(
        symbol=market["symbol"],
        exchange_symbol=market["id"],
        base=market["base"],
        quote=market["quote"],
        active=bool(market.get("active")),
        taker_fee=taker_fee if taker_fee is not None else _decimal(market.get("taker") or 0),
        maker_fee=maker_fee if maker_fee is not None else _decimal(market.get("maker") or 0),
        constraints=constraints,
    )


def to_exchange_amount(exchange: Any, symbol: str, amount: Decimal) -> str:
    """Formate une quantité au pas de l'exchange, sans jamais dépasser l'entrée."""
    if not isinstance(amount, Decimal):
        raise OrderPlanError("la quantité doit être un Decimal, pas un flottant")
    if amount <= 0:
        raise OrderPlanError(f"quantité non positive : {amount}")
    formatted = exchange.amount_to_precision(symbol, amount)
    value = Decimal(formatted)
    if value > amount:
        raise OrderPlanError(
            f"l'arrondi exchange ({formatted}) dépasse la quantité validée ({amount}) : "
            "le risque effectif sortirait du budget"
        )
    return formatted


def to_exchange_price(exchange: Any, symbol: str, price: Decimal) -> str:
    """Formate un prix au tick de l'exchange. Utilisé pour l'entrée ou le TP."""
    if not isinstance(price, Decimal):
        raise OrderPlanError("le prix doit être un Decimal, pas un flottant")
    if price <= 0:
        raise OrderPlanError(f"prix non positif : {price}")
    return exchange.price_to_precision(symbol, price)


def to_exchange_stop_price(exchange: Any, symbol: str, stop_price: Decimal) -> str:
    """Formate le stop sans jamais le rapprocher de l'entrée.

    Un stop arrondi **plus bas** augmente la distance réelle et donc la perte en cas
    de déclenchement : le budget de risque serait dépassé par un simple arrondi.
    On refuse plutôt que de accepter silencieusement.
    """
    formatted = to_exchange_price(exchange, symbol, stop_price)
    if Decimal(formatted) < stop_price:
        raise OrderPlanError(
            f"le stop arrondi ({formatted}) est plus bas que le stop validé ({stop_price}) : "
            "la perte dépasserait le budget"
        )
    return formatted


# ---------------------------------------------------------------------------
# Construction de l'ordre (aucun envoi ici)
# ---------------------------------------------------------------------------


def bracket_params(stop_price: str) -> dict[str, Any]:
    """Paramètre `close` de Kraken : stop-loss au marché attaché à l'ordre d'entrée.

    Le nom du paramètre compte : CCXT 4.5.91 lit `stopLossPrice`, pas `stopPrice`.
    Avec `stopPrice`, la requête part avec `ordertype=stop-loss` mais **sans prix**.
    """
    if not stop_price:
        raise OrderPlanError("un bracket sans prix de stop n'est pas un bracket")
    return {"close": {"ordertype": "stop-loss", "price": stop_price}}


def build_bracket_entry(exchange: Any, decision: RiskDecision, symbol: str) -> dict[str, Any]:
    """Arguments prêts pour `exchange.create_order(**args)`.

    Exige une `RiskDecision` **approuvée** : la taille et le stop viennent d'elle,
    jamais d'un appelant. C'est la barrière de type qui rend le contournement du
    pare-feu impossible.
    """
    if not decision.approved:
        raise OrderPlanError(
            f"décision refusée ({decision.rejection_code}) : aucun ordre ne peut être construit"
        )
    if decision.amount is None or decision.stop_loss_price is None:
        raise OrderPlanError("décision approuvée sans quantité ou sans stop : incohérente")
    if decision.amount <= 0 or decision.stop_loss_price <= 0:
        raise OrderPlanError("décision approuvée avec une quantité ou un stop non positifs")

    amount = to_exchange_amount(exchange, symbol, decision.amount)
    stop = to_exchange_stop_price(exchange, symbol, decision.stop_loss_price)
    return {
        "symbol": symbol,
        "type": "market",
        "side": "buy",
        "amount": amount,
        "price": None,
        "params": bracket_params(stop),
    }


def is_stop_loss_order(order):
    kind=str(order.get('type') or '').lower().replace('_','-')
    if kind in {'stop-loss','stop-loss-limit','stop-market'}:return True
    if kind in {'take-profit','take-profit-limit'}:return False
    return order.get('stopLossPrice') is not None and order.get('takeProfitPrice') is None
