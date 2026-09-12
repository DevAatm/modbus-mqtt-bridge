"""Décodage : registres bruts -> grandeurs SI. Fonctions pures.

C'est ici que va l'essentiel des tests (tables `registres -> grandeur attendue`
dérivées de relevés réels). Aucune dépendance externe : `struct` et des
opérateurs de décalage suffisent pour du 16 bits.
"""

from __future__ import annotations

from bridge.domain.profile import PointSpec, RegisterType, WordOrder
from bridge.domain.result import Err, Ok, Result
from bridge.domain.values import Quantity

# Nombre de registres 16 bits consommés par type.
_WIDTH: dict[RegisterType, int] = {"u16": 1, "s16": 1, "u32": 2, "s32": 2}


def register_width(type_: RegisterType) -> int:
    return _WIDTH[type_]


def _combine(regs: tuple[int, ...], word_order: WordOrder) -> int:
    """Concatène des registres 16 bits en un entier non signé."""
    ordered = regs if word_order == "big" else tuple(reversed(regs))
    acc = 0
    for r in ordered:
        acc = (acc << 16) | (r & 0xFFFF)
    return acc


def _to_signed(value: int, bits: int) -> int:
    sign_bit = 1 << (bits - 1)
    return value - (1 << bits) if value & sign_bit else value


def decode_point(registers: tuple[int, ...], spec: PointSpec) -> Result[Quantity, str]:
    """Décode un point à partir de la fenêtre de registres d'un bloc.

    `registers` est le bloc complet ; le point lit à partir de `spec.offset`.
    """
    width = register_width(spec.type)
    end = spec.offset + width
    if end > len(registers):
        return Err(
            f"point {spec.key!r}: offset {spec.offset}+{width} déborde "
            f"du bloc de {len(registers)} registres"
        )

    window = tuple(registers[spec.offset : end])
    raw = _combine(window, spec.word_order)

    if spec.type in ("s16", "s32"):
        raw = _to_signed(raw, 16 * width)

    return Ok(Quantity(value=raw * spec.scale, unit=spec.unit))
