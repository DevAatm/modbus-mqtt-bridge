"""Composition root : le SEUL endroit qui instancie les adaptateurs.

Assemble profil + transport + publisher + horloge, puis lance poll et
souscription commandes comme tâches concurrentes d'un même TaskGroup.
"""

from __future__ import annotations

import asyncio
import json
import logging

from bridge.adapters.clock import SystemClock
from bridge.adapters.modbus_rtu import PymodbusTransport
from bridge.adapters.mqtt import MqttPublisher
from bridge.adapters.profile_yaml import load_profile
from bridge.app.config import Config
from bridge.app.dispatcher import BusDispatcher
from bridge.app.poller import Poller
from bridge.domain.commands import WriteCommand, validate
from bridge.domain.profile import DeviceProfile
from bridge.domain.result import Err
from bridge.domain.values import DeviceId
from bridge.ports.publisher import MessagePublisher

_log = logging.getLogger(__name__)


async def run(config: Config) -> None:
    """Démarre le service. Annulable ; nettoie transport et publisher en sortie."""
    profile = load_profile(config.profile_path)
    device = _device_id(profile)
    _log.info(
        "profil chargé: %s (%s), %d bloc(s), %d écriture(s) déclarée(s)",
        profile.name,
        profile.protocol,
        len(profile.blocks),
        len(profile.writes),
    )
    if config.read_only:
        _log.warning("service en LECTURE SEULE (READ_ONLY) : toute écriture sera refusée")

    transport = PymodbusTransport(config.serial_port, profile.serial, profile.slave_id)
    publisher = MqttPublisher(
        config.mqtt_host,
        device,
        prefix=config.mqtt_prefix,
        port=config.mqtt_port,
        username=config.mqtt_username,
        password=config.mqtt_password,
    )
    clock = SystemClock()

    await transport.connect()
    _log.info("bus série connecté sur %s (slave %d)", config.serial_port, profile.slave_id.value)
    try:
        await publisher.connect()
        _log.info(
            "MQTT connecté sur %s:%d (préfixe %r)",
            config.mqtt_host,
            config.mqtt_port,
            config.mqtt_prefix,
        )
        try:
            await publisher.publish_discovery(profile)
            dispatcher = BusDispatcher(transport, publisher)
            poller = Poller(profile, dispatcher, publisher, clock, config.health_file)
            async with asyncio.TaskGroup() as tg:
                tg.create_task(poller.run(), name="poller")
                tg.create_task(
                    _command_loop(publisher, dispatcher, profile, read_only=config.read_only),
                    name="commands",
                )
        finally:
            await publisher.close()  # publie offline
    finally:
        await transport.close()


def _device_id(profile: DeviceProfile) -> DeviceId:
    result = DeviceId.parse(profile.name)
    if isinstance(result, Err):
        raise ValueError(f"nom d'équipement invalide dans le profil: {result.error}")
    return result.value


async def _command_loop(
    publisher: MessagePublisher,
    dispatcher: BusDispatcher,
    profile: DeviceProfile,
    *,
    read_only: bool,
) -> None:
    """Reçoit les commandes MQTT, valide, et met en file. Acquitte les rejets.

    L'acquittement des commandes acceptées est émis après exécution par le
    dispatcher (`accepted` / `failed`). Ici on n'acquitte que les rejets.
    """
    async for command in publisher.commands():
        parsed = _parse_command(command.key, command.payload)
        if parsed is None:
            await publisher.publish_command_result(
                command.key, "rejected: charge utile invalide (scalaire ou objet JSON attendu)"
            )
            continue

        result = validate(parsed, profile, read_only=read_only)
        if isinstance(result, Err):
            reason = result.error
            await publisher.publish_command_result(
                command.key, f"rejected: {reason.reason.value} ({reason.detail})"
            )
        else:
            dispatcher.enqueue_write(result.value)


def _parse_command(key: str, payload: str) -> WriteCommand | None:
    """Charge utile scalaire (`-2000`) ou objet JSON (`{"active_power": -2000}`)."""
    text = payload.strip()
    try:
        return WriteCommand.of_scalar(key, float(text))
    except (TypeError, ValueError):
        pass
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or not obj:
        return None
    try:
        fields = {str(k): float(v) for k, v in obj.items()}
    except (TypeError, ValueError):
        return None
    return WriteCommand.of_fields(key, fields)
