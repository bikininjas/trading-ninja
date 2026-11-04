"""Génère un secret TOTP, son QR code et l'URI de provisionnement.

    .venv/bin/python scripts/totp_setup.py

Compatible Authy (et Aegis, FreeOTP, Google Authenticator) : TOTP standard,
6 chiffres, 30 secondes, SHA-1. Deux façons d'enrôler le compte :

- scanner le QR code affiché dans le terminal (Authy → Ajouter un compte) ;
- ou saisir la clé manuellement (Authy → Saisir la clé manuellement).

Coller ensuite la ligne `TOTP_SECRET=...` dans `backend/.env`. Le secret n'est
affiché qu'une fois : le terminal est local, pas un journal.
"""

from __future__ import annotations

import pyotp

try:  # le QR n'est pas indispensable : la saisie manuelle fonctionne toujours
    import qrcode
except ImportError:  # pragma: no cover - dépend de l'environnement
    qrcode = None


def main() -> int:
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name="admin@trading-ninja", issuer_name="Trading Ninja")

    print("1. Ajouter cette ligne à backend/.env :\n")
    print(f"TOTP_SECRET={secret}\n")

    if qrcode is not None:
        print("2. Scanner ce QR code avec Authy (Ajouter un compte → Scanner) :\n")
        qr = qrcode.QRCode(border=1)
        qr.add_data(uri)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    else:
        print("2. Installer `qrcode` pour afficher un QR, ou saisir la clé manuellement.")

    print("\n3. Ou saisir la clé manuellement dans Authy :")
    print(f"   Compte : Trading Ninja (admin@trading-ninja)")
    print(f"   Clé    : {secret}   (6 chiffres, 30 s)")
    print(f"\nURI complète : {uri}")
    print(f"\nCode courant (à usage unique) : {totp.now()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
