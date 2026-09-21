"""Adaptateur pymodbus -> ModbusTransport (série RTU ou TCP).

SEUL endroit qui ouvre un lien vers l'équipement. Convertit les exceptions
pymodbus et les réponses d'erreur en `ModbusError`. Rien du domaine ne dépend
de ce module.

Le protocole applicatif (fonctions 0x03/0x06/0x10, registres, `slave_id`) est
identique sur les deux liens : seule l'ADU change (CRC en RTU, en-tête MBAP en
TCP). D'où une seule implémentation, deux fabriques de client.

Attention : `tcp` recouvre deux matériels différents.
  - équipement Modbus TCP natif        -> framing `socket` (en-tête MBAP) ;
  - passerelle transparente TCP<->RS485 -> framing `rtu` (trames RTU encapsulées).
Un mauvais framing ne produit pas de valeurs fausses, il produit des timeouts.
Le choix est explicite dans la configuration, jamais deviné au runtime.
"""

from __future__ import annotations

import logging

from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
from pymodbus.client.base import ModbusBaseClient
from pymodbus.exceptions import ModbusException
from pymodbus.framer import FramerType
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

_FRAMERS: dict[str, FramerType] = {
    "socket": FramerType.SOCKET,  # Modbus TCP natif
    "rtu": FramerType.RTU,  # passerelle transparente TCP<->RS485
}


class PymodbusTransport:
    """Implémente `ModbusTransport` au-dessus d'un client pymodbus asynchrone.

    Construire par `open_serial` / `open_tcp`, jamais directement : le choix du
    lien appartient au composition root.
    """

    def __init__(self, client: ModbusBaseClient, slave_id: SlaveId, description: str) -> None:
        self._client = client
        self._slave_id = slave_id.value
        self._description = description

    def __str__(self) -> str:
        return self._description

    @property
    def connected(self) -> bool:
        return bool(self._client.connected)

    async def connect(self) -> None:
        if not await self._client.connect():
            raise ConnectionError(f"connexion impossible vers {self._description}")

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


def open_serial(port: str, serial: SerialSettings, slave_id: SlaveId) -> PymodbusTransport:
    """Lien série RTU. Le process est alors l'unique maître du bus."""
    client = AsyncModbusSerialClient(
        port=port,
        baudrate=serial.baudrate,
        bytesize=serial.bytesize,
        parity=serial.parity,
        stopbits=serial.stopbits,
    )
    return PymodbusTransport(client, slave_id, f"série {port}")


def open_tcp(
    host: str,
    port: int,
    framing: str,
    slave_id: SlaveId,
    *,
    timeout_s: float = 3.0,
    reconnect_delay_s: float = 1.0,
    reconnect_delay_max_s: float = 60.0,
) -> PymodbusTransport:
    """Lien TCP.

    La reconnexion est déléguée à pymodbus (backoff exponentiel borné) : une
    coupure réseau ne doit pas tuer le service, elle doit rendre les lectures
    en erreur jusqu'au rétablissement. `retries` est laissé au défaut pour les
    lectures ; aucune écriture n'est rejouée par le service (cf. dispatcher).
    """
    framer = _FRAMERS.get(framing)
    if framer is None:
        raise ValueError(f"framing TCP inconnu: {framing!r} (attendu {sorted(_FRAMERS)})")
    client = AsyncModbusTcpClient(
        host,
        port=port,
        framer=framer,
        timeout=timeout_s,
        reconnect_delay=reconnect_delay_s,
        reconnect_delay_max=reconnect_delay_max_s,
    )
    return PymodbusTransport(client, slave_id, f"tcp {host}:{port} ({framing})")


def _from_exception(exc: ModbusException) -> ModbusError:
    _log.warning("exception Modbus: %s", exc)
    return ModbusError(kind="timeout_or_transport", detail=str(exc))


def _from_response(response: object) -> ModbusError:
    if isinstance(response, ExceptionResponse):
        kind = _EXCEPTION_KINDS.get(response.exception_code, "modbus_exception")
        return ModbusError(kind=kind, detail=str(response))
    return ModbusError(kind="modbus_error", detail=str(response))
