"""Golden de backtest : décisions enregistrées + métriques attendues.

Le fichier golden contient les bougies de la fenêtre, les décisions du modèle à
chaque instant, et les métriques produites par le moteur. Il permet deux choses :

1. **régression déterministe** : rejouer les décisions enregistrées à travers le
   code courant, sans LLM, et comparer les métriques. Toute modification du
   pare-feu, du dimensionnement, des frais ou du moteur change le résultat ;
2. **dérive du modèle** : quand le prompt, le modèle ou le profil de risque change,
   le golden devient périmé et doit être ré-enregistré après relecture du diff.

Le golden n'est **pas** une preuve de rentabilité : c'est un filet anti-régression.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.backtest.engine import BacktestResult
from app.engine.risk_gate import ExchangeConstraints
from app.exchange.kraken import KrakenMarketInfo
from app.llm.client import PROMPT_VERSION, SYSTEM_PROMPT

__all__ = [
    "GoldenBundle",
    "RecordedSignal",
    "market_from_bundle",
    "ReplayLLM",
    "diff_metrics",
    "load_bundle",
    "metrics_of",
    "prompt_fingerprint",
    "risk_fingerprint",
    "save_bundle",
]

GOLDEN_VERSION = 1


@dataclass
class GoldenBundle:
    symbol: str
    model: str
    prompt_fingerprint: str
    risk_fingerprint: str
    start_index: int
    end_index: int
    h4: list[list]
    h1: list[list]
    decisions: list[dict[str, Any]]
    expected: dict[str, str]
    market: dict[str, str] = field(default_factory=dict)
    created_at: str = ""
    version: int = GOLDEN_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def save_bundle(bundle: GoldenBundle, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(bundle.to_dict(), ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_bundle(path: str | Path) -> GoldenBundle:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("version") != GOLDEN_VERSION:
        raise ValueError(
            f"golden version {data.get('version')} inconnue (attendu {GOLDEN_VERSION})"
        )
    return GoldenBundle(**data)


def prompt_fingerprint(settings: Any) -> str:
    """Identité du couple prompt + modèle. Un changement invalide les décisions."""
    payload = {
        "model": settings.llm.model,
        "prompt_version": PROMPT_VERSION,
        "system_prompt": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def risk_fingerprint(settings: Any, info: Any) -> str:
    """Identité du risque, des frais et du capital de départ. Change les résultats."""
    risk = asdict(settings.risk)
    # Préserver l'identité des anciens profils lorsque les nouveaux mécanismes
    # sont désactivés ; leur activation reste bien couverte par l'empreinte.
    if not settings.risk.cost_aware and not settings.risk.profit_trailing_enabled:
        for key in ("cost_aware", "execution_fee_pct", "execution_slippage_pct",
                    "opportunity_r_multiple", "min_net_reward_to_risk",
                    "profit_trailing_enabled", "profit_trailing_start_r",
                    "profit_trailing_distance_r"):
            risk.pop(key)
    payload = {
        "risk": {key: str(value) for key, value in risk.items()},
        "taker_fee": str(info.taker_fee),
        "slippage_bps": str(settings.paper_slippage_bps),
        "start_equity": str(settings.paper_start_equity),
    }
    if getattr(settings, 'capital_policy', None) and settings.capital_policy.enabled:
        payload['capital_policy'] = {key: str(value) for key, value in asdict(settings.capital_policy).items()}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def metrics_of(result: BacktestResult) -> dict[str, str]:
    """Métriques comparables, en texte : aucun flottant, aucune tolérance."""
    approved_counterfactuals = [c for c in result.counterfactuals if c.approved]
    counterfactual_pnl = sum(
        (c.pnl_quote for c in approved_counterfactuals), Decimal(0)
    )
    return {
        "decisions": str(result.decisions),
        "buy": str(result.buy),
        "hold": str(result.hold),
        "downgraded": str(result.downgraded),
        "degraded": str(result.degraded),
        "approvals": str(result.approvals),
        "sell": str(result.sell),
        "halt": str(result.halt),
        "llm_exits": str(result.llm_exits),
        "rejections": json.dumps(dict(result.rejections), sort_keys=True),
        "trades": str(len(result.trades)),
        "wins": str(len(result.wins)),
        "losses": str(len(result.losses)),
        "win_rate": str(result.win_rate),
        "net_pnl": str(result.net_pnl),
        "equity_end": str(result.equity_end),
        "max_drawdown_pct": str(result.max_drawdown_pct),
        "buy_and_hold_pct": str(result.buy_and_hold_pct),
        "counterfactual_approved": str(len(approved_counterfactuals)),
        "counterfactual_pnl": str(counterfactual_pnl),
        "baseline_trades": str(len(result.baseline_trades)),
        "baseline_equity_end": str(result.baseline_equity_end),
    }


def diff_metrics(expected: dict[str, str], actual: dict[str, str]) -> list[str]:
    return [
        f"  {key}: {expected.get(key, '?')} → {actual.get(key, '?')}"
        for key in sorted(set(expected) | set(actual))
        if expected.get(key) != actual.get(key)
    ]


class RecordedSignal:
    """Rend une décision enregistrée avec l'interface d'un `LLMSignal`."""

    def __init__(self, row: dict[str, Any]) -> None:
        self.action = row["action"]
        self.confidence = Decimal(str(row["confidence"]))
        self.market_regime = row["market_regime"]
        self.directional_bias = row.get("directional_bias", "NEUTRAL")
        self.thesis = row.get("thesis", "")
        self.inference_ms = int(row.get("inference_ms", 0))
        self.degraded = bool(row.get("degraded", False))
        self.degradation_reason = row.get("degradation_reason", "")


class ReplayLLM:
    """Sert les décisions du golden. Échoue si une décision manque.

    Un HOLD silencieux serait pire qu'une erreur : il masquerait une fenêtre
    différente entre l'enregistrement et le rejeu. Le mode `scenario` peut
    demander explicitement un HOLD par défaut quand la géométrie de sortie
    change et que le golden ne contient pas la décision correspondante.
    """

    def __init__(self, decisions: list[dict[str, Any]], *, default: str | None = None) -> None:
        self._by_time = {row["at"]: row for row in decisions}
        self._default = default
        self.missing = 0

    async def __call__(self, snapshot, context, has_position: bool = False) -> RecordedSignal:
        key = snapshot.tick_at.isoformat()
        row = self._by_time.get(key)
        if row is None:
            if self._default is not None:
                self.missing += 1
                return RecordedSignal({
                    "action": self._default,
                    "confidence": "0",
                    "market_regime": "RANGING",
                    "thesis": "décision absente du golden : HOLD de scénario",
                })
            raise AssertionError(
                f"aucune décision enregistrée pour {key} — la fenêtre du golden "
                "ne correspond plus au rejeu (bougies ou indices modifiés ?)"
            )
        return RecordedSignal(row)


def market_from_bundle(bundle: GoldenBundle) -> KrakenMarketInfo:
    """Reconstruit les contraintes figées, sans réseau."""
    market = bundle.market
    return KrakenMarketInfo(
        symbol=bundle.symbol,
        exchange_symbol=market.get("exchange_symbol", bundle.symbol.replace("/", "")),
        base=market.get("base", bundle.symbol.split("/")[0]),
        quote=market.get("quote", bundle.symbol.split("/")[-1]),
        active=True,
        taker_fee=Decimal(market.get("taker_fee", "0.0026")),
        maker_fee=Decimal(market.get("maker_fee", "0.0016")),
        constraints=ExchangeConstraints(
            amount_step=Decimal(market.get("amount_step", "0.00000001")),
            min_amount=Decimal(market.get("min_amount", "0.00001")),
            min_notional=Decimal(market.get("min_notional", "1")),
            price_tick=Decimal(market.get("price_tick", "0.01")),
        ),
    )
