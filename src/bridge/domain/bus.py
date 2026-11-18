"""Bus : les équipements qui partagent un même lien Modbus (même maître).

Un profil décrit un MODÈLE d'équipement ; un bus décrit un DÉPLOIEMENT :
quels équipements, à quelles adresses, sous quels noms. Le même profil peut
donc y figurer plusieurs fois (deux compteurs identiques en guirlande).

Les règles de cohérence sont pures et vivent ici, pas dans l'adaptateur YAML :
elles s'appliquent aussi au cas historique d'un profil seul.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from bridge.domain.profile import DeviceProfile
from bridge.domain.result import Err, Ok, Result
from bridge.domain.values import DeviceId


@dataclass(frozen=True, slots=True)
class Bus:
    """Équipements d'un même lien, chacun avec un nom et une adresse uniques."""

    devices: tuple[DeviceProfile, ...]

    @staticmethod
    def of(devices: Sequence[DeviceProfile]) -> Result[Bus, str]:
        """Valide la cohérence du bus.

        - au moins un équipement ;
        - noms valides pour un topic MQTT et uniques (sinon deux équipements
          publieraient sur les mêmes topics) ;
        - adresses d'esclave uniques (sinon deux équipements répondraient à la
          même trame, ou plutôt un seul, lu deux fois) ;
        - réglages série identiques : tous partagent la même ligne. Exigé même
          en TCP, où ils ne servent pas : deux profils en désaccord sur une
          même ligne trahissent une erreur de configuration.
        """
        if not devices:
            return Err("bus vide : au moins un équipement attendu")

        names: set[str] = set()
        slaves: dict[int, str] = {}
        reference = devices[0]
        for device in devices:
            parsed = DeviceId.parse(device.name)
            if isinstance(parsed, Err):
                return Err(parsed.error)
            name = parsed.value.name
            if name in names:
                return Err(f"nom d'équipement en double sur le bus: {name!r}")
            names.add(name)

            slave = device.slave_id.value
            if slave in slaves:
                return Err(f"slave_id {slave} en double sur le bus: {slaves[slave]!r} et {name!r}")
            slaves[slave] = name

            if device.serial != reference.serial:
                return Err(
                    f"réglages série incompatibles sur une même ligne: "
                    f"{reference.name!r} {reference.serial} vs {name!r} {device.serial}"
                )
        return Ok(Bus(tuple(devices)))
