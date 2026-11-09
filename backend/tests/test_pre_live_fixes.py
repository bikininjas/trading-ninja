"""Contre-exemples pré live convertis en contrats à respecter."""

from decimal import Decimal as D
import uuid
from unittest.mock import patch
import pytest
from tests.test_insights import config, FeatureLedger, NOW
from app.db.repository import PositionRow
from app.api.main import _default_kill_switch


@pytest.mark.asyncio
async def test_paper_kill_switch_never_contacts_exchange_and_keeps_stop_owner_running():
    ledger = FeatureLedger()
    ledger.current = "IN_POSITION"
    ledger.position = PositionRow(
        uuid.uuid4(), "BTC/EUR", D(100), D(1), D(95), None, NOW, D(5)
    )
    with patch(
        "app.exchange.kraken.build_exchange",
        side_effect=AssertionError("forbidden exchange call"),
    ):
        result = await _default_kill_switch(ledger, config())
    assert result["canceled_orders"] == [] and not ledger.control["enabled"]
    assert ledger.position.amount == 1 and ledger.current == "IN_POSITION"


def test_factory_cannot_promote_paper_configuration_to_live():
    from app.execution.factory import make_broker

    with pytest.raises(RuntimeError, match="non armée"):
        make_broker(config(), object(), mode="live")
