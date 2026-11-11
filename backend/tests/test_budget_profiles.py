from dataclasses import replace
from decimal import Decimal as D

import pytest

from app.core.settings import load_settings
from app.api.settings_store import build_payload, friendly_settings, validate_payload
from app.engine.affordability import assess
from app.engine.risk_gate import ExchangeConstraints, evaluate
from tests.test_risk_gate import market, account, intent

CONSTRAINTS = ExchangeConstraints(amount_step=D('.000001'), min_amount=D('.04'),
                                  min_notional=D('.45'), price_tick=D('.01'))


def config(profile, **overrides):
    return load_settings(use_environment=False, overrides={
        'RISK_PROFILE': profile, 'TRADING_MODE': 'paper', 'EXCHANGE_ID': 'kraken',
        'TAKER_FEE_PCT': '.008', 'PAPER_SLIPPAGE_BPS': '5', **overrides})


@pytest.mark.parametrize('profile,budget,possible', [
    ('petit_budget', '25', True), ('micro_budget', '9.37', True),
    ('nano_budget', '5', True), ('nano_budget', '3', False),
    ('nano_budget', '1', False), ('nano_budget', '.5', False),
    ('micro_budget', '5', False),
])
def test_budget_profile_financing_matches_real_gateway(profile, budget, possible):
    cfg = config(profile).risk
    verdict = assess('BTC/EUR', price=D(100), atr_14=D('1.2'), constraints=CONSTRAINTS,
                     config=cfg, equity=D(budget))
    decision = evaluate(intent(), market(atr='1.2'),
                        account(equity_quote=D(budget), free_quote=D(budget)), CONSTRAINTS, cfg)
    assert verdict.affordable == decision.approved == possible
    if decision.approved:
        assert decision.amount >= CONSTRAINTS.min_amount
        assert decision.estimated_total_loss_quote <= D(budget) * cfg.risk_per_trade_pct
        assert decision.notional_quote <= D(budget) * cfg.max_notional_pct
    assert cfg.cost_aware and cfg.max_open_positions == 1


@pytest.mark.parametrize('profile', ['petit_budget', 'micro_budget', 'nano_budget'])
def test_new_profiles_are_manual_paper_only_and_never_raise_a_reduced_risk(profile):
    with pytest.raises(RuntimeError, match='PAPER'):
        config(profile, TRADING_MODE='live')
    cfg = config(profile, RISK_PER_TRADE_PCT='.001').risk
    assert cfg.small_account_max_risk_pct <= D('.001')
    decision = evaluate(intent(), market(atr='1.2'), account(equity_quote=D(5), free_quote=D(5)), CONSTRAINTS, cfg)
    assert not decision.approved


def test_selecting_budget_profile_clears_old_overrides_and_overrides_startup_risk():
    payload = build_payload({'RISK_PROFILE': 'prudent', 'RISK_PER_TRADE_PCT': '.0075',
        'MAX_NOTIONAL_PCT': '.30', 'MIN_CONFIDENCE': '.99', 'MAX_WEEKLY_DRAWDOWN_PCT': '.01'},
        {'profile': 'micro_budget'})
    candidate = validate_payload(payload, loader=lambda overrides: config('prudent', **overrides))
    assert candidate.risk.risk_per_trade_pct == D('.03')
    assert candidate.risk.max_notional_pct == D('.50')
    assert candidate.risk.min_confidence == D('.60')
    assert candidate.risk.max_weekly_drawdown_pct == D('.10')
    assert candidate.risk.take_profit_r_multiple == D('3.5')
    assert any(p['key'] == 'nano_budget' and '100 %' in p['description']
               for p in friendly_settings(candidate)['profiles'])


def test_nano_does_not_remove_net_cost_filter_or_increase_risk_after_a_loss():
    cfg = config('nano_budget').risk
    bad_costs = replace(cfg, execution_fee_pct=D('.03'))
    assert not evaluate(intent(), market(atr='1.2'), account(equity_quote=D(5), free_quote=D(5)), CONSTRAINTS, bad_costs).approved
    assert cfg.effective_risk_pct(1) <= cfg.risk_per_trade_pct
    assert cfg.effective_risk_pct(2) <= cfg.effective_risk_pct(1)


def test_reduced_nano_exposure_is_respected_by_diagnostic_and_execution():
    cfg = config('nano_budget', MAX_NOTIONAL_PCT='.50').risk
    verdict = assess('BTC/EUR', price=D(100), atr_14=D('1.2'), constraints=CONSTRAINTS,
                     config=cfg, equity=D(5))
    decision = evaluate(intent(), market(atr='1.2'), account(equity_quote=D(5), free_quote=D(5)), CONSTRAINTS, cfg)
    assert not verdict.affordable and not decision.approved
