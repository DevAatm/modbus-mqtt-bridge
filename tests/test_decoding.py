"""Tables `registres -> grandeur attendue`. Le filet principal (cf. CLAUDE.md)."""

from __future__ import annotations

import pytest

from bridge.domain.decoding import decode_point
from bridge.domain.profile import PointSpec
from bridge.domain.result import Err, Ok

# Bloc `battery` du profil de référence, avec une batterie en décharge :
# courant et puissance négatifs (d'où l'importance de s16 vs u16).
BATTERY = (500, 0xFF9C, 0xFFCE, 25, 80, 100, 0)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (PointSpec("battery_voltage", 0, "u16", scale=0.1, unit="V"), 50.0),
        (PointSpec("battery_current", 1, "s16", scale=0.01, unit="A"), -1.0),
        (PointSpec("battery_power", 2, "s16", scale=10, unit="W"), -500.0),
        (PointSpec("battery_temp", 3, "s16", unit="°C"), 25.0),
        (PointSpec("battery_soc", 4, "u16", unit="%"), 80.0),
    ],
)
def test_decode_battery_points(spec: PointSpec, expected: float) -> None:
    result = decode_point(BATTERY, spec)
    assert isinstance(result, Ok)
    assert result.value.value == pytest.approx(expected)
    assert result.value.unit == spec.unit


def test_u32_big_word_order() -> None:
    # 0x0001_0000 = 65536, mot de poids fort en premier.
    spec = PointSpec("energy", 0, "u32", word_order="big")
    result = decode_point((0x0001, 0x0000), spec)
    assert isinstance(result, Ok)
    assert result.value.value == 65536.0


def test_u32_little_word_order() -> None:
    spec = PointSpec("energy", 0, "u32", word_order="little")
    result = decode_point((0x0000, 0x0001), spec)
    assert isinstance(result, Ok)
    assert result.value.value == 65536.0


def test_offset_overflow_is_err() -> None:
    spec = PointSpec("oops", 6, "u32")  # 6+2 > 7
    result = decode_point(BATTERY, spec)
    assert not result.is_ok()


# --- f32 : IEEE 754 32 bits, mot de poids fort en premier (Eastron SDM) ------


@pytest.mark.parametrize(
    ("registers", "expected"),
    [
        ((0x4366, 0x0000), 230.0),  # 230.0 V
        ((0xC366, 0x0000), -230.0),  # puissance exportée : négative
        ((0x3F80, 0x0000), 1.0),  # facteur de puissance unitaire
        ((0x4248, 0x0000), 50.0),  # 50 Hz
        ((0x0000, 0x0000), 0.0),
    ],
)
def test_f32_big_endian(registers: tuple[int, int], expected: float) -> None:
    result = decode_point(registers, PointSpec("v", 0, "f32", unit="V"))
    assert isinstance(result, Ok)
    assert result.value.value == pytest.approx(expected)
    assert result.value.unit == "V"


def test_f32_little_word_order() -> None:
    result = decode_point((0x0000, 0x4366), PointSpec("v", 0, "f32", word_order="little"))
    assert isinstance(result, Ok)
    assert result.value.value == pytest.approx(230.0)


def test_f32_scale_applied_when_present() -> None:
    # 230.0 Wh lus, publiés en kWh via scale 0.001.
    result = decode_point((0x4366, 0x0000), PointSpec("e", 0, "f32", scale=0.001))
    assert isinstance(result, Ok)
    assert result.value.value == pytest.approx(0.23)


def test_f32_at_offset_in_block() -> None:
    block = (0x4366, 0x0000, 0x0000, 0x0000, 0x40A0, 0x0000)  # 230.0, 0.0, 5.0
    result = decode_point(block, PointSpec("i", 4, "f32"))
    assert isinstance(result, Ok)
    assert result.value.value == pytest.approx(5.0)


@pytest.mark.parametrize(
    "registers",
    [
        (0x7FC0, 0x0000),  # NaN silencieux
        (0xFFC0, 0x0001),  # NaN négatif avec charge utile
        (0x7F80, 0x0000),  # +inf
        (0xFF80, 0x0000),  # -inf
    ],
)
def test_f32_non_finite_is_err(registers: tuple[int, int]) -> None:
    result = decode_point(registers, PointSpec("v", 0, "f32"))
    assert isinstance(result, Err)
    assert result.error.kind == "non_finite"


def test_overflow_error_kind() -> None:
    result = decode_point((0x4366,), PointSpec("v", 0, "f32"))
    assert isinstance(result, Err)
    assert result.error.kind == "overflow"
