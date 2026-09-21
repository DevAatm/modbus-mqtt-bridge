"""Configuration runtime, lue de l'environnement. Read-only par défaut.

Le profil décrit l'ÉQUIPEMENT, l'environnement décrit le DÉPLOIEMENT. Le lien
physique (port série ou host:port TCP) appartient donc ici : le même profil
sert un onduleur câblé en RS485 et le même modèle atteint via une passerelle.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, get_args

# `socket` = Modbus TCP natif (en-tête MBAP).
# `rtu`    = passerelle transparente TCP<->RS485 (trames RTU encapsulées).
TcpFraming = Literal["socket", "rtu"]

_FRAMINGS: frozenset[str] = frozenset(get_args(TcpFraming))


class ConfigError(Exception):
    """Configuration invalide. Levée avant tout démarrage."""


@dataclass(frozen=True, slots=True)
class SerialEndpoint:
    port: str

    def __str__(self) -> str:
        return f"série {self.port}"


@dataclass(frozen=True, slots=True)
class TcpEndpoint:
    host: str
    port: int
    framing: TcpFraming
    timeout_s: float

    def __str__(self) -> str:
        return f"tcp {self.host}:{self.port} ({self.framing})"


# Union discriminée : pas d'état invalide représentable (pas de host sans port,
# pas de port série qui traîne dans une configuration TCP).
ModbusEndpoint = SerialEndpoint | TcpEndpoint


@dataclass(frozen=True, slots=True)
class Config:
    endpoint: ModbusEndpoint
    profile_path: str
    read_only: bool
    health_file: str
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None
    mqtt_prefix: str

    @staticmethod
    def from_env() -> Config:
        return Config(
            endpoint=_endpoint_from_env(),
            profile_path=_require("PROFILE"),
            # Sécurité : read_only actif tant que non explicitement désactivé.
            read_only=os.environ.get("READ_ONLY", "true").lower() != "false",
            health_file=os.environ.get("HEALTH_FILE", "/tmp/bridge-health"),
            mqtt_host=_require("MQTT_HOST"),
            mqtt_port=_int("MQTT_PORT", 1883),
            mqtt_username=os.environ.get("MQTT_USERNAME"),
            mqtt_password=os.environ.get("MQTT_PASSWORD"),
            mqtt_prefix=os.environ.get("MQTT_PREFIX", "modbus"),
        )


def _endpoint_from_env() -> ModbusEndpoint:
    """MODBUS_TRANSPORT=serial|tcp. Défaut `serial` : déploiements existants inchangés."""
    kind = os.environ.get("MODBUS_TRANSPORT", "serial").strip().lower()
    if kind == "serial":
        return SerialEndpoint(port=_require("SERIAL_PORT", because="MODBUS_TRANSPORT=serial"))
    if kind == "tcp":
        framing = os.environ.get("MODBUS_FRAMING", "socket").strip().lower()
        if framing not in _FRAMINGS:
            raise ConfigError(
                f"MODBUS_FRAMING invalide: {framing!r} (attendu {sorted(_FRAMINGS)}) — "
                "'socket' pour un équipement Modbus TCP natif, "
                "'rtu' pour une passerelle transparente TCP<->RS485"
            )
        return TcpEndpoint(
            host=_require("MODBUS_HOST", because="MODBUS_TRANSPORT=tcp"),
            port=_int("MODBUS_PORT", 502),
            framing=framing,  # type: ignore[arg-type]  # validé ci-dessus
            timeout_s=_float("MODBUS_TIMEOUT", 3.0),
        )
    raise ConfigError(f"MODBUS_TRANSPORT invalide: {kind!r} (attendu 'serial' ou 'tcp')")


def _require(name: str, *, because: str | None = None) -> str:
    value = os.environ.get(name)
    if not value:
        suffix = f" ({because})" if because else ""
        raise ConfigError(f"variable d'environnement requise manquante: {name}{suffix}")
    return value


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name}: entier attendu, reçu {raw!r}") from None


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name}: nombre attendu, reçu {raw!r}") from None
