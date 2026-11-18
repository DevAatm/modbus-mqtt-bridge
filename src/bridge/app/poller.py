"""Boucle de poll. Propriétaire de la cadence. S'arrête proprement sur annulation.

Chaque bloc a son `interval`. Entre deux échéances, `Clock.sleep` (pas de
boucle serrée). Après chaque lecture réussie, on met à jour l'horodatage de
santé (healthcheck par fichier, pas de serveur HTTP). Une absence de réponse
n'est jamais publiée comme une valeur nulle.

Plusieurs équipements sur un même bus : un poller par équipement, mais un
seul fichier de santé par process, que tous rafraîchissent. Il reste donc
frais tant qu'AU MOINS UN équipement répond. Choix délibéré : la perte d'un
seul compteur est signalée par SON availability MQTT (`offline`), pas par un
redémarrage du conteneur qui interromprait aussi les équipements sains. Le
healthcheck ne détecte que la perte du bus entier (câble, adaptateur, lien).
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
        failures_before_offline: int = 3,
    ) -> None:
        self._profile = profile
        self._dispatcher = dispatcher
        self._publisher = publisher
        self._clock = clock
        self._health_file = Path(health_file)
        # Blocs déjà lus avec succès au moins une fois (log INFO -> DEBUG ensuite).
        self._logged_blocks: set[str] = set()
        # Disponibilité : le publisher a publié `online` à la connexion MQTT.
        # Une coupure du lien (câble arraché, passerelle TCP injoignable) doit
        # basculer l'availability, sinon HA affiche des valeurs figées comme si
        # elles étaient fraîches. Un seuil évite de battre sur un timeout isolé.
        self._failures_before_offline = max(1, failures_before_offline)
        self._consecutive_failures = 0
        self._available = True

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
        result = await self._dispatcher.read_registers(block.function, block.address, block.count)
        if isinstance(result, Err):
            # Pas de valeur -> on ne publie rien, on ne touche pas la santé.
            _log.warning("lecture bloc %s échouée: %s", block.name, result.error)
            await self._record_failure()
            return

        await self._record_success()
        registers = result.value
        published = 0
        for point in block.points:
            decoded = decode_point(registers, point)
            if isinstance(decoded, Ok):
                await self._publisher.publish_state(point.key, decoded.value.value, point.unit)
                published += 1
            elif decoded.error.kind == "non_finite":
                # Valeur indisponible côté équipement (NaN/inf) : rien n'est
                # publié, la dernière valeur retenue reste celle de HA.
                _log.warning("point %s non publié: %s", point.key, decoded.error)
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

    async def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._available and self._consecutive_failures >= self._failures_before_offline:
            self._available = False
            _log.error(
                "équipement injoignable (%d lectures consécutives en échec): availability=offline",
                self._consecutive_failures,
            )
            await self._publisher.publish_availability(False)

    async def _record_success(self) -> None:
        self._consecutive_failures = 0
        if not self._available:
            self._available = True
            _log.info("équipement de nouveau joignable: availability=online")
            await self._publisher.publish_availability(True)

    def _touch_health(self) -> None:
        """Écrit un horodatage; testé par bridge.healthcheck.

        Fichier partagé par tous les pollers du process : voir le docstring du
        module pour la sémantique en mode bus.
        """
        try:
            self._health_file.write_text(str(self._clock.monotonic()), encoding="ascii")
        except OSError as exc:
            _log.warning("impossible d'écrire le fichier de santé: %s", exc)
