"""Domaine : règles de cohérence d'un bus, sans YAML ni I/O."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from bridge.domain.bus import Bus
from bridge.domain.profile import BlockSpec, DeviceProfile, PointSpec, SerialSettings
from bridge.domain.result import Err, Ok
from bridge.domain.values import RegisterAddress, SlaveId


def _device(name: str, slave: int, baudrate: int = 9600) -> DeviceProfile:
    return DeviceProfile(
        name=name,
        protocol="p",
        slave_id=SlaveId(slave),
        serial=SerialSettings(baudrate=baudrate),
        blocks=(
            BlockSpec(
                name="b",
                address=RegisterAddress(0),
                count=1,
                interval=timedelta(seconds=5),
                points=(PointSpec("v", 0, "u16"),),
            ),
        ),
        writes=(),
    )


def test_two_distinct_devices_form_a_bus() -> None:
    result = Bus.of([_device("a", 1), _device("b", 2)])
    assert isinstance(result, Ok)
    assert [d.name for d in result.value.devices] == ["a", "b"]


def test_empty_bus_is_rejected() -> None:
    assert isinstance(Bus.of([]), Err)


def test_duplicate_slave_id_is_rejected() -> None:
    result = Bus.of([_device("a", 1), _device("b", 1)])
    assert isinstance(result, Err)
    assert "slave_id 1" in result.error


def test_duplicate_name_is_rejected() -> None:
    result = Bus.of([_device("a", 1), _device("a", 2)])
    assert isinstance(result, Err)
    assert "en double" in result.error


def test_name_unusable_as_topic_is_rejected() -> None:
    assert isinstance(Bus.of([_device("a#", 1)]), Err)


def test_serial_settings_must_match() -> None:
    a = _device("a", 1)
    b = replace(_device("b", 2), serial=SerialSettings(baudrate=2400))
    result = Bus.of([a, b])
    assert isinstance(result, Err)
    assert "série" in result.error
