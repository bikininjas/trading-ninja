#!/usr/bin/env python3
"""Kill-switch autonome — solde la position et halte le bot, backend arrêté ou non.

    .venv/bin/python scripts/kill_switch.py --dry-run   # montre ce qui serait fait
    .venv/bin/python scripts/kill_switch.py             # liquide et halte

Le script ne dépend pas du processus principal : il ouvre sa propre connexion à la
base et parle directement à l'exchange (prix public). Il fonctionne donc quand le
backend est mort, ce qui est précisément le cas d'usage.

En mode `paper`, il liquide la position du ledger papier au prix courant, annule le
stop armé, écrit l'ordre `EMERGENCY_EXIT`, passe la position en
`EMERGENCY_LIQUIDATED` et force `bot_state` en `EMERGENCY_HALT`.

En mode `live`, il envoie une vraie vente au marché après avoir annulé les protections,
puis halte le bot. Il exige `--confirm-live` : une liquidation réelle ne doit jamais
partir d'une commande tapée par erreur.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.settings import load_settings  # noqa: E402
from app.db.repository import PostgresRepository  # noqa: E402
from app.exchange.kraken import build_exchange  # noqa: E402
from app.execution.factory import make_broker  # noqa: E402
from app.runtime.recovery import liquidate_open_positions  # noqa: E402


async def _run(dry_run: bool, confirm_live: bool) -> int:
    settings = load_settings()
    mode = settings.execution_mode

    if settings.mode not in {"paper", "live"}:
        print(
            "REFUS : le mode testnet n'existe pas chez Kraken spot dans CCXT 4.5.91. "
            "Utiliser paper, ou live avec les deux verrous.",
            file=sys.stderr,
        )
        return 2
    if settings.mode == "live" and not dry_run and not confirm_live:
        print(
            "REFUS : une liquidation réelle exige --confirm-live. "
            "Relancer avec --confirm-live après avoir vérifié la position.",
            file=sys.stderr,
        )
        return 2

    repo = await PostgresRepository.connect(settings.database())
    try:
        state, _ = await repo.state(mode)
        position = await repo.open_position(mode)
        orders = await repo.open_orders(mode)

        if dry_run:
            print(f"mode {mode} · état {state}")
            print("position ouverte :", "aucune" if position is None else
                  f"{position.symbol} {position.amount} @ {position.entry_price} "
                  f"(stop {position.stop_loss_price})")
            print(f"ordres ouverts : {len(orders)}")
            for order in orders:
                print(f"  {order['symbol']} {order['purpose']} {order['status']}")
            print("→ rien n'a été modifié (--dry-run)")
            return 0

        # Kraken impose des nonces croissants par clé : une seconde paire dédiée
        # permet au kill-switch de signer pendant que le bot tourne.
        if settings.exchange.kill_api_key:
            api_key, secret = settings.exchange.kill_api_key, settings.exchange.kill_secret
            print("clé : kill-switch dédiée (KILL_SWITCH_API_KEY)")
        else:
            api_key, secret = settings.exchange.api_key, settings.exchange.secret
            print(
                "AVERTISSEMENT : KILL_SWITCH_API_KEY absent — la clé du bot est "
                "réutilisée ; une collision de nonce est possible si le bot tourne.",
                file=sys.stderr,
            )
        exchange = build_exchange(api_key=api_key, secret=secret)
        exchange.load_markets()
        broker = make_broker(settings, exchange, settings.exchange.allowed_symbols)

        results = await liquidate_open_positions(
            repo, exchange, broker, mode,
            quote_currency=settings.exchange.quote_currency,
            starting_equity=settings.paper_start_equity,
            capital_policy=settings.capital_policy,
        )
        if not results:
            print("aucune position ouverte à liquider.")
        for item in results:
            print(f"LIQUIDÉ {item.symbol} {item.amount} @ {item.exit_price} · "
                  f"frais {item.fees_quote:.6f} · net {item.net_pnl_quote:.6f} "
                  f"· {item.orders_canceled} ordre(s) annulé(s)")
        new_state, _ = await repo.state(mode)
        print(f"état : {new_state}")
        return 0
    finally:
        await repo.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Kill-switch — solde et halte")
    parser.add_argument("--dry-run", action="store_true",
                        help="affiche la position et les ordres sans rien modifier")
    parser.add_argument("--confirm-live", action="store_true",
                        help="confirme une vente réelle en mode live")
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args.dry_run, args.confirm_live))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
