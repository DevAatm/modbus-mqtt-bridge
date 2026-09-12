"""Boucle de poll. Propriétaire de la cadence. S'arrête proprement sur annulation.

Chaque bloc a son `interval`. Entre deux échéances, `Clock.sleep` (pas de
boucle serrée). Après chaque lecture réussie, on met à jour l'horodatage de
santé (healthcheck par fichier, pas de serveur HTTP). Une absence de réponse
n'est jamais publiée comme une valeur nulle.
"""

from __future__ import annotations

import logging
from pathlib import Path

from bridge.app.dispatcher import BusDispatcher
from bridge.domain.decoding import decode_point
from bridge.domain.profile import BlockSpec, DeviceProfile
from bridge.domain.result import Err, Ok
from bridge.ports.clock import Clock
from bridge.ports.publisher import MessagePublisher

_log = logging.getLogger(__name__)


class Poller:
    def __init__(
        self,
        profile: DeviceProfile,
        dispatcher: BusDispatcher,
        publisher: MessagePublisher,
        clock: Clock,
        health_file: str,
    ) -> None:
        self._profile = profile
        self._dispatcher = dispatcher
        self._publisher = publisher
        self._clock = clock
        self._health_file = Path(health_file)
        # Blocs déjà lus avec succès au moins une fois (log INFO -> DEBUG ensuite).
        self._logged_blocks: set[str] = set()

    async def run(self) -> None:
        """Boucle principale. Annulable (propage CancelledError)."""
        _log.info(
            "poll démarré: %s",
            ", ".join(f"{b.name}@{b.interval.total_seconds():g}s" for b in self._profile.blocks),
        )
        # Échéancier : instant monotone de prochaine lecture par bloc.
        next_due: dict[str, float] = {b.name: self._clock.monotonic() for b in self._profile.blocks}

        while True:
            now = self._clock.monotonic()
            for block in self._profile.blocks:
                if next_due[block.name] <= now:
                    await self._poll_block(block)
                    next_due[block.name] = now + block.interval.total_seconds()

            # Les écritures s'intercalent entre deux cycles, pas pendant.
            await self._dispatcher.drain_writes()

            now = self._clock.monotonic()
            sleep_for = max(0.0, min(next_due.values()) - now)
            await self._clock.sleep(sleep_for)

    async def _poll_block(self, block: BlockSpec) -> None:
        result = await self._dispatcher.read_holding(block.address, block.count)
        if isinstance(result, Err):
            # Pas de valeur -> on ne publie rien, on ne touche pas la santé.
            _log.warning("lecture bloc %s échouée: %s", block.name, result.error)
            return

        registers = result.value
        published = 0
        for point in block.points:
            decoded = decode_point(registers, point)
            if isinstance(decoded, Ok):
                await self._publisher.publish_state(point.key, decoded.value.value, point.unit)
                published += 1
            else:
                _log.error("décodage %s échoué: %s", point.key, decoded.error)

        # Première lecture réussie d'un bloc en INFO, les suivantes en DEBUG :
        # visibilité au démarrage sans inonder les logs en régime établi.
        if block.name not in self._logged_blocks:
            self._logged_blocks.add(block.name)
            _log.info("bloc %s: %d point(s) publié(s)", block.name, published)
        else:
            _log.debug("bloc %s: %d point(s) publié(s)", block.name, published)

        self._touch_health()

    def _touch_health(self) -> None:
        """Écrit un horodatage; testé par bridge.healthcheck."""
        try:
            self._health_file.write_text(str(self._clock.monotonic()), encoding="ascii")
        except OSError as exc:
            _log.warning("impossible d'écrire le fichier de santé: %s", exc)
