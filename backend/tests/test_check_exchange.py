"""Tests du verdict de la sonde exchange — la logique qui protège la clé.

Aucun réseau ici : `verdict()` est une fonction pure sur un dictionnaire de réponse.
Les tests figent la règle de D-018 — `apiRestrictions.enableWithdrawals` fait autorité,
`canWithdraw` au niveau du compte ne décrit pas la permission de la clé — parce qu'une
régression à cet endroit rendrait le garde-fou soit aveugle, soit hystérique.
"""

from __future__ import annotations

from scripts.check_exchange import verdict

RESTRICTIONS_MALINES = {
    "enableReading": True,
    "enableSpotAndMarginTrading": True,
    "enableWithdrawals": True,
    "ipRestrict": True,
}

RESTRICTIONS_SAINES = {**RESTRICTIONS_MALINES, "enableWithdrawals": False}


def compte(**kwargs) -> dict:
    base = {
        "ok": True,
        "can_trade": True,
        "can_withdraw": True,
        "can_deposit": True,
        "restrictions": RESTRICTIONS_SAINES,
    }
    base.update(kwargs)
    return base


def test_cle_avec_retraits_refusee():
    ok, raison = verdict(compte(restrictions=RESTRICTIONS_MALINES))
    assert ok is False
    assert "retraits" in raison


def test_restrictions_saines_acceptees():
    ok, raison = verdict(compte())
    assert ok is True
    assert "retraits désactivés" in raison


def test_can_withdraw_du_compte_n_est_pas_la_permission_de_la_cle():
    # D-018 : le compte renvoie canWithdraw=true, la clé n'a pas le droit de retrait.
    # Confondre les deux condamnait une clé saine — c'est la régression à empêcher.
    assert verdict(compte(can_withdraw=True, restrictions=RESTRICTIONS_SAINES))[0] is True


def test_restrictions_incompletes_refusees_fail_closed():
    # Un endpoint qui répond partiellement ne vaut pas une autorisation : l'absence
    # d'un droit explicite est un refus, jamais un « probablement bon ».
    ok, raison = verdict(compte(restrictions={"enableWithdrawals": False}))
    assert ok is False
    assert "lecture" in raison


def test_lecture_desactivee_refusee():
    ok, raison = verdict(compte(restrictions={**RESTRICTIONS_SAINES, "enableReading": False}))
    assert ok is False
    assert "lecture" in raison


def test_trading_desactive_refuse():
    ok, raison = verdict(
        compte(restrictions={**RESTRICTIONS_SAINES, "enableSpotAndMarginTrading": False})
    )
    assert ok is False
    assert "trading" in raison


def test_endpoint_restrictions_absent_mais_can_withdraw_refuse():
    ok, raison = verdict(compte(restrictions=None, can_withdraw=True))
    assert ok is False
    assert "canWithdraw" in raison


def test_cle_invalide_refusee():
    ok, raison = verdict({"ok": False, "error_code": -2015, "error": "Invalid API-key"})
    assert ok is False
    assert "refusée" in raison


def test_panne_reseau_refusee_fail_closed():
    ok, raison = verdict({"ok": False, "error_code": None, "error": "réseau injoignable"})
    assert ok is False
    assert "échec" in raison
