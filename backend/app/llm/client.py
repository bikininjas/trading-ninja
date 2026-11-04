"""Client LLM local (Ollama) — auditeur de confluences, jamais exécutant.

Contrat, tel que spécifié :

- **Entrée** : un snapshot de marché déjà calculé (prix, EMA50/200 H4, RSI14,
  histogramme MACD, ATR14, déséquilibre du carnet). Aucun solde, aucune clé,
  aucun identifiant de compte.
- **Sortie** : `action`, `confidence`, `market_regime`, `directional_bias`, `thesis`
  (200 caractères maximum). Rien d'autre n'est lu.
- **Garde-fous déterministes appliqués côté client** : un BUY est refusé si le prix
  n'est pas au-dessus de l'EMA200 H4 (marge du pare-feu) ou si le RSI dépasse 70.
  Le modèle est prié de le faire ; le code ne lui fait pas confiance.
- **Repli** : timeout, panne ou réponse illisible → HOLD, confiance 0, signal marqué
  `degraded`. La boucle continue de surveiller, et l'incident est audité.

Température 0, timeout court, sortie JSON demandée. La validation syntaxique reste
la seule garantie : un modèle peut ignorer une consigne, pas un parseur.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

import httpx

from app.core.settings import LLMSettings
from app.engine.risk_gate import MarketSnapshot
from app.market.feed import MarketContext

__all__ = [
    "FORBIDDEN_FIELDS",
    "LLMError",
    "LLMSignal",
    "ParsedSignal",
    "SignalClient",
    "parse_signal",
]

PROMPT_VERSION = "v1"
ACTIONS = frozenset({"BUY", "SELL", "HOLD"})
REGIMES = frozenset({"TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE"})
BIASES = frozenset({"BULLISH", "BEARISH", "NEUTRAL"})
RSI_MAX_BUY = Decimal("70")
THESIS_MAX_CHARS = 200

# Champs qu'un modèle peut inventer et qui ne doivent jamais être lus. Les repérer
# sert à l'audit : on sait que le modèle a essayé, et on sait qu'on n'a pas cédé.
# Schéma imposé au moteur d'inférence (décodage contraint Ollama). Le parseur
# valide de toute façon : un serveur qui ignore `format` ne doit pas produire un
# signal non vérifié.
SIGNAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "market_regime": {
            "type": "string",
            "enum": ["TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE"],
        },
        "directional_bias": {"type": "string", "enum": ["BULLISH", "BEARISH", "NEUTRAL"]},
        "thesis": {"type": "string", "maxLength": THESIS_MAX_CHARS},
    },
    "required": ["action", "confidence", "market_regime", "directional_bias", "thesis"],
}

FORBIDDEN_FIELDS = frozenset(
    {"amount", "quantity", "size", "price", "entry", "stop", "stop_loss", "take_profit",
     "leverage", "risk", "notional", "position_size"}
)

SYSTEM_PROMPT = (
    "Tu es un auditeur de confluences techniques, prudent et factuel. "
    "Tu réponds UNIQUEMENT par un objet JSON valide, sans texte autour, sans balise "
    "de code. Les clés attendues sont exactement : action, confidence, market_regime, "
    "directional_bias, thesis. "
    "action vaut BUY, SELL ou HOLD. confidence est un nombre entre 0 et 1. "
    "market_regime vaut TRENDING_UP, TRENDING_DOWN, RANGING ou VOLATILE. "
    "directional_bias vaut BULLISH, BEARISH ou NEUTRAL. "
    "thesis est une phrase factuelle de 200 caractères maximum. "
    "RÈGLES ABSOLUES : si le prix est sous l'EMA200 H4, tu ne peux pas émettre BUY. "
    "Si le RSI14 est supérieur à 70, tu ne peux pas émettre BUY. Dans ces cas, réponds "
    "HOLD. "
    "Tu ne proposes jamais de taille, de prix, de stop, de take-profit ni de levier : "
    "ces valeurs sont calculées par le système et toute valeur que tu donnerais serait "
    "ignorée. En cas de doute, réponds HOLD avec une confiance basse."
)


class LLMError(RuntimeError):
    """Le modèle n'a pas produit d'intention exploitable."""


@dataclass(frozen=True, slots=True)
class ParsedSignal:
    action: str
    confidence: Decimal
    market_regime: str
    directional_bias: str
    thesis: str
    ignored_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LLMSignal:
    action: str
    confidence: Decimal
    market_regime: str
    directional_bias: str
    thesis: str
    raw: Mapping[str, Any]
    prompt_payload: Mapping[str, Any]
    prompt_sha256: str
    inference_ms: int
    ignored_fields: tuple[str, ...] = ()
    degraded: bool = False
    degradation_reason: str | None = None


def _extract_json(content: str) -> dict:
    """Récupère le premier objet JSON, même entouré de texte ou de balises."""
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        raise LLMError(f"réponse sans objet JSON : {content[:160]!r}")
    try:
        parsed = json.loads(content[start:end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"JSON invalide : {exc.msg} (position {exc.pos})") from exc
    if not isinstance(parsed, dict):
        raise LLMError("la réponse JSON n'est pas un objet")
    return parsed


def _confidence(value: Any) -> Decimal:
    try:
        confidence = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise LLMError(f"confidence illisible : {value!r}") from exc
    if not confidence.is_finite() or confidence < 0 or confidence > 1:
        raise LLMError(f"confidence hors bornes [0,1] : {value!r}")
    return confidence


def parse_signal(content: str) -> ParsedSignal:
    """Valide la réponse du modèle. Ne lit que les cinq champs autorisés."""
    data = _extract_json(content)
    action = str(data.get("action", "")).strip().upper()
    if action not in ACTIONS:
        raise LLMError(f"action inconnue : {data.get('action')!r}")
    regime = str(data.get("market_regime", "")).strip().upper()
    if regime not in REGIMES:
        raise LLMError(f"market_regime inconnu : {data.get('market_regime')!r}")
    bias = str(data.get("directional_bias", "")).strip().upper()
    if bias not in BIASES:
        raise LLMError(f"directional_bias inconnu : {data.get('directional_bias')!r}")
    thesis = str(data.get("thesis", "")).strip()[:THESIS_MAX_CHARS]
    ignored = tuple(sorted(key for key in data if key.lower() in FORBIDDEN_FIELDS))
    return ParsedSignal(
        action=action,
        confidence=_confidence(data.get("confidence")),
        market_regime=regime,
        directional_bias=bias,
        thesis=thesis,
        ignored_fields=ignored,
    )


class SignalClient:
    """Appelle Ollama et rend une intention validée, ou un HOLD de repli."""

    def __init__(self, settings: LLMSettings, *, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._transport = transport

    # -- payload ---------------------------------------------------------

    def _payload(self, symbol: str, snapshot: MarketSnapshot, context: MarketContext,
                 has_position: bool, trend_buffer_pct: Decimal) -> tuple[dict, dict]:
        distance_pct = (snapshot.price - snapshot.ema_200_h4) / snapshot.ema_200_h4
        atr_pct = snapshot.atr_14_h4 / snapshot.price
        market = {
            "symbol": symbol,
            "decision_timeframe": "4h",
            "last_close": str(snapshot.price),
            "ema_50_h4": str(context.ema_50_h4),
            "ema_200_h4": str(snapshot.ema_200_h4),
            "distance_to_ema200_pct": str(distance_pct.quantize(Decimal("0.000001"))),
            "above_ema200_with_buffer": snapshot.price
            > snapshot.ema_200_h4 * (1 + trend_buffer_pct),
            "rsi_14": str(context.rsi_14_h4.quantize(Decimal("0.01"))),
            "macd_histogram": str(context.macd_histogram.quantize(Decimal("0.00000001"))),
            "atr_14_h4": str(snapshot.atr_14_h4),
            "atr_pct_of_price": str(atr_pct.quantize(Decimal("0.000001"))),
            "order_book_imbalance": str(context.order_book_imbalance.quantize(Decimal("0.0001"))),
            "last_closed_h1_return_atr": str(snapshot.last_closed_h1_return_atr),
            "position_already_open": has_position,
            "prompt_version": PROMPT_VERSION,
        }
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Contexte de marché :\n" + json.dumps(market, ensure_ascii=False)
                + "\nRéponds en JSON avec action, confidence, market_regime, "
                  "directional_bias, thesis.",
            },
        ]
        payload = {
            "model": self._settings.model,
            "messages": messages,
            "stream": False,
            # Coupe la phase de réflexion : sur un modèle « thinking », elle coûte
            # plusieurs secondes et ne sert pas un schéma de cinq champs.
            "think": False,
            # Décodage contraint : le moteur restreint les tokens au schéma.
            "format": SIGNAL_SCHEMA,
            "options": {"temperature": self._settings.temperature, "num_predict": 220},
            "keep_alive": "30m",
        }
        return payload, market

    def _root(self) -> str:
        root = self._settings.base_url.rstrip("/")
        return root[:-3] if root.endswith("/v1") else root

    async def _post(self, payload: dict, timeout: float) -> dict:
        # API native d'Ollama : c'est la seule qui accepte `think` et un schéma de
        # décodage contraint. L'endpoint compatible OpenAI ignore les deux.
        url = self._root() + "/api/chat"
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout), transport=self._transport
        ) as client:
            response = await client.post(url, json=payload)
        if response.status_code != 200:
            raise LLMError(f"HTTP {response.status_code} du modèle : {response.text[:200]}")
        body = response.json()
        if not isinstance(body, dict) or "message" not in body:
            raise LLMError("réponse native sans champ message")
        return body

    # -- appel -----------------------------------------------------------

    def _fallback(self, snapshot: MarketSnapshot, reason: str) -> LLMSignal:
        # Le régime du repli est **factuel** (prix vs EMA200), pas une invention du
        # modèle : `model_name` et `thesis` disent explicitement qu'il s'agit d'un repli.
        regime = (
            "TRENDING_UP"
            if snapshot.price > snapshot.ema_200_h4
            else "TRENDING_DOWN"
        )
        return LLMSignal(
            action="HOLD",
            confidence=Decimal(0),
            market_regime=regime,
            directional_bias="NEUTRAL",
            thesis=f"REPLI TECHNIQUE : {reason}"[:THESIS_MAX_CHARS],
            raw={"error": reason},
            prompt_payload={},
            prompt_sha256="",
            inference_ms=0,
            degraded=True,
            degradation_reason=reason,
        )

    async def propose(self, symbol: str, snapshot: MarketSnapshot, context: MarketContext, *,
                      has_position: bool = False,
                      trend_buffer_pct: Decimal = Decimal("0.005")) -> LLMSignal:
        payload, market = self._payload(symbol, snapshot, context, has_position, trend_buffer_pct)
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        started = time.perf_counter()

        body: dict | None = None
        last_error: Exception | None = None
        for attempt in range(self._settings.max_retries + 1):
            try:
                body = await self._post(payload, self._settings.timeout_seconds)
                break
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt < self._settings.max_retries:
                    continue
            except LLMError as exc:
                return self._fallback(snapshot, str(exc))
            except ValueError as exc:  # JSON de transport illisible
                return self._fallback(snapshot, f"réponse non JSON : {exc}")
        if body is None:
            return self._fallback(snapshot, f"modèle injoignable : {last_error}")

        try:
            content = body["message"]["content"]
            parsed = parse_signal(content)
        except (KeyError, TypeError) as exc:
            return self._fallback(snapshot, f"réponse malformée : {exc}")
        except LLMError as exc:
            return self._fallback(snapshot, str(exc))

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        signal = LLMSignal(
            action=parsed.action,
            confidence=parsed.confidence,
            market_regime=parsed.market_regime,
            directional_bias=parsed.directional_bias,
            thesis=parsed.thesis,
            raw={
                "content": content,
                "eval_count": body.get("eval_count"),
                "total_duration_ms": (body.get("total_duration") or 0) // 1_000_000,
            },
            prompt_payload=market,
            prompt_sha256=digest,
            inference_ms=elapsed_ms,
            ignored_fields=parsed.ignored_fields,
        )
        return self._enforce_rules(signal, snapshot, trend_buffer_pct)

    def _enforce_rules(self, signal: LLMSignal, snapshot: MarketSnapshot,
                       trend_buffer_pct: Decimal) -> LLMSignal:
        """Rend inopérant un BUY que les règles interdisent.

        Le prompt le demande déjà ; le code le garantit. Un modèle qui ignore la
        consigne produit un HOLD, pas une position contre-tendance.
        """
        if signal.action != "BUY":
            return signal
        reasons = []
        floor = snapshot.ema_200_h4 * (Decimal(1) + trend_buffer_pct)
        if snapshot.price <= floor:
            reasons.append(f"prix {snapshot.price} sous EMA200+marge ({floor:.2f})")
        context_rsi = None
        try:
            context_rsi = Decimal(str(signal.prompt_payload.get("rsi_14")))
        except (InvalidOperation, TypeError):
            pass
        if context_rsi is not None and context_rsi > RSI_MAX_BUY:
            reasons.append(f"RSI14 {context_rsi} > {RSI_MAX_BUY}")
        if not reasons:
            return signal
        return LLMSignal(
            action="HOLD",
            confidence=Decimal(0),
            market_regime=signal.market_regime,
            directional_bias=signal.directional_bias,
            thesis=("BUY rétrogradé : " + "; ".join(reasons))[:THESIS_MAX_CHARS],
            raw=signal.raw,
            prompt_payload=signal.prompt_payload,
            prompt_sha256=signal.prompt_sha256,
            inference_ms=signal.inference_ms,
            ignored_fields=signal.ignored_fields,
            degraded=False,
            degradation_reason="règle déterministe : " + "; ".join(reasons),
        )

    async def warmup(self, timeout_seconds: float = 180.0) -> bool:
        """Charge le modèle en mémoire avant la première décision.

        Un premier appel à froid dépasse largement les 4 secondes du contrat ; sans
        préchauffage, la première intention serait toujours un repli.
        """
        root = self._root()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as client:
                response = await client.post(
                    root + "/api/generate",
                    json={"model": self._settings.model, "prompt": "", "stream": False,
                          "keep_alive": "30m", "options": {"num_predict": 1}},
                )
            return response.status_code == 200
        except httpx.HTTPError:
            return False
