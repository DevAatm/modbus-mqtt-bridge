"""Adaptateur pymodbus : traduction table de registres -> code fonction.

Le client pymodbus est remplacé par un double qui enregistre les appels : aucun
port série ni socket n'est ouvert.
"""

from __future__ import annotations

from typing import cast

import pytest
from pymodbus.client.base import ModbusBaseClient

from bridge.adapters.modbus import PymodbusTransport
from bridge.domain.profile import RegisterTable
from bridge.domain.result import Err, Ok
from bridge.domain.values import RegisterAddress, SlaveId

pytestmark = pytest.mark.asyncio


class _Response:
    def __init__(self, registers: list[int], error: bool) -> None:
        self.registers = registers
        self._error = error

    def isError(self) -> bool:
        return self._error


class _RecordingClient:
    def __init__(self, error: bool = False) -> None:
        self.calls: list[tuple[str, int, int, int]] = []
        self._error = error

    async def read_holding_registers(
        self, address: int, *, count: int, device_id: int
    ) -> _Response:
        self.calls.append(("holding", address, count, device_id))
        return _Response([1] * count, self._error)

    async def read_input_registers(self, address: int, *, count: int, device_id: int) -> _Response:
        self.calls.append(("input", address, count, device_id))
        return _Response([2] * count, self._error)


def _transport(client: _RecordingClient) -> PymodbusTransport:
    return PymodbusTransport(cast(ModbusBaseClient, client), SlaveId(7), "test")


async def test_input_table_calls_read_input_registers() -> None:
    client = _RecordingClient()
    result = await _transport(client).read_registers(RegisterTable.INPUT, RegisterAddress(0x46), 6)
    assert client.calls == [("input", 0x46, 6, 7)]
    assert isinstance(result, Ok)
    assert result.value == (2,) * 6


async def test_holding_table_calls_read_holding_registers() -> None:
    client = _RecordingClient()
    result = await _transport(client).read_registers(
        RegisterTable.HOLDING, RegisterAddress(0x0604), 7
    )
    assert client.calls == [("holding", 0x0604, 7, 7)]
    assert isinstance(result, Ok)
    assert result.value == (1,) * 7


async def test_input_read_error_is_err() -> None:
    client = _RecordingClient(error=True)
    result = await _transport(client).read_registers(RegisterTable.INPUT, RegisterAddress(0), 2)
    assert isinstance(result, Err)
