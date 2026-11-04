"""Authentification de l'API d'administration.

Trois couches, volontairement indépendantes :

1. **Mot de passe** : scrypt (stdlib) avec sel aléatoire, comparaison à temps
   constant. Aucun mot de passe en clair n'est stocké ni journalisé.
2. **Session** : JWT HS256 signé localement, 15 minutes, émis et vérifiés par
   l'API. Le secret doit faire au moins 32 caractères, sinon l'API refuse de servir.
3. **Second facteur** : TOTP (RFC 6238), fenêtre ±1 pas de 30 s, avec anti-rejeu.

Fail-closed : une configuration incomplète lève `AuthConfigError` au démarrage.
Une API d'administration ouverte par oubli est plus dangereuse qu'une API absente.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timezone
from typing import Any

import jwt
import pyotp

__all__ = [
    "AttemptLimiter",
    "AuthConfigError",
    "AuthError",
    "TokenError",
    "TotpVerifier",
    "create_token",
    "decode_token",
    "hash_password",
    "require_secrets",
    "verify_password",
]

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
DKLEN = 32
TOKEN_TTL_SECONDS = 900
TOKEN_ISSUER = "trading-ninja"
MIN_JWT_SECRET_CHARS = 32


class AuthError(RuntimeError):
    """Échec d'authentification générique."""


class AuthConfigError(AuthError):
    """Configuration d'authentification absente ou invalide."""


class TokenError(AuthError):
    """Jeton absent, malformé, expiré ou mal signé."""


# ---------------------------------------------------------------------------
# Mot de passe
# ---------------------------------------------------------------------------


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if not password:
        raise ValueError("mot de passe vide")
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        dklen=DKLEN,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(derived)}"


def verify_password(password: str, stored: str) -> bool:
    """Vrai si le mot de passe correspond. Toute anomalie vaut faux."""
    try:
        algorithm, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(hash_b64, validate=True)
        derived = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, expected)


# ---------------------------------------------------------------------------
# Session JWT
# ---------------------------------------------------------------------------


def create_token(secret: str, *, subject: str = "admin",
                 ttl_seconds: int = TOKEN_TTL_SECONDS,
                 now: datetime | None = None) -> str:
    if len(secret) < MIN_JWT_SECRET_CHARS:
        raise AuthConfigError(
            f"JWT_SECRET_KEY doit faire au moins {MIN_JWT_SECRET_CHARS} caractères"
        )
    moment = now or datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iss": TOKEN_ISSUER,
        "iat": int(moment.timestamp()),
        "exp": int(moment.timestamp()) + ttl_seconds,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(secret: str, token: str) -> dict[str, Any]:
    if len(secret) < MIN_JWT_SECRET_CHARS:
        raise AuthConfigError(
            f"JWT_SECRET_KEY doit faire au moins {MIN_JWT_SECRET_CHARS} caractères"
        )
    try:
        return jwt.decode(
            token, secret, algorithms=["HS256"], issuer=TOKEN_ISSUER,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("session expirée") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"session invalide : {exc}") from exc


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------


class TotpVerifier:
    """Vérifie un code TOTP et refuse sa réutilisation.

    pyotp accepte la fenêtre ±1 pas ; sans anti-rejeu, un code intercepté reste
    valable jusqu'à 90 secondes. On mémorise donc les codes acceptés, avec une
    expiration courte.
    """

    def __init__(self, secret: str, *, window: int = 1, replay_ttl: int = 90):
        if not secret:
            raise AuthConfigError("TOTP_SECRET manquant")
        try:
            self._totp = pyotp.TOTP(secret)
            self._totp.now()  # valide le base32 à la construction
        except Exception as exc:  # noqa: BLE001 — base32 invalide = config invalide
            raise AuthConfigError(f"TOTP_SECRET invalide : {exc}") from exc
        self._window = window
        self._replay_ttl = replay_ttl
        self._used: dict[str, float] = {}

    def verify(self, code: str, *, now: datetime | None = None,
               consume: bool = True) -> bool:
        code = (code or "").strip()
        if not code.isdigit() or len(code) != 6:
            return False
        moment = now or datetime.now(timezone.utc)
        epoch = moment.timestamp()
        self._used = {key: exp for key, exp in self._used.items() if exp > epoch}
        if code in self._used:
            return False
        if not self._totp.verify(code, for_time=moment, valid_window=self._window):
            return False
        if consume:
            self._used[code] = epoch + self._replay_ttl
        return True


# ---------------------------------------------------------------------------
# Limitation des tentatives
# ---------------------------------------------------------------------------


class AttemptLimiter:
    """Fenêtre glissante en mémoire, par clé (adresse IP)."""

    def __init__(self, max_attempts: int = 5, window_seconds: int = 300):
        self._max = max_attempts
        self._window = window_seconds
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str, epoch: float) -> list[float]:
        return [t for t in self._failures.get(key, []) if epoch - t < self._window]

    def allow(self, key: str, *, now: float | None = None) -> bool:
        epoch = time.time() if now is None else now
        return len(self._recent(key, epoch)) < self._max

    def fail(self, key: str, *, now: float | None = None) -> None:
        epoch = time.time() if now is None else now
        self._failures[key] = self._recent(key, epoch) + [epoch]

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)


def require_secrets(jwt_secret: str, totp_secret: str, password_hash: str) -> None:
    """Refuse une configuration incomplète, au démarrage plutôt qu'à l'usage."""
    if len(jwt_secret) < MIN_JWT_SECRET_CHARS:
        raise AuthConfigError(
            f"JWT_SECRET_KEY absent ou trop court (< {MIN_JWT_SECRET_CHARS} caractères)"
        )
    if not totp_secret:
        raise AuthConfigError("TOTP_SECRET absent")
    try:
        pyotp.TOTP(totp_secret).now()
    except Exception as exc:  # noqa: BLE001
        raise AuthConfigError(f"TOTP_SECRET invalide : {exc}") from exc
    parts = password_hash.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        raise AuthConfigError(
            "ADMIN_PASSWORD_HASH absent ou invalide "
            "(générer avec scripts/hash_password.py)"
        )
