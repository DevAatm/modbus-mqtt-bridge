"""Adaptateur MQTT (aiomqtt) -> MessagePublisher + souscription commandes.

Topics (unités SI, un topic par point) :
    <prefix>/<device>/state/<key>          # retain
    <prefix>/<device>/availability         # LWT, retain
    <prefix>/<device>/command/<key>        # demandes d'écriture
    <prefix>/<device>/command/<key>/result # acquittement
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from aiomqtt import Client, Will

from bridge.domain.profile import DeviceProfile
from bridge.domain.values import DeviceId
from bridge.ports.publisher import IncomingCommand

_log = logging.getLogger(__name__)


class MqttPublisher:
    """Implémente `MessagePublisher` via aiomqtt. LWT `offline` obligatoire."""

    def __init__(
        self,
        host: str,
        device: DeviceId,
        prefix: str = "modbus",
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._device = device
        self._prefix = prefix
        # Client ID stable dérivé du nom d'équipement : le broker reconnaît le
        # client d'une connexion à l'autre, et un doublon devient visible.
        self._client = Client(
            hostname=host,
            port=port,
            identifier=f"{prefix}-{device}",
            username=username,
            password=password,
            will=Will(
                topic=self._availability_topic(),
                payload="offline",
                qos=1,
                retain=True,
            ),
        )

    # --- topics ---
    def _state_topic(self, key: str) -> str:
        return f"{self._prefix}/{self._device}/state/{key}"

    def _availability_topic(self) -> str:
        return f"{self._prefix}/{self._device}/availability"

    def _command_wildcard(self) -> str:
        # '+' = un seul niveau : ne matche pas command/<key>/result.
        return f"{self._prefix}/{self._device}/command/+"

    def _result_topic(self, key: str) -> str:
        return f"{self._prefix}/{self._device}/command/{key}/result"

    # --- cycle de vie ---
    async def connect(self) -> None:
        await self._client.__aenter__()
        await self._client.subscribe(self._command_wildcard(), qos=1)
        await self.publish_availability(True)

    async def close(self) -> None:
        try:
            await self.publish_availability(False)
        finally:
            await self._client.__aexit__(None, None, None)

    # --- publication ---
    async def publish_state(self, key: str, value: float, unit: str) -> None:
        await self._client.publish(self._state_topic(key), payload=repr(value), retain=True)

    async def publish_availability(self, online: bool) -> None:
        await self._client.publish(
            self._availability_topic(),
            payload="online" if online else "offline",
            qos=1,
            retain=True,
        )

    async def publish_command_result(self, key: str, result: str) -> None:
        await self._client.publish(self._result_topic(key), payload=result, qos=1)

    async def commands(self) -> AsyncIterator[IncomingCommand]:
        async for message in self._client.messages:
            topic = message.topic.value
            key = topic.rsplit("/", 1)[-1]
            payload = message.payload
            text = payload.decode() if isinstance(payload, bytes) else str(payload)
            yield IncomingCommand(key=key, payload=text)

    # --- découverte Home Assistant ---
    async def publish_discovery(self, profile: DeviceProfile) -> None:
        """Publie les configs de découverte HA générées depuis le profil.

        Ajouter un point au YAML suffit alors à créer l'entité côté HA.
        """
        device_block = {
            "identifiers": [str(self._device)],
            "name": str(self._device),
            "model": profile.protocol,
        }
        for block in profile.blocks:
            for point in block.points:
                unique_id = f"{self._device}_{point.key}"
                config = {
                    "name": point.key,
                    "unique_id": unique_id,
                    "state_topic": self._state_topic(point.key),
                    "availability_topic": self._availability_topic(),
                    "device": device_block,
                }
                if point.unit:
                    config["unit_of_measurement"] = point.unit
                topic = f"homeassistant/sensor/{unique_id}/config"
                await self._client.publish(topic, payload=json.dumps(config), retain=True)
        _log.info("découverte HA publiée pour %s", self._device)
