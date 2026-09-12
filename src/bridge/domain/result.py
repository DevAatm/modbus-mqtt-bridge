"""Result[T, E] minimal — erreurs typées dans le domaine.

Les exceptions restent aux frontières d'I/O (adaptateurs), converties en
`Result` par l'adaptateur. Le domaine ne lève pas d'exception traversante.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Ok[T]:
    value: T

    def is_ok(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class Err[E]:
    error: E

    def is_ok(self) -> bool:
        return False


type Result[T, E] = Ok[T] | Err[E]
