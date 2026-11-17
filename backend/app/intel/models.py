"""Contrat strict et portée temporelle des observations publiques."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool


class MarketIntelDigest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    overall_sentiment: Literal["BULLISH", "NEUTRAL", "BEARISH", "CRISIS"]
    sentiment_score: float = Field(ge=-1, le=1, strict=True)
    systemic_risk_detected: StrictBool
    affected_assets: list[str] = Field(max_length=30)
    catalyst_summary: str = Field(max_length=240)


def neutral():
    return MarketIntelDigest(overall_sentiment="NEUTRAL", sentiment_score=0,
                            systemic_risk_detected=False, affected_assets=[],
                            catalyst_summary="Veille indisponible : aucun contexte public validé.")


def usable(row, now: datetime):
    return bool(row and row.get("status") == "OK"
                and isinstance(row.get("observed_at"), datetime)
                and isinstance(row.get("valid_until"), datetime)
                and row["observed_at"].tzinfo and row["valid_until"].tzinfo
                and row["observed_at"] <= now < row["valid_until"])


def systemic_for(row, symbol: str, now: datetime) -> bool:
    if not usable(row, now) or row.get("systemic_risk_detected") is not True:
        return False
    assets = row.get("affected_assets")
    if not isinstance(assets, list):
        return False
    return not assets or "ALL" in assets or symbol.split("/")[0].upper() in assets
