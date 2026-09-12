"""Poller : décodage/publication d'un bloc, santé, et sérialisation des écritures."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from bridge.app.dispatcher import BusDispatcher
from bridge.app.poller import Poller
from bridge.domain.commands import PlannedWrite
from bridge.domain.profile import (
    BlockSpec,
    DeviceProfile,
    LayoutSpec,
    PointSpec,
    SerialSettings,
    WriteMode,
    WriteSpec,
)
from bridge.domain.values import RegisterAddress, SlaveId
from tests.fakes import FakeClock, FakePublisher, FakeTransport

pytestmark = pytest.mark.asyncio


def _profile() -> DeviceProfile:
    return DeviceProfile(
        name="dev",
        protocol="p",
        slave_id=SlaveId(1),
        serial=SerialSettings(),
        blocks=(
            BlockSpec(
                name="battery",
                address=RegisterAddress(0x0604),
                count=7,
                interval=timedelta(seconds=5),
                points=(
                    PointSpec("battery_voltage", 0, "u16", scale=0.1, unit="V"),
                    PointSpec("battery_current", 1, "s16", scale=0.01, unit="A"),
                ),
            ),
        ),
        writes=(
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
        ),
    )


async def test_poll_block_decodes_and_publishes(tmp_path: Path) -> None:
    transport = FakeTransport()
    transport.script_read((500, 0xFF9C, 0, 0, 0, 0, 0))
    publisher = FakePublisher()
    clock = FakeClock()
    health = tmp_path / "health"
    poller = Poller(_profile(), BusDispatcher(transport, publisher), publisher, clock, str(health))

    await poller._poll_block(_profile().blocks[0])

    assert ("battery_voltage", 50.0, "V") in publisher.states
    assert ("battery_current", -1.0, "A") in publisher.states
    assert health.exists()


async def test_read_error_publishes_nothing_and_skips_health(tmp_path: Path) -> None:
    from bridge.ports.transport import ModbusError

    transport = FakeTransport()
    transport.script_read_error(ModbusError("timeout", "pas de réponse"))
    publisher = FakePublisher()
    health = tmp_path / "health"
    poller = Poller(
        _profile(), BusDispatcher(transport, publisher), publisher, FakeClock(), str(health)
    )

    await poller._poll_block(_profile().blocks[0])

    assert publisher.states == []  # jamais de valeur nulle sur absence de réponse
    assert not health.exists()


async def test_write_is_serialized_after_read(tmp_path: Path) -> None:
    transport = FakeTransport()
    transport.script_read((0, 0, 0, 0, 0, 0, 0))  # poll du bloc battery
    transport.script_read((0, 0, 0, 0, 0, 0))  # relecture preserve du bloc setpoint
    publisher = FakePublisher()
    dispatcher = BusDispatcher(transport, publisher)
    profile = _profile()
    poller = Poller(profile, dispatcher, publisher, FakeClock(), str(tmp_path / "h"))

    dispatcher.enqueue_write(PlannedWrite(profile.writes[0], {"power_setpoint": -500}))
    await poller._poll_block(profile.blocks[0])
    await dispatcher.drain_writes()

    # La lecture du poll précède toute écriture : aucune écriture pendant le poll.
    assert transport.ops == ["read", "read", "write"]
