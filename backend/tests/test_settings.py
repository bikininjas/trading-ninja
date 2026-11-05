"""Tests du chargement de configuration : presets, écrasements, verrous d'armement."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.core.settings import RISK_PRESETS, load_settings
from app.engine.risk_gate import EntryMode, SwingExitMode


def test_defaults_are_paper_and_do_not_touch_real_money(tmp_path: Path):
    s = load_settings(env_file=tmp_path / "absent.env", overrides={})
    assert s.mode == "paper"
    assert s.is_sandbox is True
    assert s.risk_profile == "prudent"
    assert s.risk.risk_per_trade_pct == Decimal("0.005")
    assert s.risk.stop_atr_multiple == Decimal("2.5")
    assert s.risk.max_daily_drawdown_pct == Decimal("0.02")


def test_profile_preset_changes_several_knobs_at_once(tmp_path: Path):
    s = load_settings(env_file=tmp_path / "absent.env", overrides={"RISK_PROFILE": "aggressive"})
    assert s.risk.risk_per_trade_pct == Decimal("0.01")
    assert s.risk.max_notional_pct == Decimal("0.50")


def test_individual_override_beats_preset(tmp_path: Path):
    s = load_settings(
        env_file=tmp_path / "absent.env",
        overrides={"RISK_PROFILE": "aggressive", "RISK_PER_TRADE_PCT": "0.003"},
    )
    assert s.risk.risk_per_trade_pct == Decimal("0.003")
    assert s.risk.max_notional_pct == Decimal("0.50")


def test_swing_preset_enables_d1_and_small_account_allocation(tmp_path: Path):
    s = load_settings(
        env_file=tmp_path / "absent.env", overrides={"RISK_PROFILE": "swing"}
    )
    assert s.risk.entry_mode is EntryMode.SWING_MACRO_D1
    assert s.exchange.daily_timeframe_decision == "1d"
    assert s.risk.max_notional_pct == Decimal("1.0")
    assert s.risk.small_account_full_allocation is True


def test_swing_mode_refuses_a_non_daily_decision_timeframe(tmp_path: Path):
    with pytest.raises(RuntimeError, match="DECISION_TIMEFRAME=1d"):
        load_settings(
            env_file=tmp_path / "absent.env",
            overrides={
                "ENTRY_MODE": "SWING_MACRO_D1",
                "DECISION_TIMEFRAME": "4h",
            },
        )


def test_live_mode_requires_two_deliberate_gestures(tmp_path: Path):
    with pytest.raises(RuntimeError, match="I_UNDERSTAND_REAL_MONEY"):
        load_settings(
            env_file=tmp_path / "absent.env",
            overrides={"TRADING_MODE": "live", "EXCHANGE_SANDBOX_MODE": "false"},
        )

    with pytest.raises(RuntimeError, match="incompatible"):
        load_settings(
            env_file=tmp_path / "absent.env",
            overrides={"TRADING_MODE": "live", "I_UNDERSTAND_REAL_MONEY": "true"},
        )

    with pytest.raises(RuntimeError, match="clés API"):
        load_settings(
            env_file=tmp_path / "absent.env",
            overrides={
                "TRADING_MODE": "live",
                "EXCHANGE_SANDBOX_MODE": "false",
                "I_UNDERSTAND_REAL_MONEY": "true",
            },
        )


def test_live_mode_accepted_only_when_fully_configured(tmp_path: Path):
    s = load_settings(
        env_file=tmp_path / "absent.env",
        overrides={
            "TRADING_MODE": "live",
            "EXCHANGE_SANDBOX_MODE": "false",
            "I_UNDERSTAND_REAL_MONEY": "true",
            "EXCHANGE_API_KEY": "k",
            "EXCHANGE_SECRET": "s",
        },
    )
    assert s.is_live
    assert s.is_sandbox is False


def test_bad_values_fail_loudly(tmp_path: Path):
    with pytest.raises(RuntimeError, match="TRADING_MODE"):
        load_settings(env_file=tmp_path / "absent.env", overrides={"TRADING_MODE": "yolo"})
    with pytest.raises(RuntimeError, match="RISK_PROFILE"):
        load_settings(env_file=tmp_path / "absent.env", overrides={"RISK_PROFILE": "daredevil"})
    with pytest.raises(ValueError):
        load_settings(env_file=tmp_path / "absent.env", overrides={"RISK_PER_TRADE_PCT": "0.9"})


def test_dotenv_file_is_read(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("RISK_PROFILE=standard\nTRADING_SYMBOL=ETH/USDT\n", encoding="utf-8")
    s = load_settings(env_file=env, overrides={})
    assert s.risk_profile == "standard"
    assert s.exchange.symbol == "ETH/USDT"


def test_binance_key_aliases_are_accepted(tmp_path: Path):
    # Le fichier .env.local nomme les clés BINANCE_* ; le code doit les trouver
    # sans qu'on ait à les renommer.
    s = load_settings(
        env_file=tmp_path / "absent.env",
        overrides={"BINANCE_API_KEY": "pub", "BINANCE_SECRET_KEY": "priv"},
    )
    assert s.exchange.api_key == "pub"
    assert s.exchange.secret == "priv"


def test_explicit_exchange_names_win_over_aliases(tmp_path: Path):
    s = load_settings(
        env_file=tmp_path / "absent.env",
        overrides={
            "EXCHANGE_API_KEY": "explicit",
            "BINANCE_API_KEY": "alias",
            "EXCHANGE_SECRET": "explicit-s",
            "BINANCE_SECRET_KEY": "alias-s",
        },
    )
    assert s.exchange.api_key == "explicit"
    assert s.exchange.secret == "explicit-s"


def test_secrets_never_appear_in_repr(tmp_path: Path):
    s = load_settings(
        env_file=tmp_path / "absent.env",
        overrides={"BINANCE_API_KEY": "AKIA-VISIBLE?", "BINANCE_SECRET_KEY": "SHOULD-NOT-LEAK"},
    )
    text = repr(s)
    assert "SHOULD-NOT-LEAK" not in text
    assert "AKIA-VISIBLE?" not in text


# ---------------------------------------------------------------------------
# Cohérence mode / sandbox
# (régression trouvée par un audit : « testnet » + sandbox=false chargeait sans
#  erreur, étiquetait TESTNET et parlait au réel)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["paper", "testnet"])
def test_paper_and_testnet_refuse_a_disabled_sandbox(tmp_path: Path, mode):
    with pytest.raises(RuntimeError, match="EXCHANGE_SANDBOX_MODE=true"):
        load_settings(
            env_file=tmp_path / "absent.env",
            overrides={"TRADING_MODE": mode, "EXCHANGE_SANDBOX_MODE": "false"},
        )


def test_execution_mode_follows_the_declared_mode(tmp_path: Path):
    base = {"EXCHANGE_SANDBOX_MODE": "true"}
    assert load_settings(env_file=tmp_path / "absent.env", overrides={**base}).execution_mode == "PAPER"
    assert (
        load_settings(env_file=tmp_path / "absent.env", overrides={**base, "TRADING_MODE": "testnet"}).execution_mode
        == "TESTNET"
    )


# ---------------------------------------------------------------------------
# Kraken : clés, frais, paires EUR
# ---------------------------------------------------------------------------


def test_kraken_key_aliases_are_accepted(tmp_path: Path):
    s = load_settings(
        env_file=tmp_path / "absent.env",
        overrides={
            "EXCHANGE_ID": "kraken",
            "KRAKEN_API_KEY": "krak-pub",
            "KRAKEN_SECRET": "krak-priv",
        },
    )
    assert s.exchange.exchange_id == "kraken"
    assert s.exchange.api_key == "krak-pub"
    assert s.exchange.secret == "krak-priv"


def test_kraken_does_not_borrow_binance_keys(tmp_path: Path):
    # Un fichier de secrets peut contenir les deux jeux : Kraken ne doit jamais
    # signer avec une clé Binance.
    s = load_settings(
        env_file=tmp_path / "absent.env",
        overrides={
            "EXCHANGE_ID": "kraken",
            "BINANCE_API_KEY": "binance-pub",
            "BINANCE_SECRET_KEY": "binance-priv",
        },
    )
    assert s.exchange.api_key == ""
    assert s.exchange.secret == ""


def test_explicit_exchange_key_still_wins_on_kraken(tmp_path: Path):
    s = load_settings(
        env_file=tmp_path / "absent.env",
        overrides={
            "EXCHANGE_ID": "kraken",
            "EXCHANGE_API_KEY": "explicit",
            "KRAKEN_API_KEY": "alias",
        },
    )
    assert s.exchange.api_key == "explicit"


def test_fee_buffer_default_matches_the_exchange_taker_fee(tmp_path: Path):
    binance = load_settings(env_file=tmp_path / "absent.env", overrides={"EXCHANGE_ID": "binance"})
    kraken = load_settings(env_file=tmp_path / "absent.env", overrides={"EXCHANGE_ID": "kraken"})
    assert binance.risk.fee_buffer_pct == Decimal("0.002")   # 2 × 0,10 %
    assert kraken.risk.fee_buffer_pct == Decimal("0.016")    # 2 × 0,80 %
    explicit = load_settings(
        env_file=tmp_path / "absent.env",
        overrides={"EXCHANGE_ID": "kraken", "FEE_BUFFER_PCT": "0.01"},
    )
    assert explicit.risk.fee_buffer_pct == Decimal("0.01")


def test_taker_fee_defaults_to_the_current_kraken_tier(tmp_path: Path):
    binance = load_settings(env_file=tmp_path / "absent.env", overrides={"EXCHANGE_ID": "binance"})
    kraken = load_settings(env_file=tmp_path / "absent.env", overrides={"EXCHANGE_ID": "kraken"})
    assert binance.exchange.taker_fee == Decimal("0.001")
    assert kraken.exchange.taker_fee == Decimal("0.008")
    assert kraken.exchange.maker_fee == Decimal("0.004")
    explicit = load_settings(
        env_file=tmp_path / "absent.env",
        overrides={"EXCHANGE_ID": "kraken", "TAKER_FEE_PCT": "0.006"},
    )
    assert explicit.exchange.taker_fee == Decimal("0.006")


def test_an_absurd_taker_fee_is_refused(tmp_path: Path):
    with pytest.raises(RuntimeError, match="TAKER_FEE_PCT"):
        load_settings(
            env_file=tmp_path / "absent.env",
            overrides={"EXCHANGE_ID": "kraken", "TAKER_FEE_PCT": "0.10"},
        )


def test_quote_currency_is_derived_from_the_symbol(tmp_path: Path):
    s = load_settings(
        env_file=tmp_path / "absent.env",
        overrides={"EXCHANGE_ID": "kraken", "TRADING_SYMBOL": "BTC/EUR"},
    )
    assert s.exchange.quote_currency == "EUR"


def test_declared_symbols_keep_the_primary_symbol_first(tmp_path: Path):
    s = load_settings(
        env_file=tmp_path / "absent.env",
        overrides={
            "EXCHANGE_ID": "kraken",
            "TRADING_SYMBOL": "BTC/EUR",
            "TRADING_SYMBOLS": "ETH/EUR, SOL/EUR , BTC/USDT",
        },
    )
    assert s.exchange.allowed_symbols == ("BTC/EUR", "ETH/EUR", "SOL/EUR", "BTC/USDT")


def test_unknown_exchange_is_refused(tmp_path: Path):
    with pytest.raises(RuntimeError, match="EXCHANGE_ID"):
        load_settings(env_file=tmp_path / "absent.env", overrides={"EXCHANGE_ID": "ftx"})


# ---------------------------------------------------------------------------
# Profil micro : rendre un compte de quelques euros mécaniquement tradable
# ---------------------------------------------------------------------------


def test_micro_profile_loads_for_small_accounts(tmp_path: Path):
    s = load_settings(env_file=tmp_path / "absent.env", overrides={"RISK_PROFILE": "micro"})
    assert s.risk_profile == "micro"
    assert s.risk.risk_per_trade_pct == Decimal("0.0175")
    assert s.risk.max_notional_pct == Decimal("0.50")
    assert s.risk.min_confidence == Decimal("0.60")


@pytest.mark.parametrize("profile", sorted(RISK_PRESETS))
def test_daily_breaker_leaves_room_for_more_than_one_loss(tmp_path: Path, profile: str):
    # Si le drawdown journalier tombe sous deux fois le risque par trade, une seule
    # perte verrouille le bot jusqu'au lendemain : le profil est incohérent.
    s = load_settings(env_file=tmp_path / "absent.env", overrides={"RISK_PROFILE": profile})
    assert s.risk.max_daily_drawdown_pct >= s.risk.risk_per_trade_pct * 2


def test_invalid_sweep_ratio_is_refused_at_startup(tmp_path: Path):
    with pytest.raises(RuntimeError, match="SWEEP_RATIO"):
        load_settings(env_file=tmp_path / "absent.env", overrides={"SWEEP_RATIO": "0"})


def test_negative_sweep_minimum_is_refused(tmp_path: Path):
    with pytest.raises(RuntimeError, match="SWEEP_MIN_TRANSFER_QUOTE"):
        load_settings(
            env_file=tmp_path / "absent.env",
            overrides={"SWEEP_MIN_TRANSFER_QUOTE": "-1"},
        )


def test_negative_paper_slippage_is_refused(tmp_path: Path):
    with pytest.raises(RuntimeError, match="PAPER_SLIPPAGE_BPS"):
        load_settings(env_file=tmp_path / "absent.env", overrides={"PAPER_SLIPPAGE_BPS": "-5"})


def test_entry_mode_defaults_to_the_historical_setup(tmp_path: Path):
    s = load_settings(env_file=tmp_path / "absent.env", overrides={})
    assert s.risk.entry_mode == EntryMode.EMA50_H4


def test_entry_mode_can_be_switched_by_environment(tmp_path: Path):
    s = load_settings(
        env_file=tmp_path / "absent.env", overrides={"ENTRY_MODE": "PULLBACK_H1"}
    )
    assert s.risk.entry_mode is EntryMode.PULLBACK_H1


def test_unknown_entry_mode_is_refused_at_startup(tmp_path: Path):
    with pytest.raises(RuntimeError, match="ENTRY_MODE"):
        load_settings(env_file=tmp_path / "absent.env", overrides={"ENTRY_MODE": "MOMENTUM"})


# ---------------------------------------------------------------------------
# Sortie swing : EMA20 par défaut, Chandelier en option (D-061)
# ---------------------------------------------------------------------------


def test_swing_exit_defaults_to_ema20(tmp_path: Path):
    s = load_settings(env_file=tmp_path / "absent.env", overrides={"RISK_PROFILE": "swing"})
    assert s.risk.swing_exit_mode is SwingExitMode.EMA20
    assert s.risk.swing_chandelier_period == 20
    assert s.risk.swing_chandelier_atr_multiple == Decimal("3.0")


def test_swing_exit_mode_accepts_chandelier_case_insensitively(tmp_path: Path):
    s = load_settings(
        env_file=tmp_path / "absent.env",
        overrides={"RISK_PROFILE": "swing", "SWING_EXIT_MODE": "chandelier"},
    )
    assert s.risk.swing_exit_mode is SwingExitMode.CHANDELIER


def test_swing_chandelier_parameters_are_configurable(tmp_path: Path):
    s = load_settings(
        env_file=tmp_path / "absent.env",
        overrides={
            "RISK_PROFILE": "swing",
            "SWING_CHANDELIER_PERIOD": "30",
            "SWING_CHANDELIER_ATR_MULTIPLE": "2.5",
        },
    )
    assert s.risk.swing_chandelier_period == 30
    assert s.risk.swing_chandelier_atr_multiple == Decimal("2.5")


def test_unknown_swing_exit_mode_is_refused_at_startup(tmp_path: Path):
    with pytest.raises(RuntimeError, match="SWING_EXIT_MODE"):
        load_settings(
            env_file=tmp_path / "absent.env",
            overrides={"RISK_PROFILE": "swing", "SWING_EXIT_MODE": "supertrend"},
        )
