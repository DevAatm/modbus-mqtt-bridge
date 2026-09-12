"""Validation des écritures : whitelist, allowed, clamp, read_only, multi-champ."""

from __future__ import annotations

from bridge.domain.commands import RejectReason, WriteCommand, validate
from bridge.domain.profile import (
    DeviceProfile,
    LayoutSpec,
    SerialSettings,
    WriteMode,
    WriteSpec,
)
from bridge.domain.result import Err, Ok
from bridge.domain.values import RegisterAddress, SlaveId


def _profile() -> DeviceProfile:
    return DeviceProfile(
        name="test",
        protocol="x",
        slave_id=SlaveId(1),
        serial=SerialSettings(),
        blocks=(),
        writes=(
            WriteSpec(
                key="storage_mode",
                address=RegisterAddress(0x1110),
                mode=WriteMode.SINGLE,
                type="u16",
                allowed=(0, 1, 2, 3),
            ),
            WriteSpec(
                key="power_setpoint",
                address=RegisterAddress(0x1187),
                mode=WriteMode.BLOCK,
                count=6,
                layout=(
                    LayoutSpec(
                        offset=2, type="s16", key="power_setpoint", scale=10, min=-5000, max=5000
                    ),
                ),
                preserve=True,
            ),
            WriteSpec(
                key="power_control",
                address=RegisterAddress(0x1187),
                mode=WriteMode.BLOCK,
                count=6,
                layout=(
                    LayoutSpec(offset=2, type="s16", key="active", scale=10, min=-5000, max=5000),
                    LayoutSpec(offset=3, type="s16", key="reactive", scale=10, min=-3000, max=3000),
                ),
                preserve=True,
            ),
            WriteSpec(
                key="full_block",
                address=RegisterAddress(0x1200),
                mode=WriteMode.BLOCK,
                count=2,
                layout=(
                    LayoutSpec(offset=0, type="u16", key="a"),
                    LayoutSpec(offset=1, type="u16", key="b"),
                ),
                preserve=False,
            ),
        ),
    )


def test_unknown_key_rejected() -> None:
    result = validate(WriteCommand.of_scalar("nope", 1), _profile(), read_only=False)
    assert isinstance(result, Err)
    assert result.error.reason is RejectReason.UNKNOWN_KEY


def test_read_only_rejects_everything() -> None:
    result = validate(WriteCommand.of_scalar("storage_mode", 1), _profile(), read_only=True)
    assert isinstance(result, Err)
    assert result.error.reason is RejectReason.READ_ONLY


def test_value_not_in_allowed_rejected() -> None:
    result = validate(WriteCommand.of_scalar("storage_mode", 9), _profile(), read_only=False)
    assert isinstance(result, Err)
    assert result.error.reason is RejectReason.NOT_ALLOWED


def test_out_of_range_block_rejected() -> None:
    result = validate(WriteCommand.of_scalar("power_setpoint", 6000), _profile(), read_only=False)
    assert isinstance(result, Err)
    assert result.error.reason is RejectReason.OUT_OF_RANGE


def test_scalar_write_accepted() -> None:
    result = validate(WriteCommand.of_scalar("power_setpoint", -1000), _profile(), read_only=False)
    assert isinstance(result, Ok)
    assert result.value.values == {"power_setpoint": -1000}


def test_scalar_rejected_on_multifield_block() -> None:
    result = validate(WriteCommand.of_scalar("power_control", -1000), _profile(), read_only=False)
    assert isinstance(result, Err)
    assert result.error.reason is RejectReason.BAD_PAYLOAD


def test_multifield_write_accepted() -> None:
    result = validate(
        WriteCommand.of_fields("power_control", {"active": -2000, "reactive": 500}),
        _profile(),
        read_only=False,
    )
    assert isinstance(result, Ok)
    assert result.value.values == {"active": -2000, "reactive": 500}


def test_multifield_partial_ok_with_preserve() -> None:
    # preserve=True : fournir un sous-ensemble des champs est autorisé.
    result = validate(
        WriteCommand.of_fields("power_control", {"active": -2000}),
        _profile(),
        read_only=False,
    )
    assert isinstance(result, Ok)


def test_multifield_unknown_field_rejected() -> None:
    result = validate(
        WriteCommand.of_fields("power_control", {"nope": 1}),
        _profile(),
        read_only=False,
    )
    assert isinstance(result, Err)
    assert result.error.reason is RejectReason.UNKNOWN_FIELD


def test_multifield_out_of_range_rejected() -> None:
    result = validate(
        WriteCommand.of_fields("power_control", {"reactive": 9000}),
        _profile(),
        read_only=False,
    )
    assert isinstance(result, Err)
    assert result.error.reason is RejectReason.OUT_OF_RANGE


def test_missing_field_rejected_without_preserve() -> None:
    # Sans preserve, un champ non fourni serait écrit à 0 -> rejet.
    result = validate(
        WriteCommand.of_fields("full_block", {"a": 1}),
        _profile(),
        read_only=False,
    )
    assert isinstance(result, Err)
    assert result.error.reason is RejectReason.MISSING_FIELD
