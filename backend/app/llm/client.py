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

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

import httpx

from app.core.settings import LLMSettings
from app.engine.risk_gate import MarketSnapshot
from app.llm.lessons import lessons_block
from app.market.feed import MarketContext

__all__ = [
    "D1_PROMPT_VERSION",
    "D1_SYSTEM_PROMPT",
    "FORBIDDEN_FIELDS",
    "LLMError",
    "LLMSignal",
    "ParsedSignal",
    "SignalClient",
    "parse_signal",
]

PROMPT_VERSION = "v3"
D1_PROMPT_VERSION = "v3-d1"
ACTIONS = frozenset({"BUY", "SELL", "HOLD", "HALT"})
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
        "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD", "HALT"]},
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

LESSONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "lessons": {
            "type": "array",
            "items": {"type": "string", "maxLength": 240},
            "minItems": 1,
            "maxItems": 5,
        }
    },
    "required": ["lessons"],
}

FORBIDDEN_FIELDS = frozenset(
    {"amount", "quantity", "size", "price", "entry", "stop", "stop_loss", "take_profit",
     "leverage", "risk", "notional", "position_size"}
)

SYSTEM_PROMPT = (
    "Tu es un trader autonome prudent, factuel et discipliné. "
    "Tu réponds UNIQUEMENT par un objet JSON valide, sans texte autour, sans balise "
    "de code. Les clés attendues sont exactement : action, confidence, market_regime, "
    "directional_bias, thesis. "
    "action vaut BUY, SELL, HOLD ou HALT. confidence est un nombre entre 0 et 1. "
    "market_regime vaut TRENDING_UP, TRENDING_DOWN, RANGING ou VOLATILE. "
    "directional_bias vaut BULLISH, BEARISH ou NEUTRAL. "
    "thesis est une phrase factuelle de 200 caractères maximum. "
    "RÈGLES ABSOLUES : si le prix est sous l'EMA200 H4, tu ne peux pas émettre BUY. "
    "Si le RSI14 est supérieur à 70, tu ne peux pas émettre BUY. Dans ces cas, réponds "
    "HOLD. "
    "SCÉNARIO D'ACHAT : un pullback dans une tendance haussière est le setup normal. "
    "Il est réuni quand le prix est au-dessus de l'EMA200 H4 avec la marge, que l'EMA50 "
    "H4 est au-dessus de l'EMA200 H4, que le RSI14 est entre 40 et 65 (repli, pas "
    "surchauffe), que l'ATR reste dans la bande normale et que la dernière H1 n'est pas "
    "une chute verticale. Dans ce cas, BUY est le scénario par défaut ; ne réponds HOLD "
    "que si tu vois une raison factuelle de refuser (MACD franchement négatif, régime "
    "baissier, couteau, volatilité anormale). Ne réponds pas HOLD simplement parce que "
    "le RSI est neutre : dans un pullback, la neutralité du RSI est attendue. "
    "Un SELL ne sert qu'à clôturer une position déjà ouverte : tu ne vends jamais "
    "à découvert. Un HALT arrête tout : à utiliser uniquement si la situation est "
    "réellement dangereuse et que tu ne veux plus aucune exposition. "
    "Tu ne proposes jamais de taille, de prix, de stop, de take-profit ni de levier : "
    "ces valeurs sont calculées par le système et toute valeur que tu donnerais serait "
    "ignorée. En cas de doute réel, réponds HOLD avec une confiance basse."
)


D1_SYSTEM_PROMPT = (
    "Tu es un trader de tendance Daily prudent, factuel et discipliné. "
    "Tu réponds UNIQUEMENT par un objet JSON valide, sans texte autour, sans balise "
    "de code. Les clés attendues sont exactement : action, confidence, market_regime, "
    "directional_bias, thesis. "
    "action vaut BUY, SELL, HOLD ou HALT. confidence est un nombre entre 0 et 1. "
    "market_regime vaut TRENDING_UP, TRENDING_DOWN, RANGING ou VOLATILE. "
    "directional_bias vaut BULLISH, BEARISH ou NEUTRAL. "
    "thesis est une phrase factuelle de 200 caractères maximum. "
    "RÈGLES ABSOLUES : si le prix est sous l'EMA200 D1, tu ne peux pas émettre BUY. "
    "Si le RSI14 D1 est supérieur à 70, tu ne peux pas émettre BUY. Si le contexte "
    "Daily manque, réponds HOLD. "
    "SCÉNARIO D'ACHAT : une tendance de fond saine avec l'EMA50 au-dessus de l'EMA200 "
    "D1 et une pente d'EMA50 positive. Le déclencheur est soit une cassure du plus "
    "haut Donchian 20, soit un rebond de valeur sur l'EMA20 D1 après un creux. "
    "Dans un tel setup, BUY est le scénario par défaut ; ne réponds HOLD que si tu "
    "vois une raison factuelle de refuser : divergence baissière confirmée, chute "
    "verticale de la dernière D1, volatilité anormale, régime baissier ou structure "
    "cassée. Ne réponds pas HOLD simplement parce que le RSI est neutre. "
    "Un SELL ne sert qu'à clôturer une position déjà ouverte : tu ne vends jamais "
    "à découvert. Un HALT arrête tout : à utiliser uniquement si la situation est "
    "réellement dangereuse et que tu ne veux plus aucune exposition. "
    "Tu ne proposes jamais de taille, de prix, de stop, de take-profit ni de levier : "
    "ces valeurs sont calculées par le système et toute valeur que tu donnerais serait "
    "ignorée. En cas de doute réel, réponds HOLD avec une confiance basse."
)


REVIEW_SYSTEM_PROMPT = (
    "Tu es un analyste de trading. Tu reçois un journal de trades passés et de trades "
    "refusés. Tu écris 1 à 5 leçons courtes (240 caractères maximum), factuelles, au "
    "présent, utiles pour éviter les erreurs répétées. Tu ne donnes jamais d'ordre "
    "d'achat ou de vente, jamais de taille, de prix, de stop, de take-profit ni de "
    "levier. Tu réponds UNIQUEMENT par un objet JSON valide, sans texte autour, de la "
    "forme exacte : {\"lessons\": [\"...\"]}."
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
        self._warmup_retry_at = 0.0

    # -- payload ---------------------------------------------------------

    def _payload(self, symbol: str, snapshot: MarketSnapshot, context: MarketContext,
                 has_position: bool, trend_buffer_pct: Decimal,
                 lessons: list[str] | None = None,
                 decision_timeframe: str = "4h") -> tuple[dict, dict]:
        if decision_timeframe == "1d":
            d1 = context.d1
            if d1 is None:
                raise LLMError("contexte D1 absent : le prompt Daily ne peut pas être construit")
            distance_pct = (snapshot.price - snapshot.ema_200_h4) / snapshot.ema_200_h4
            atr_pct = d1.atr_14 / snapshot.price if snapshot.price > 0 else Decimal(0)
            distance_ema50_atr = (
                (snapshot.price - d1.ema_50) / d1.atr_14
                if d1.atr_14 > 0 else Decimal(0)
            )
            market = {
                "symbol": symbol,
                "decision_timeframe": "1d",
                "last_close": str(snapshot.price),
                "ema_20": str(d1.ema_20),
                "ema_50": str(d1.ema_50),
                "ema_200": str(d1.ema_200),
                "ema_20_prev": str(d1.ema_20_prev),
                "ema50_above_ema200": d1.ema_50 > d1.ema_200,
                "ema_50_slope_5d_pct": str(d1.ema_50_slope_5d_pct.quantize(Decimal("0.000001"))),
                "distance_to_ema200_pct": str(distance_pct.quantize(Decimal("0.000001"))),
                "distance_to_ema50_atr": str(distance_ema50_atr.quantize(Decimal("0.01"))),
                "above_ema200_with_buffer": snapshot.price
                > snapshot.ema_200_h4 * (1 + trend_buffer_pct),
                "rsi_14": str(d1.rsi_14.quantize(Decimal("0.01"))),
                "atr_14": str(d1.atr_14),
                "atr_pct_of_price": str(atr_pct.quantize(Decimal("0.000001"))),
                "donchian_high_20": str(d1.donchian_high_20),
                "breakout_donchian": snapshot.price > d1.donchian_high_20,
                "bollinger_upper": str(d1.bollinger_upper),
                "bollinger_mid": str(d1.bollinger_mid),
                "swing_low_10": str(d1.swing_low_10),
                "highest_high_chandelier": str(d1.highest_high_chandelier),
                "last_open": str(d1.last_open),
                "last_low": str(d1.last_low),
                "last_high": str(d1.last_high),
                "prev_close": str(d1.prev_close),
                "prev_low": str(d1.prev_low),
                "value_rebound": (
                    d1.prev_low <= d1.ema_20_prev
                    and snapshot.price > d1.ema_20
                    and snapshot.price > d1.prev_close
                ),
                "bearish_divergence": d1.bearish_divergence,
                "last_d1_return_atr": str(
                    ((d1.last_close - d1.last_open) / d1.atr_14)
                    if d1.atr_14 > 0 else Decimal(0)
                ),
                "position_already_open": has_position,
                "prompt_version": D1_PROMPT_VERSION,
            }
            system_prompt = D1_SYSTEM_PROMPT
        else:
            distance_pct = (snapshot.price - snapshot.ema_200_h4) / snapshot.ema_200_h4
            atr_pct = snapshot.atr_14_h4 / snapshot.price
            distance_ema50_atr = (snapshot.price - context.ema_50_h4) / snapshot.atr_14_h4
            market = {
                "symbol": symbol,
                "decision_timeframe": "4h",
                "last_close": str(snapshot.price),
                "ema_50_h4": str(context.ema_50_h4),
                "ema_200_h4": str(snapshot.ema_200_h4),
                "ema50_above_ema200": context.ema_50_h4 > snapshot.ema_200_h4,
                "distance_to_ema200_pct": str(distance_pct.quantize(Decimal("0.000001"))),
                "distance_to_ema50_atr": str(distance_ema50_atr.quantize(Decimal("0.01"))),
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
            system_prompt = SYSTEM_PROMPT
        if lessons:
            joined = "\n".join(lessons)
            market["lessons_count"] = len(lessons)
            market["lessons_sha256"] = hashlib.sha256(
                joined.encode("utf-8")
            ).hexdigest()
        content = (
            "Contexte de marché :\n" + json.dumps(market, ensure_ascii=False)
        )
        block = lessons_block(lessons)
        if block:
            content += "\n\n" + block
        content += (
            "\nRéponds en JSON avec action, confidence, market_regime, "
            "directional_bias, thesis."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
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

    async def model_identity(self):
        """Identité observée dans l'inventaire local, jamais une clé ni un secret."""
        root=self._settings.base_url.rstrip('/')
        if root.endswith('/v1'):root=root[:-3]
        try:
            async with httpx.AsyncClient(transport=self._transport,timeout=2) as client:
                response=await client.get(root+'/api/tags')
                response.raise_for_status()
                models=response.json().get('models',[])
                matches=[m for m in models if m.get('name')==self._settings.model or m.get('model')==self._settings.model]
                return matches[0].get('digest') if len(matches)==1 else None
        except (httpx.HTTPError,ValueError,TypeError,KeyError):
            return None

    async def propose(self, symbol: str, snapshot: MarketSnapshot, context: MarketContext, *,
                      has_position: bool = False,
                      trend_buffer_pct: Decimal = Decimal("0.005"),
                      lessons: list[str] | None = None,
                      decision_timeframe: str = "4h") -> LLMSignal:
        decision_timeframe = decision_timeframe.lower()
        if decision_timeframe not in {"4h", "1d"}:
            raise ValueError(f"decision_timeframe non géré : {decision_timeframe!r}")
        if decision_timeframe == "1d" and context.d1 is None:
            return self._fallback(snapshot, "contexte D1 absent")
        # La veille H4 peut dépasser la durée de résidence du modèle. Un appel
        # de chargement borné précède chaque décision, sans relancer Ollama ni
        # bloquer indéfiniment la gestion des positions en cas d'indisponibilité.
        if time.monotonic() < self._warmup_retry_at:
            return self._fallback(snapshot, "préchauffage Ollama temporairement indisponible")
        try:
            ready = await asyncio.wait_for(self.warmup(timeout_seconds=30.0), timeout=30.0)
        except TimeoutError:
            ready = False
        if not ready:
            self._warmup_retry_at = time.monotonic() + 30.0
            return self._fallback(snapshot, "préchauffage Ollama impossible ou expiré")
        self._warmup_retry_at = 0.0
        payload, market = self._payload(
            symbol, snapshot, context, has_position, trend_buffer_pct,
            lessons=lessons, decision_timeframe=decision_timeframe,
        )
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

    async def review(self, digest: str, *, timeout_seconds: float = 30.0) -> list[str]:
        """Une seule inférence de relecture : rend des leçons brutes à valider.

        Le prompt de relecture est distinct de celui des décisions ; il ne peut pas
        produire un BUY/SELL/HALT, et sa sortie ne passe jamais par le pare-feu.
        """
        payload = {
            "model": self._settings.model,
            "messages": [
                {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "Journal des résultats récents :\n" + digest,
                },
            ],
            "stream": False,
            "think": False,
            "format": LESSONS_SCHEMA,
            "options": {"temperature": 0, "num_predict": 500},
            "keep_alive": "30m",
        }
        body = await self._post(payload, timeout_seconds)
        try:
            content = body["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LLMError(f"réponse de relecture malformée : {exc}") from exc
        data = _extract_json(content)
        lessons = data.get("lessons")
        if not isinstance(lessons, list):
            raise LLMError("la relecture n'a pas rendu de liste `lessons`")
        return lessons

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
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds),
                                         transport=self._transport) as client:
                response = await client.post(
                    root + "/api/generate",
                    json={"model": self._settings.model, "prompt": "", "stream": False,
                          "keep_alive": "30m", "options": {"num_predict": 1}},
                )
            return response.status_code == 200
        except httpx.HTTPError:
            return False
