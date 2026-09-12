"""Dispatcher : exécution des écritures, preserve, multi-champ, sérialisation."""

from __future__ import annotations

import pytest

from bridge.app.dispatcher import BusDispatcher
from bridge.domain.commands import PlannedWrite
from bridge.domain.profile import LayoutSpec, WriteMode, WriteSpec
from bridge.domain.values import RegisterAddress
from tests.fakes import FakePublisher, FakeTransport

pytestmark = pytest.mark.asyncio


def _single() -> WriteSpec:
    return WriteSpec(
        key="storage_mode",
        address=RegisterAddress(0x1110),
        mode=WriteMode.SINGLE,
        type="u16",
        allowed=(0, 1, 2, 3),
    )


def _block_preserve() -> WriteSpec:
    return WriteSpec(
        key="power_setpoint",
        address=RegisterAddress(0x1187),
        mode=WriteMode.BLOCK,
        count=6,
        layout=(
            LayoutSpec(offset=2, type="s16", key="power_setpoint", scale=10, min=-5000, max=5000),
        ),
        preserve=True,
    )


def _block_multifield() -> WriteSpec:
    return WriteSpec(
        key="power_control",
        address=RegisterAddress(0x1187),
        mode=WriteMode.BLOCK,
        count=6,
        layout=(
            LayoutSpec(offset=2, type="s16", key="active", scale=10, min=-5000, max=5000),
            LayoutSpec(offset=3, type="s16", key="reactive", scale=10, min=-3000, max=3000),
        ),
        preserve=True,
    )


async def test_single_write_executes_and_acks() -> None:
    transport = FakeTransport()
    publisher = FakePublisher()
    dispatcher = BusDispatcher(transport, publisher)

    dispatcher.enqueue_write(PlannedWrite(_single(), {"storage_mode": 2}))
    await dispatcher.drain_writes()

    assert transport.writes == [(RegisterAddress(0x1110), (2,))]
    assert publisher.results == [("storage_mode", "accepted")]


async def test_block_preserve_reads_then_writes_only_target_offset() -> None:
    transport = FakeTransport()
    transport.script_read((11, 22, 999, 44, 55, 66))
    publisher = FakePublisher()
    dispatcher = BusDispatcher(transport, publisher)

    # -500 W avec scale 10 -> registre s16 0xFFCE au seul offset 2.
    dispatcher.enqueue_write(PlannedWrite(_block_preserve(), {"power_setpoint": -500}))
    await dispatcher.drain_writes()

    assert transport.ops == ["read", "write"]  # relecture avant réécriture
    address, values = transport.writes[0]
    assert address == RegisterAddress(0x1187)
    assert values == (11, 22, 0xFFCE, 44, 55, 66)  # offsets non ciblés préservés


async def test_multifield_sets_several_offsets_in_one_write() -> None:
    transport = FakeTransport()
    transport.script_read((11, 22, 33, 44, 55, 66))
    publisher = FakePublisher()
    dispatcher = BusDispatcher(transport, publisher)

    # active=-2000 (offset 2 -> -200 -> 0xFF38), reactive=500 (offset 3 -> 50).
    dispatcher.enqueue_write(PlannedWrite(_block_multifield(), {"active": -2000, "reactive": 500}))
    await dispatcher.drain_writes()

    address, values = transport.writes[0]
    assert address == RegisterAddress(0x1187)
    # Une seule trame 0x10 ; offsets 2 et 3 pilotés, le reste préservé.
    assert values == (11, 22, 0xFF38, 50, 55, 66)
    assert publisher.results == [("power_control", "accepted")]


async def test_multifield_partial_preserves_unset_field() -> None:
    transport = FakeTransport()
    transport.script_read((11, 22, 33, 44, 55, 66))
    publisher = FakePublisher()
    dispatcher = BusDispatcher(transport, publisher)

    # Seul 'active' fourni : offset 3 (reactive) reste préservé (44).
    dispatcher.enqueue_write(PlannedWrite(_block_multifield(), {"active": -2000}))
    await dispatcher.drain_writes()

    _, values = transport.writes[0]
    assert values == (11, 22, 0xFF38, 44, 55, 66)


async def test_out_of_range_encoding_is_reported_failed() -> None:
    transport = FakeTransport()
    publisher = FakePublisher()
    dispatcher = BusDispatcher(transport, publisher)

    dispatcher.enqueue_write(PlannedWrite(_block_preserve(), {"power_setpoint": 999999}))
    await dispatcher.drain_writes()

    assert transport.writes == []
    assert publisher.results[0][0] == "power_setpoint"
    assert publisher.results[0][1].startswith("failed")
