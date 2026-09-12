"""Configuration runtime, lue de l'environnement. Read-only par défaut."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Config:
    serial_port: str
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
            serial_port=os.environ["SERIAL_PORT"],
            profile_path=os.environ["PROFILE"],
            # Sécurité : read_only actif tant que non explicitement désactivé.
            read_only=os.environ.get("READ_ONLY", "true").lower() != "false",
            health_file=os.environ.get("HEALTH_FILE", "/tmp/bridge-health"),
            mqtt_host=os.environ["MQTT_HOST"],
            mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
            mqtt_username=os.environ.get("MQTT_USERNAME"),
            mqtt_password=os.environ.get("MQTT_PASSWORD"),
            mqtt_prefix=os.environ.get("MQTT_PREFIX", "modbus"),
        )
