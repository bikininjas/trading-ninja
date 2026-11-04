#!/usr/bin/env python3
"""Vérifie les drawdowns sur un jeu de données figé, dans une transaction annulée.

    cd backend
    .venv/bin/python scripts/verify_drawdowns.py

Trois valeurs sont calculées à la main, puis comparées à ce que produit
`PostgresRepository.account()` :

    équité 9,60 · pic du jour 12,00 → 20,00 %
    équité 9,60 · pic 7 jours 16,00 → 40,00 %
    équité 9,60 · plus haut historique 16,00 → 60,00 % (ici 24,00 → 60 %)

Tout est écrit puis annulé : la base réelle sert de bac à sable sans être
modifiée. C'est le critère de la phase 1.8 — il vérifie le SQL, pas seulement
l'arithmétique de `_drawdown()`.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402

from app.core.settings import load_settings  # noqa: E402
from app.db.repository import PostgresRepository  # noqa: E402

MODE = "PAPER"
EQUITY = Decimal("9.60")
DAILY_PEAK = Decimal("12.00")
WEEKLY_PEAK = Decimal("16.00")
HWM = Decimal("24.00")

EXPECTED_DAILY = Decimal("0.20")    # 1 - 9,60 / 12,00
EXPECTED_WEEKLY = Decimal("0.40")   # 1 - 9,60 / 16,00
EXPECTED_HWM = Decimal("0.60")      # 1 - 9,60 / 24,00

# Exception sentinelle : elle force le rollback du contexte `conn.transaction()`.
class _Rollback(Exception):
    pass


def _frozen_rows(now: datetime) -> list[tuple[datetime, Decimal]]:
    """Cinq instantanés : HWM hors fenêtre, pic 7 j, pic du jour, valeur courante."""
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if now - day_start > timedelta(seconds=2):
        daily_peak_at = day_start + timedelta(seconds=1)
    else:
        daily_peak_at = now - timedelta(microseconds=1)
    return [
        (now - timedelta(days=10), HWM),
        (now - timedelta(days=3), WEEKLY_PEAK),
        (now - timedelta(days=2), Decimal("14.00")),
        (daily_peak_at, DAILY_PEAK),
        (now, EQUITY),
    ]


async def _run() -> int:
    settings = load_settings()
    try:
        conn = await asyncpg.connect(**settings.database().asyncpg_kwargs())
    except (OSError, asyncpg.PostgresError) as exc:
        print(f"base injoignable : {exc}", file=sys.stderr)
        return 3

    try:
        try:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM equity_snapshots WHERE mode = 'PAPER'"
                )
                await conn.execute(
                    "DELETE FROM profit_sweeps WHERE mode = 'PAPER'"
                )
                await conn.execute(
                    "UPDATE bot_state SET equity_hwm_quote = $1, "
                    "consecutive_losses = 0 WHERE mode = 'PAPER'",
                    HWM,
                )
                rows = _frozen_rows(datetime.now(timezone.utc))
                await conn.executemany(
                    "INSERT INTO equity_snapshots "
                    "(taken_at, mode, equity_quote, free_quote, "
                    "position_value_quote, source) "
                    "VALUES ($1, 'PAPER', $2, $2, 0, 'FROZEN_TEST')",
                    rows,
                )

                repo = PostgresRepository(conn)
                account = await repo.account(MODE, EQUITY)

                print(
                    f"jeu figé : équité {account.equity} · "
                    f"pic jour {DAILY_PEAK} → {account.daily_drawdown_pct * 100:.2f} % · "
                    f"pic semaine {WEEKLY_PEAK} → {account.weekly_drawdown_pct * 100:.2f} % · "
                    f"HWM {HWM} → {account.from_hwm_drawdown_pct * 100:.2f} %"
                )

                assert account.equity == EQUITY, account.equity
                assert account.daily_drawdown_pct == EXPECTED_DAILY, (
                    f"drawdown jour : {account.daily_drawdown_pct}, attendu {EXPECTED_DAILY}"
                )
                assert account.weekly_drawdown_pct == EXPECTED_WEEKLY, (
                    f"drawdown semaine : {account.weekly_drawdown_pct}, "
                    f"attendu {EXPECTED_WEEKLY}"
                )
                assert account.from_hwm_drawdown_pct == EXPECTED_HWM, (
                    f"drawdown HWM : {account.from_hwm_drawdown_pct}, "
                    f"attendu {EXPECTED_HWM}"
                )
                raise _Rollback
        except _Rollback:
            pass
    finally:
        await conn.close()

    print("OK   les trois drawdowns correspondent au calcul manuel ; transaction annulée")
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
