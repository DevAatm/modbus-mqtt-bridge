"""Chargement du profil : le profil de référence charge, les invalides échouent."""

from __future__ import annotations

from pathlib import Path

import pytest

from bridge.adapters.profile_yaml import ProfileError, load_profile, parse_duration
from bridge.domain.profile import WriteMode
from bridge.domain.result import Ok

REFERENCE = Path(__file__).resolve().parents[1] / "profiles" / "sofar-esi-5k-s1.yaml"


def test_reference_profile_loads() -> None:
    profile = load_profile(str(REFERENCE))
    assert profile.name == "sofar-esi-5k-s1"
    assert profile.slave_id.value == 1
    assert profile.serial.baudrate == 9600

    battery = profile.blocks[0]
    assert battery.name == "battery"
    assert battery.address.value == 0x0604
    assert battery.interval.total_seconds() == 5
    keys = {p.key for p in battery.points}
    assert "battery_power" in keys

    setpoint = profile.write_spec("power_setpoint")
    assert setpoint is not None
    assert setpoint.mode is WriteMode.BLOCK
    assert setpoint.preserve is True
    assert setpoint.layout[0].min == -5000


def test_duration_parsing() -> None:
    half = parse_duration("500ms")
    assert isinstance(half, Ok)
    assert half.value.total_seconds() == 0.5
    two_min = parse_duration("2m")
    assert isinstance(two_min, Ok)
    assert two_min.value.total_seconds() == 120
    assert not parse_duration("later").is_ok()


def _write(tmp_path: Path, text: str) -> str:
    path = tmp_path / "p.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_missing_blocks_rejected(tmp_path: Path) -> None:
    text = "device:\n  name: x\n  protocol: p\n  slave_id: 1\n"
    with pytest.raises(ProfileError, match="blocks"):
        load_profile(_write(tmp_path, text))


def test_point_offset_overflow_rejected(tmp_path: Path) -> None:
    text = (
        "device: {name: x, protocol: p, slave_id: 1}\n"
        "blocks:\n"
        "  - name: b\n    address: 0x10\n    count: 2\n    interval: 1s\n"
        "    points:\n      - {key: k, offset: 3, type: u16}\n"
    )
    with pytest.raises(ProfileError, match="déborde"):
        load_profile(_write(tmp_path, text))


def test_duplicate_point_key_rejected(tmp_path: Path) -> None:
    text = (
        "device: {name: x, protocol: p, slave_id: 1}\n"
        "blocks:\n"
        "  - name: b\n    address: 0x10\n    count: 2\n    interval: 1s\n"
        "    points:\n      - {key: k, offset: 0, type: u16}\n"
        "      - {key: k, offset: 1, type: u16}\n"
    )
    with pytest.raises(ProfileError, match="dupliquée"):
        load_profile(_write(tmp_path, text))


def test_bad_slave_id_rejected(tmp_path: Path) -> None:
    text = (
        "device: {name: x, protocol: p, slave_id: 999}\n"
        "blocks:\n"
        "  - name: b\n    address: 0x10\n    count: 1\n    interval: 1s\n"
        "    points:\n      - {key: k, offset: 0, type: u16}\n"
    )
    with pytest.raises(ProfileError, match="slave_id"):
        load_profile(_write(tmp_path, text))


def test_block_write_requires_layout(tmp_path: Path) -> None:
    text = (
        "device: {name: x, protocol: p, slave_id: 1}\n"
        "blocks:\n"
        "  - name: b\n    address: 0x10\n    count: 1\n    interval: 1s\n"
        "    points:\n      - {key: k, offset: 0, type: u16}\n"
        "writes:\n"
        "  - {key: w, address: 0x20, mode: block, count: 4}\n"
    )
    with pytest.raises(ProfileError, match="layout"):
        load_profile(_write(tmp_path, text))
