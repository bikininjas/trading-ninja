"""Quota mesuré, rétention et lecture privée causale des digests."""
from datetime import timedelta
from decimal import Decimal
import json
from app.market.history import storage_verdict


class BudgetExceeded(RuntimeError):
    """Le digest ne tient pas dans le quota avec le plancher protégé."""


async def store_within_budget(conn, row, *, quota_mb=500, min_free_mb=100):
    # Borne dure D-057 : une configuration ne peut abaisser les 100 Mo protégés.
    minimum = max(100, min_free_mb) * 1_000_000
    await conn.execute("DELETE FROM market_intel_digests WHERE observed_at < $1", row["observed_at"] - timedelta(days=30))
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(1414416963, 1)")
        size = await conn.fetchval("SELECT pg_database_size(current_database())")
        sources = json.dumps(row.get("source_items", []), ensure_ascii=False)
        row_bytes = max(65536, len(sources.encode("utf-8")) + 16384)
        verdict = storage_verdict(db_bytes=int(size), row_bytes=row_bytes, rows_to_add=1,
                                  quota_bytes=quota_mb * 1_000_000, min_free_bytes=minimum)
        if not verdict.allowed:
            raise BudgetExceeded(verdict.reason)
        return await conn.fetchval(
            "INSERT INTO market_intel_digests(observed_at,valid_until,status,overall_sentiment,sentiment_score,"
            "systemic_risk_detected,affected_assets,catalyst_summary,model,source_hashes,source_items,"
            "fear_and_greed_value,fear_and_greed_at,analysis_response,analysis_error) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13,$14,$15) RETURNING id",
            row["observed_at"], row["valid_until"], row["status"], row["overall_sentiment"],
            Decimal(str(row["sentiment_score"])), row["systemic_risk_detected"], row["affected_assets"],
            row["catalyst_summary"], row["model"], row["source_hashes"], sources,
            row.get("fear_and_greed_value"), row.get("fear_and_greed_at"),
            row.get("analysis_response"), row.get("analysis_error"))


async def latest_digest(conn, now):
    # Un repli NEUTRAL hors ligne ne supprime pas une alerte encore valide.
    row = await conn.fetchrow("SELECT * FROM market_intel_digests WHERE status='OK' AND observed_at <= $1 "
                              "AND valid_until > $1 ORDER BY observed_at DESC LIMIT 1", now)
    return dict(row) if row else None


def dashboard_row(row):
    if row is None:
        return None
    data = dict(row)
    items = data.get("source_items", [])
    data["source_items"] = json.loads(items) if isinstance(items, str) else items
    return data


async def dashboard_digests(conn, now, selected_id=None):
    """Historique borné, sources de l'analyse choisie et veto réellement actif."""
    fields = ("id,observed_at,valid_until,status,overall_sentiment,sentiment_score,"
              "systemic_risk_detected,affected_assets,catalyst_summary,model,"
              "cardinality(source_hashes) AS source_count,analysis_error")
    history = [dict(row) for row in await conn.fetch(
        f"SELECT {fields} FROM market_intel_digests WHERE observed_at <= $1 "
        "ORDER BY observed_at DESC,id DESC LIMIT 12", now)]
    active = await latest_digest(conn, now)
    latest = history[0] if history else None
    chosen = selected_id or (active["id"] if active else latest["id"] if latest else None)
    selected = dashboard_row(await conn.fetchrow(
        "SELECT * FROM market_intel_digests WHERE id=$1 AND observed_at <= $2", chosen, now)) if chosen else None
    if selected_id and selected is None:
        raise LookupError("analyse inconnue")
    return {"as_of": now, "latest_attempt": latest, "active": dashboard_row(active),
            "selected": selected, "history": history}
