"""Modèle du profil : description complète de l'équipement, sans code métier.

Le profil est le produit (cf. CLAUDE.md). Ces types sont le résultat validé
du chargement YAML ; ils ne connaissent pas le YAML lui-même (c'est le rôle de
`adapters/profile_yaml.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Literal

from bridge.domain.values import RegisterAddress, SlaveId

# Types de registre supportés. Signé et non signé sont distincts, jamais un
# `int` nu (une puissance batterie est négative en décharge).
RegisterType = Literal["u16", "s16", "u32", "s32"]
WordOrder = Literal["big", "little"]


class WriteMode(Enum):
    SINGLE = "single"  # fonction 0x06
    BLOCK = "block"  # fonction 0x10, réécriture du bloc complet


@dataclass(frozen=True, slots=True)
class SerialSettings:
    baudrate: int = 9600
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 1


@dataclass(frozen=True, slots=True)
class PointSpec:
    """Un point mesurable dans un bloc de lecture."""

    key: str
    offset: int
    type: RegisterType
    scale: float = 1.0
    unit: str = ""
    word_order: WordOrder = "big"


@dataclass(frozen=True, slots=True)
class BlockSpec:
    """Lecture groupée : un read par bloc, pas un par point."""

    name: str
    address: RegisterAddress
    count: int
    interval: timedelta
    points: tuple[PointSpec, ...]


@dataclass(frozen=True, slots=True)
class LayoutSpec:
    """Un champ pilotable à l'intérieur d'une écriture groupée.

    `key` identifie le champ dans une commande structurée JSON. Pour un bloc
    mono-champ, il peut être omis dans le profil (par défaut = clé de l'écriture).
    """

    offset: int
    type: RegisterType
    key: str = ""
    scale: float = 1.0
    min: float | None = None
    max: float | None = None
    word_order: WordOrder = "big"


@dataclass(frozen=True, slots=True)
class WriteSpec:
    """Whitelist d'écriture. Ce qui n'est pas listé ici est interdit."""

    key: str
    address: RegisterAddress
    mode: WriteMode
    type: RegisterType | None = None  # mode single
    scale: float = 1.0  # mode single (le mode block porte l'échelle par champ)
    word_order: WordOrder = "big"  # mode single
    count: int = 1  # mode block
    layout: tuple[LayoutSpec, ...] = ()
    allowed: tuple[int, ...] | None = None  # valeurs discrètes autorisées
    preserve: bool = False  # relire/réécrire les offsets non pilotés


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    name: str
    protocol: str
    slave_id: SlaveId
    serial: SerialSettings
    blocks: tuple[BlockSpec, ...]
    writes: tuple[WriteSpec, ...]

    def write_spec(self, key: str) -> WriteSpec | None:
        return next((w for w in self.writes if w.key == key), None)
