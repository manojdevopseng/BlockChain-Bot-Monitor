"""BoundedSet — a set with a maximum size and FIFO eviction.

Ported verbatim from the reference repo (utils/bounded_set.py). Used for dedup
guards that would otherwise grow forever on a 24/7 bot.
"""

from __future__ import annotations

from collections import deque
from typing import Iterable, Iterator, Optional


class BoundedSet:
    def __init__(self, maxlen: int, initial: Optional[Iterable[str]] = None) -> None:
        self._max = max(1, int(maxlen))
        self._set: set[str] = set()
        self._order: deque[str] = deque()
        for x in (initial or []):
            self.add(x)

    def add(self, key: str) -> None:
        if key in self._set:
            return
        self._set.add(key)
        self._order.append(key)
        while len(self._order) > self._max:
            old = self._order.popleft()
            self._set.discard(old)

    def discard(self, key: str) -> None:
        if key in self._set:
            self._set.discard(key)
            try:
                self._order.remove(key)
            except ValueError:
                pass

    def __contains__(self, key: object) -> bool:
        return key in self._set

    def __len__(self) -> int:
        return len(self._set)

    def __iter__(self) -> Iterator[str]:
        return iter(self._set)
