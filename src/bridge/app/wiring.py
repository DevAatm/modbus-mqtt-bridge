"""Composition root : le SEUL endroit qui instancie les adaptateurs.

Assemble bus + transports + publishers + horloge, puis lance poll et
souscription commandes de chaque équipement comme tâches concurrentes d'un
même TaskGroup.

Plusieurs équipements sur un même lien (fichier `BUS`) :
  - UN `ModbusBus` (une connexion), une vue `ModbusTransport` par esclave ;
  - UN verrou partagé par tous les dispatchers : c'est lui qui préserve
    l'invariant mono-maître (une seule trame à la fois sur la ligne) ;
  - un dispatcher, un poller, une boucle de commandes PAR équipement ;
  - un client MQTT PAR équipement : un client n'a qu'un seul LWT, et c'est ce
    qui donne à chaque équipement une availability `offline` indépendante ;
  - un seul fichier de santé, rafraîchi dès qu'UN équipement a été lu (cf.
    `Poller`) : la perte d'un compteur est signalée par son availability
    MQTT, pas par un redémarrage du conteneur qui couperait les autres.
Mode `PROFILE` : un bus d'un seul équipement, comportement historique.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from typing import assert_never

from bridge.adapters.bus_yaml import load_bus, single_device_bus
from bridge.adapters.clock import SystemClock
from bridge.adapters.modbus import ModbusBus, PymodbusTransport, open_serial, open_tcp
from bridge.adapters.mqtt import MqttPublisher
from bridge.app.config import (
    BusFile,
    Config,
    DeviceSource,
    ProfileFile,
    SerialEndpoint,
    TcpEndpoint,
)
from bridge.app.dispatcher import BusDispatcher
from bridge.app.poller import Poller
from bridge.domain.bus import Bus
from bridge.domain.commands import WriteCommand, validate
from bridge.domain.profile import DeviceProfile, SerialSettings
from bridge.domain.result import Err
from bridge.domain.values import DeviceId
from bridge.ports.publisher import MessagePublisher

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _RunningDevice:
    """Un équipement assemblé : son publisher, son dispatcher, son poller."""

    profile: DeviceProfile
    publisher: MqttPublisher
    dispatcher: BusDispatcher
    poller: Poller


async def run(config: Config) -> None:
    """Démarre le service. Annulable ; nettoie publishers puis bus en sortie."""
    devices = _load_devices(config.devices)
    multi = len(devices.devices) > 1
    for profile in devices.devices:
        _log.info(
            "profil chargé: %s (%s), %d bloc(s), %d écriture(s) déclarée(s)",
            profile.name,
            profile.protocol,
            len(profile.blocks),
            len(profile.writes),
        )
    if config.read_only:
        _log.warning("service en LECTURE SEULE (READ_ONLY) : toute écriture sera refusée")

    # Bus.of a garanti des réglages série identiques : le premier vaut pour tous.
    bus = _open_bus(config, devices.devices[0].serial)
    transports = _transports(devices, bus)
    # Verrou du BUS, partagé par tous les dispatchers : invariant mono-maître.
    bus_lock = asyncio.Lock()
    clock = SystemClock()

    await bus.connect()
    for profile, _ in transports:
        # Préfixe vide en mono-équipement : logs de démarrage inchangés.
        _log.info(
            "%séquipement connecté via %s (slave %d)",
            _tag(profile, multi),
            bus,
            profile.slave_id.value,
        )
    try:
        async with contextlib.AsyncExitStack() as stack:
            running: list[_RunningDevice] = []
            for profile, transport in transports:
                publisher = MqttPublisher(
                    config.mqtt_host,
                    _device_id(profile),
                    prefix=config.mqtt_prefix,
                    port=config.mqtt_port,
                    username=config.mqtt_username,
                    password=config.mqtt_password,
                )
                await publisher.connect()
                # Fermé en sortie (publie offline), même si un équipement
                # suivant échoue à se connecter.
                stack.push_async_callback(publisher.close)
                _log.info(
                    "%sMQTT connecté sur %s:%d (préfixe %r)",
                    _tag(profile, multi),
                    config.mqtt_host,
                    config.mqtt_port,
                    config.mqtt_prefix,
                )
                await publisher.publish_discovery(profile)
                dispatcher = BusDispatcher(transport, publisher, bus_lock)
                poller = Poller(profile, dispatcher, publisher, clock, config.health_file)
                running.append(_RunningDevice(profile, publisher, dispatcher, poller))

            # Une tâche qui plante annule tout le groupe : le conteneur
            # redémarre, comme en mono-équipement.
            async with asyncio.TaskGroup() as tg:
                for device in running:
                    name = device.profile.name
                    tg.create_task(device.poller.run(), name=f"poller:{name}")
                    tg.create_task(
                        _command_loop(
                            device.publisher,
                            device.dispatcher,
                            device.profile,
                            read_only=config.read_only,
                        ),
                        name=f"commands:{name}",
                    )
    finally:
        await bus.close()


def _load_devices(source: DeviceSource) -> Bus:
    """`PROFILE` -> bus d'un équipement (nom et adresse du profil) ; `BUS` -> fichier."""
    if isinstance(source, ProfileFile):
        return single_device_bus(source.path)
    if isinstance(source, BusFile):
        return load_bus(source.path)
    assert_never(source)


def _transports(devices: Bus, bus: ModbusBus) -> list[tuple[DeviceProfile, PymodbusTransport]]:
    """Une vue par esclave, toutes sur le client unique du bus."""
    return [(profile, bus.for_slave(profile.slave_id)) for profile in devices.devices]


def _tag(profile: DeviceProfile, multi: bool) -> str:
    return f"{profile.name}: " if multi else ""


def _open_bus(config: Config, serial: SerialSettings) -> ModbusBus:
    """Choisit le lien. Le reste du service ne voit que des `ModbusTransport`,
    obtenus par `bus.for_slave(...)`.

    L'invariant mono-maître vaut pour les deux liens : en TCP l'équipement
    n'accepte souvent qu'une ou deux sockets, et derrière une passerelle le
    RS485 reste half-duplex. Le dispatcher sérialise donc dans tous les cas.
    """
    endpoint = config.endpoint
    if isinstance(endpoint, SerialEndpoint):
        return open_serial(endpoint.port, serial)
    if isinstance(endpoint, TcpEndpoint):
        return open_tcp(
            endpoint.host,
            endpoint.port,
            endpoint.framing,
            logger_serial=endpoint.logger_serial,
            timeout_s=endpoint.timeout_s,
        )
    assert_never(endpoint)  # exhaustivité vérifiée par mypy


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
