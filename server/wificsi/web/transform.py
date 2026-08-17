"""Pure CSI derivation functions.

These functions must stay free of I/O, asyncio and FastAPI imports so they can be
unit-tested in isolation and never block the UDP ingest path.
"""

from __future__ import annotations

import math
import struct


def iq_to_amplitude(iq: bytes, first_word_invalid: bool) -> tuple[float, ...]:
    """Convert signed int8 I/Q pairs to amplitudes.

    ESP-IDF CSI data is two's-complement bytes ordered imaginary, real. When
    ``first_word_invalid`` is true the leading 4 raw bytes are skipped. An odd
    trailing byte is dropped defensively (normal WCSI payloads are even).
    """
    if not iq:
        return ()
    values = struct.unpack(f"{len(iq)}b", iq)
    start = 4 if first_word_invalid and len(values) >= 4 else 0
    usable = values[start:]
    if len(usable) % 2:
        usable = usable[:-1]
    return tuple(
        math.hypot(usable[index + 1], usable[index])
        for index in range(0, len(usable), 2)
    )


def amplitude_stats(amplitude: tuple[float, ...]) -> tuple[float, float, float]:
    """Return ``(mean, rms, variance)`` for a non-empty amplitude vector."""
    if not amplitude:
        return (0.0, 0.0, 0.0)
    count = len(amplitude)
    mean = sum(amplitude) / count
    rms = math.sqrt(sum(value * value for value in amplitude) / count)
    variance = sum((value - mean) ** 2 for value in amplitude) / count
    return (mean, rms, variance)
