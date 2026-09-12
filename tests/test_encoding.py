"""Encodage grandeur SI -> registres, et aller-retour avec le décodage."""

from __future__ import annotations

import pytest

from bridge.domain.decoding import decode_point
from bridge.domain.encoding import encode_value
from bridge.domain.profile import PointSpec, RegisterType, WordOrder
from bridge.domain.result import Err, Ok


@pytest.mark.parametrize(
    ("value", "type_", "scale", "expected"),
    [
        (-1.0, "s16", 0.01, (0xFF9C,)),  # courant batterie en décharge
        (-500.0, "s16", 10.0, (0xFFCE,)),  # puissance en décharge
        (50.0, "u16", 0.1, (500,)),
        (2.0, "u16", 1.0, (2,)),
    ],
)
def test_encode_matches_expected_registers(
    value: float, type_: RegisterType, scale: float, expected: tuple[int, ...]
) -> None:
    result = encode_value(value, type_, scale, "big")
    assert isinstance(result, Ok)
    assert result.value == expected


@pytest.mark.parametrize("word_order", ["big", "little"])
@pytest.mark.parametrize(
    ("value", "type_", "scale"),
    [(-1.0, "s16", 0.01), (-500.0, "s16", 10.0), (65536.0, "u32", 1.0), (-3.0, "s32", 0.5)],
)
def test_roundtrip_encode_then_decode(
    value: float, type_: RegisterType, scale: float, word_order: WordOrder
) -> None:
    enc = encode_value(value, type_, scale, word_order)
    assert isinstance(enc, Ok)
    spec = PointSpec("x", 0, type_, scale=scale, word_order=word_order)
    dec = decode_point(enc.value, spec)
    assert isinstance(dec, Ok)
    assert dec.value.value == pytest.approx(value)


def test_out_of_range_is_err() -> None:
    assert isinstance(encode_value(70000.0, "u16", 1.0, "big"), Err)
    assert isinstance(encode_value(-1.0, "u16", 1.0, "big"), Err)


def test_zero_scale_is_err() -> None:
    assert isinstance(encode_value(1.0, "u16", 0.0, "big"), Err)
