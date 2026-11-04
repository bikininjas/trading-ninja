"""Tests de la couche d'authentification : scrypt, JWT, TOTP, anti-rejeu."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pyotp
import pytest

from app.api.security import (
    MIN_JWT_SECRET_CHARS, AttemptLimiter, AuthConfigError, TokenError, TotpVerifier,
    create_token, decode_token, hash_password, require_secrets, verify_password,
)

SECRET = "JBSWY3DPEHPK3PXP"
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


def test_password_roundtrip():
    stored = hash_password("correct horse battery")
    assert stored.startswith("scrypt$")
    assert verify_password("correct horse battery", stored) is True
    assert verify_password("wrong", stored) is False


def test_password_hash_is_salted():
    assert hash_password("same") != hash_password("same")


@pytest.mark.parametrize("stored", ["", "bcrypt$1$2", "scrypt$bad", "scrypt$1$2$3$4$5"])
def test_malformed_hash_is_refused(stored):
    assert verify_password("anything", stored) is False


def test_token_roundtrip_and_expiry():
    secret = "k" * MIN_JWT_SECRET_CHARS
    now = datetime.now(timezone.utc)
    token = create_token(secret, now=now, ttl_seconds=300)
    payload = decode_token(secret, token)
    assert payload["sub"] == "admin"

    expired = create_token(secret, now=now - timedelta(hours=1), ttl_seconds=60)
    with pytest.raises(TokenError, match="expirée"):
        decode_token(secret, expired)


def test_token_signed_with_another_key_is_refused():
    token = create_token("a" * 40, now=datetime.now(timezone.utc))
    with pytest.raises(TokenError):
        decode_token("b" * 40, token)


def test_short_jwt_secret_is_refused():
    with pytest.raises(AuthConfigError):
        create_token("short")


def test_totp_accepts_the_current_code_once():
    verifier = TotpVerifier(SECRET)
    code = pyotp.TOTP(SECRET).at(NOW)
    assert verifier.verify(code, now=NOW) is True
    assert verifier.verify(code, now=NOW) is False  # rejeu


def test_totp_accepts_the_neighbouring_step_but_not_a_far_one():
    verifier = TotpVerifier(SECRET)
    next_code = pyotp.TOTP(SECRET).at(NOW + timedelta(seconds=30))
    assert verifier.verify(next_code, now=NOW) is True
    old_code = pyotp.TOTP(SECRET).at(NOW - timedelta(seconds=300))
    assert verifier.verify(old_code, now=NOW) is False


@pytest.mark.parametrize("code", ["", "12345", "abcdef", "000000"])
def test_totp_refuses_malformed_or_wrong_codes(code):
    assert TotpVerifier(SECRET).verify(code, now=NOW) is False


def test_totp_invalid_secret_fails_closed():
    with pytest.raises(AuthConfigError):
        TotpVerifier("not-base32-!!!")


def test_limiter_blocks_then_releases():
    limiter = AttemptLimiter(max_attempts=5, window_seconds=300)
    for i in range(5):
        assert limiter.allow("ip", now=i) is True
        limiter.fail("ip", now=i)
    assert limiter.allow("ip", now=5) is False
    assert limiter.allow("ip", now=301) is True


def test_require_secrets_accepts_a_complete_configuration():
    require_secrets("k" * 48, SECRET, hash_password("correct horse battery"))


@pytest.mark.parametrize("jwt_secret,totp_secret,password_hash", [
    ("short", SECRET, "scrypt$16384$8$1$AA==$AA=="),
    ("k" * 48, "", "scrypt$16384$8$1$AA==$AA=="),
    ("k" * 48, SECRET, ""),
    ("k" * 48, SECRET, "plaintext"),
])
def test_require_secrets_refuses_incomplete_configuration(jwt_secret, totp_secret,
                                                          password_hash):
    with pytest.raises(AuthConfigError):
        require_secrets(jwt_secret, totp_secret, password_hash)
