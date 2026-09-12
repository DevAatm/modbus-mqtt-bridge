"""Value objects immuables du domaine.

Construction par factory statique qui valide. Pas de primitive obsession :
une adresse de registre n'est pas un `int`, une grandeur n'est pas un `float`.
"""

from __future__ import annotations

from dataclasses import dataclass

from bridge.domain.result import Err, Ok, Result


@dataclass(frozen=True, slots=True)
class RegisterAddress:
    """Adresse de registre Modbus (0x0000..0xFFFF)."""

    value: int

    @staticmethod
    def parse(raw: int | str) -> Result[RegisterAddress, str]:
        try:
            value = int(raw, 0) if isinstance(raw, str) else int(raw)
        except (TypeError, ValueError):
            return Err(f"adresse de registre invalide: {raw!r}")
        if not 0 <= value <= 0xFFFF:
            return Err(f"adresse hors plage 16 bits: {value}")
        return Ok(RegisterAddress(value))

    def __str__(self) -> str:
        return f"0x{self.value:04X}"


@dataclass(frozen=True, slots=True)
class DeviceId:
    """Identité logique de l'équipement (utilisée dans les topics MQTT)."""

    name: str

    @staticmethod
    def parse(raw: str) -> Result[DeviceId, str]:
        name = raw.strip()
        if not name:
            return Err("nom d'équipement vide")
        # Contrainte topic MQTT : pas de séparateur ni de wildcard.
        if any(c in name for c in "/+#"):
            return Err(f"nom d'équipement invalide pour un topic: {name!r}")
        return Ok(DeviceId(name))

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True, slots=True)
class SlaveId:
    """Adresse d'esclave Modbus (1..247)."""

    value: int

    @staticmethod
    def parse(raw: int) -> Result[SlaveId, str]:
        if not 1 <= raw <= 247:
            return Err(f"slave_id hors plage Modbus (1..247): {raw}")
        return Ok(SlaveId(raw))


@dataclass(frozen=True, slots=True)
class Quantity:
    """Grandeur décodée en unité SI. La valeur brute est déjà mise à l'échelle."""

    value: float
    unit: str
