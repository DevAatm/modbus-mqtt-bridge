"""File de commandes : sérialise lecture et écriture sur le même transport.

INVARIANT (cf. CLAUDE.md) : un seul thread, une seule socket parlent au bus.
Les écritures s'intercalent ENTRE deux cycles de poll, jamais pendant. Ce
dispatcher est le point unique où lectures et écritures sont ordonnancées ;
un verrou garantit qu'aucune écriture ne part au milieu d'une lecture.
"""

from __future__ import annotations

import asyncio
import logging

from bridge.domain.commands import PlannedWrite
from bridge.domain.encoding import encode_value
from bridge.domain.profile import WriteMode, WriteSpec
from bridge.domain.result import Err, Ok, Result
from bridge.domain.values import RegisterAddress
from bridge.ports.publisher import MessagePublisher
from bridge.ports.transport import ModbusError, ModbusTransport

_log = logging.getLogger(__name__)


class BusDispatcher:
    """Propriétaire logique du transport. Rien d'autre ne l'appelle en direct."""

    def __init__(self, transport: ModbusTransport, publisher: MessagePublisher) -> None:
        self._transport = transport
        self._publisher = publisher
        self._lock = asyncio.Lock()
        self._pending: asyncio.Queue[PlannedWrite] = asyncio.Queue()

    async def read_holding(
        self, address: RegisterAddress, count: int
    ) -> Result[tuple[int, ...], ModbusError]:
        """Lecture sérialisée. Utilisée par le poller entre deux écritures."""
        async with self._lock:
            return await self._transport.read_holding(address, count)

    def enqueue_write(self, planned: PlannedWrite) -> None:
        """Dépose une écriture validée. Ne saute jamais son tour."""
        self._pending.put_nowait(planned)

    async def drain_writes(self) -> None:
        """Exécute les écritures en attente, une par une, sous verrou.

        Appelée par le poller entre deux cycles. Chaque écriture est acquittée
        (accepté / échoué + motif) via le publisher.
        """
        while not self._pending.empty():
            planned = self._pending.get_nowait()
            async with self._lock:
                result = await self._execute(planned)
            key = planned.spec.key
            if isinstance(result, Ok):
                await self._publisher.publish_command_result(key, "accepted")
            else:
                _log.warning("écriture %s échouée: %s", key, result.error)
                await self._publisher.publish_command_result(key, f"failed: {result.error}")

    async def _execute(self, planned: PlannedWrite) -> Result[None, str]:
        spec = planned.spec
        if spec.mode is WriteMode.SINGLE:
            return await self._execute_single(spec, planned.values[spec.key])
        return await self._execute_block(spec, planned.values)

    async def _execute_single(self, spec: WriteSpec, value: float) -> Result[None, str]:
        assert spec.type is not None  # garanti par la validation du profil
        enc = encode_value(value, spec.type, spec.scale, spec.word_order)
        if isinstance(enc, Err):
            return Err(enc.error)
        res = await self._transport.write_single(spec.address, enc.value[0])
        return Ok(None) if isinstance(res, Ok) else Err(res.error.detail)

    async def _execute_block(self, spec: WriteSpec, values: dict[str, float]) -> Result[None, str]:
        # Registres de base : relecture (preserve) ou zéros. La validation a
        # garanti que sans preserve, tous les champs sont fournis.
        if spec.preserve:
            current = await self._transport.read_holding(spec.address, spec.count)
            if isinstance(current, Err):
                return Err(f"relecture bloc impossible: {current.error.detail}")
            registers = list(current.value)
        else:
            registers = [0] * spec.count

        # Place chaque champ piloté à son offset, puis une seule trame 0x10.
        for field in spec.layout:
            if field.key not in values:
                continue
            enc = encode_value(values[field.key], field.type, field.scale, field.word_order)
            if isinstance(enc, Err):
                return Err(f"{field.key}: {enc.error}")
            for i, reg in enumerate(enc.value):
                registers[field.offset + i] = reg

        res = await self._transport.write_block(spec.address, tuple(registers))
        return Ok(None) if isinstance(res, Ok) else Err(res.error.detail)
