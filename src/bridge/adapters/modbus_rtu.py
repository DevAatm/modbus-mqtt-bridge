"""Adaptateur pymodbus -> ModbusTransport.

SEUL endroit qui ouvre le port série. Convertit les exceptions pymodbus et
les réponses d'erreur en `ModbusError`. Rien du domaine ne dépend de ce module.
"""

from __future__ import annotations

import logging

from pymodbus.client import AsyncModbusSerialClient
from pymodbus.exceptions import ModbusException
from pymodbus.pdu import ExceptionResponse

from bridge.domain.profile import SerialSettings
from bridge.domain.result import Err, Ok, Result
from bridge.domain.values import RegisterAddress, SlaveId
from bridge.ports.transport import ModbusError

_log = logging.getLogger(__name__)

# Codes d'exception Modbus -> libellé stable (pour l'acquittement des commandes).
_EXCEPTION_KINDS: dict[int, str] = {
    0x01: "illegal_function",
    0x02: "illegal_data_address",
    0x03: "illegal_data_value",
    0x04: "slave_device_failure",
    0x06: "slave_device_busy",
}


class PymodbusTransport:
    """Implémente `ModbusTransport` via un client pymodbus RTU asynchrone."""

    def __init__(self, port: str, serial: SerialSettings, slave_id: SlaveId) -> None:
        self._slave_id = slave_id.value
        self._client = AsyncModbusSerialClient(
            port=port,
            baudrate=serial.baudrate,
            bytesize=serial.bytesize,
            parity=serial.parity,
            stopbits=serial.stopbits,
        )

    async def connect(self) -> None:
        if not await self._client.connect():
            raise ConnectionError(f"connexion série impossible sur {self._client}")

    async def close(self) -> None:
        self._client.close()

    async def read_holding(
        self, address: RegisterAddress, count: int
    ) -> Result[tuple[int, ...], ModbusError]:
        try:
            rr = await self._client.read_holding_registers(
                address.value, count=count, device_id=self._slave_id
            )
        except ModbusException as exc:
            return Err(_from_exception(exc))
        if rr.isError():
            return Err(_from_response(rr))
        return Ok(tuple(rr.registers))

    async def write_single(self, address: RegisterAddress, value: int) -> Result[None, ModbusError]:
        try:
            rr = await self._client.write_register(address.value, value, device_id=self._slave_id)
        except ModbusException as exc:
            return Err(_from_exception(exc))
        if rr.isError():
            return Err(_from_response(rr))
        return Ok(None)

    async def write_block(
        self, address: RegisterAddress, values: tuple[int, ...]
    ) -> Result[None, ModbusError]:
        try:
            rr = await self._client.write_registers(
                address.value, list(values), device_id=self._slave_id
            )
        except ModbusException as exc:
            return Err(_from_exception(exc))
        if rr.isError():
            return Err(_from_response(rr))
        return Ok(None)


def _from_exception(exc: ModbusException) -> ModbusError:
    _log.warning("exception Modbus: %s", exc)
    return ModbusError(kind="timeout_or_transport", detail=str(exc))


def _from_response(response: object) -> ModbusError:
    if isinstance(response, ExceptionResponse):
        kind = _EXCEPTION_KINDS.get(response.exception_code, "modbus_exception")
        return ModbusError(kind=kind, detail=str(response))
    return ModbusError(kind="modbus_error", detail=str(response))
