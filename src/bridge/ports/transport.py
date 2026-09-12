"""Port transport Modbus. L'adaptateur `modbus_rtu` l'implémente.

Le propriétaire du bus (poller/dispatcher) est le SEUL à appeler ces méthodes.
Un adaptateur convertit les exceptions pymodbus en `Result`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from bridge.domain.result import Result
from bridge.domain.values import RegisterAddress


@dataclass(frozen=True, slots=True)
class ModbusError:
    """Erreur Modbus normalisée (exception code, timeout, trame corrompue)."""

    kind: str  # ex: "timeout", "illegal_data_address", "crc"
    detail: str


@runtime_checkable
class ModbusTransport(Protocol):
    async def read_holding(
        self, address: RegisterAddress, count: int
    ) -> Result[tuple[int, ...], ModbusError]:
        """Lit `count` registres à partir de `address` (fonction 0x03)."""
        ...

    async def write_single(self, address: RegisterAddress, value: int) -> Result[None, ModbusError]:
        """Écrit un registre (fonction 0x06)."""
        ...

    async def write_block(
        self, address: RegisterAddress, values: tuple[int, ...]
    ) -> Result[None, ModbusError]:
        """Écrit un bloc contigu (fonction 0x10)."""
        ...
