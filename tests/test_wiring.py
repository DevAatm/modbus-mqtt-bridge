"""Parsing des charges utiles de commande : scalaire vs objet JSON."""

from __future__ import annotations

from bridge.app.wiring import _parse_command


def test_scalar_payload() -> None:
    cmd = _parse_command("power_setpoint", " -2000 ")
    assert cmd is not None
    assert cmd.scalar == -2000
    assert cmd.fields is None


def test_json_object_payload() -> None:
    cmd = _parse_command("power_control", '{"active": -2000, "reactive": 0}')
    assert cmd is not None
    assert cmd.scalar is None
    assert cmd.fields == {"active": -2000.0, "reactive": 0.0}


def test_invalid_payload_returns_none() -> None:
    assert _parse_command("k", "abc") is None
    assert _parse_command("k", "[1, 2]") is None  # tableau JSON non supporté
    assert _parse_command("k", "{}") is None  # objet vide
    assert _parse_command("k", '{"a": "x"}') is None  # valeur non numérique
