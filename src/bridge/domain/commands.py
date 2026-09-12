"""Commandes d'écriture : validation, clamp, whitelist. Fonctions pures.

Sécurité non négociable (cf. CLAUDE.md) :
  1. Whitelist  — seules les clés déclarées dans `writes:` sont écrivables.
  2. Clamp/validation — min/max/allowed appliqués avant tout envoi ;
     hors bornes = rejet explicite, jamais de troncature silencieuse.

Une commande peut être scalaire (mono-champ) ou structurée (JSON objet
`{champ: valeur}`) pour piloter plusieurs offsets d'un même bloc en une seule
transaction 0x10. La conversion grandeur SI -> registres bruts et la stratégie
`preserve` relèvent de l'adaptateur d'écriture, via le `PlannedWrite` validé ici.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from bridge.domain.profile import DeviceProfile, LayoutSpec, WriteMode, WriteSpec
from bridge.domain.result import Err, Ok, Result


@dataclass(frozen=True, slots=True)
class WriteCommand:
    """Demande brute reçue de MQTT, avant validation.

    Exactement un des deux est renseigné : `scalar` (charge utile numérique) ou
    `fields` (charge utile JSON objet, champ -> valeur).
    """

    key: str
    scalar: float | None = None
    fields: dict[str, float] | None = None

    @staticmethod
    def of_scalar(key: str, value: float) -> WriteCommand:
        return WriteCommand(key=key, scalar=value)

    @staticmethod
    def of_fields(key: str, fields: dict[str, float]) -> WriteCommand:
        return WriteCommand(key=key, fields=fields)


class RejectReason(Enum):
    UNKNOWN_KEY = "clé inconnue (hors whitelist)"
    OUT_OF_RANGE = "valeur hors bornes"
    NOT_ALLOWED = "valeur non autorisée"
    READ_ONLY = "service en lecture seule"
    BAD_PAYLOAD = "charge utile invalide"
    UNKNOWN_FIELD = "champ inconnu"
    MISSING_FIELD = "champ obligatoire manquant (bloc sans preserve)"


@dataclass(frozen=True, slots=True)
class Rejected:
    reason: RejectReason
    detail: str


@dataclass(frozen=True, slots=True)
class PlannedWrite:
    """Commande validée, prête à être planifiée par le dispatcher.

    `values` mappe une clé de champ vers sa valeur SI. En mode single, la seule
    entrée est `{spec.key: valeur}`. En mode block, une entrée par champ piloté.
    """

    spec: WriteSpec
    values: dict[str, float]


def validate(
    command: WriteCommand,
    profile: DeviceProfile,
    *,
    read_only: bool,
) -> Result[PlannedWrite, Rejected]:
    if read_only:
        return Err(Rejected(RejectReason.READ_ONLY, command.key))

    spec = profile.write_spec(command.key)
    if spec is None:
        return Err(Rejected(RejectReason.UNKNOWN_KEY, command.key))

    if spec.mode is WriteMode.SINGLE:
        return _validate_single(spec, command)
    return _validate_block(spec, command)


def _validate_single(spec: WriteSpec, command: WriteCommand) -> Result[PlannedWrite, Rejected]:
    if command.fields is not None:
        return Err(Rejected(RejectReason.BAD_PAYLOAD, "écriture single attend un scalaire"))
    value = command.scalar
    if value is None:
        return Err(Rejected(RejectReason.BAD_PAYLOAD, "valeur manquante"))

    if spec.allowed is not None and int(value) not in spec.allowed:
        return Err(Rejected(RejectReason.NOT_ALLOWED, f"{value} pas dans {list(spec.allowed)}"))

    return Ok(PlannedWrite(spec=spec, values={spec.key: value}))


def _validate_block(spec: WriteSpec, command: WriteCommand) -> Result[PlannedWrite, Rejected]:
    by_key = {field.key: field for field in spec.layout}

    # Résolution scalaire -> {champ unique: valeur}.
    if command.scalar is not None:
        if len(spec.layout) != 1:
            return Err(
                Rejected(
                    RejectReason.BAD_PAYLOAD,
                    "bloc multi-champ : charge utile JSON {champ: valeur} requise",
                )
            )
        provided = {spec.layout[0].key: command.scalar}
    elif command.fields is not None:
        provided = dict(command.fields)
    else:
        return Err(Rejected(RejectReason.BAD_PAYLOAD, "charge utile vide"))

    # Champs inconnus rejetés (whitelist au niveau champ).
    for field_key in provided:
        if field_key not in by_key:
            return Err(Rejected(RejectReason.UNKNOWN_FIELD, field_key))

    # Bornes par champ.
    for field_key, value in provided.items():
        if _field_out_of_range(by_key[field_key], value):
            return Err(Rejected(RejectReason.OUT_OF_RANGE, f"{field_key}={value}"))

    # Couverture : sans preserve, tout champ non fourni serait écrit à 0.
    if not spec.preserve:
        missing = [f.key for f in spec.layout if f.key not in provided]
        if missing:
            return Err(Rejected(RejectReason.MISSING_FIELD, ", ".join(missing)))

    return Ok(PlannedWrite(spec=spec, values=provided))


def _field_out_of_range(field: LayoutSpec, value: float) -> bool:
    lo, hi = field.min, field.max
    return (lo is not None and value < lo) or (hi is not None and value > hi)
