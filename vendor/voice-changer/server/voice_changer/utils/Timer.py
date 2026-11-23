from time import perf_counter
from typing import Dict, List


class Timer2:
    """Lightweight perf_counter context manager safe for real-time audio callbacks
    (no inspect stack-walking or frame reference cycles)."""

    storedSecs: Dict[str, Dict[str, List[float]]] = {}

    def __init__(self, title: str, enable: bool = True):
        self.title = title
        self.enable = enable
        self.secs = 0.0
        self.start = 0.0
        self.now = 0.0
        if enable and title not in self.storedSecs:
            self.storedSecs[title] = {}

    def __enter__(self):
        if self.enable:
            self.start = perf_counter()
            self.now = self.start
        return self

    def record(self, lapname: str):
        if not self.enable:
            return
        prev = self.now
        self.now = perf_counter()
        laps = self.storedSecs[self.title].setdefault(lapname, [])
        laps.append(self.now - prev)
        if len(laps) > 10:
            del laps[:-10]

    def __exit__(self, *_):
        if self.enable:
            self.secs = perf_counter() - self.start

