from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CSI_PREFIX = "CSI_DATA,"


@dataclass(frozen=True)
class CsiFrame:
    mac: str
    rssi: int
    channel: int
    local_timestamp_us: int
    length: int
    first_word_invalid: bool
    iq: tuple[int, ...]


@dataclass(frozen=True)
class CsiSummary:
    frames: int
    invalid_lines: int
    lengths: dict[int, int]
    macs: tuple[str, ...]
    channels: tuple[int, ...]
    duration_seconds: float
    sample_rate_hz: float


@dataclass(frozen=True)
class SensingSummary:
    active_events: int
    inactive_events: int
    ap_peers: tuple[str, ...]


@dataclass(frozen=True)
class GateResult:
    passed: bool
    failures: tuple[str, ...]


def _csi_csv(line: str) -> str | None:
    clean = _ANSI_ESCAPE.sub("", line)
    start = clean.find(_CSI_PREFIX)
    return None if start < 0 else clean[start:].strip()


def parse_csi_line(line: str) -> CsiFrame | None:
    payload = _csi_csv(line)
    if payload is None:
        return None

    try:
        row = next(csv.reader([payload]))
        if len(row) < 25 or row[0] != "CSI_DATA":
            return None

        length = int(row[22])
        first_word = int(row[23])
        decoded = json.loads(row[24])
        if first_word not in (0, 1):
            return None
        if not isinstance(decoded, list) or len(decoded) != length:
            return None
        if any(type(value) is not int or not -128 <= value <= 127 for value in decoded):
            return None

        return CsiFrame(
            mac=row[2].lower(),
            rssi=int(row[3]),
            channel=int(row[16]),
            local_timestamp_us=int(row[18]),
            length=length,
            first_word_invalid=bool(first_word),
            iq=tuple(decoded),
        )
    except (csv.Error, json.JSONDecodeError, TypeError, ValueError):
        return None


def summarize_csi(lines: Iterable[str]) -> CsiSummary:
    frames: list[CsiFrame] = []
    invalid_lines = 0

    for line in lines:
        if _csi_csv(line) is None:
            continue
        frame = parse_csi_line(line)
        if frame is None:
            invalid_lines += 1
        else:
            frames.append(frame)

    duration_seconds = 0.0
    if len(frames) >= 2:
        duration_seconds = max(
            0.0,
            (frames[-1].local_timestamp_us - frames[0].local_timestamp_us) / 1_000_000,
        )
    sample_rate_hz = (
        (len(frames) - 1) / duration_seconds if duration_seconds > 0 else 0.0
    )

    return CsiSummary(
        frames=len(frames),
        invalid_lines=invalid_lines,
        lengths=dict(sorted(Counter(frame.length for frame in frames).items())),
        macs=tuple(sorted({frame.mac for frame in frames})),
        channels=tuple(sorted({frame.channel for frame in frames})),
        duration_seconds=duration_seconds,
        sample_rate_hz=sample_rate_hz,
    )


def summarize_sensing(lines: Iterable[str]) -> SensingSummary:
    event_pattern = re.compile(
        r"\[AP\]\s+(ACTIVE|INACTIVE)\s+peer=([0-9a-fA-F:]{17})"
    )
    active_events = 0
    inactive_events = 0
    peers: set[str] = set()

    for line in lines:
        match = event_pattern.search(_ANSI_ESCAPE.sub("", line))
        if match is None:
            continue
        event, peer = match.groups()
        peers.add(peer.lower())
        if event == "ACTIVE":
            active_events += 1
        else:
            inactive_events += 1

    return SensingSummary(
        active_events=active_events,
        inactive_events=inactive_events,
        ap_peers=tuple(sorted(peers)),
    )


def stage_gate(
    csi: CsiSummary,
    sensing: SensingSummary,
    *,
    minimum_csi_frames: int,
) -> GateResult:
    failures: list[str] = []
    if csi.frames < minimum_csi_frames:
        failures.append(
            f"valid CSI frames {csi.frames} below minimum {minimum_csi_frames}"
        )
    if not csi.lengths:
        failures.append("no CSI lengths observed")
    if csi.sample_rate_hz <= 0:
        failures.append("CSI sample rate is not positive")
    if sensing.active_events == 0:
        failures.append("missing AP ACTIVE event")
    if sensing.inactive_events == 0:
        failures.append("missing AP INACTIVE event")

    return GateResult(passed=not failures, failures=tuple(failures))


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate official ESP-CSI evidence")
    subparsers = parser.add_subparsers(dest="command", required=True)

    csi_parser = subparsers.add_parser("csi", help="summarize raw CSI evidence")
    csi_parser.add_argument("log", type=Path)
    csi_parser.add_argument("--min-frames", type=int, default=100)

    sensing_parser = subparsers.add_parser(
        "sensing", help="summarize official AP sensing events"
    )
    sensing_parser.add_argument("log", type=Path)

    gate_parser = subparsers.add_parser(
        "stage-gate", help="validate raw CSI and sensing evidence together"
    )
    gate_parser.add_argument("--csi-log", type=Path, required=True)
    gate_parser.add_argument("--sensing-log", type=Path, required=True)
    gate_parser.add_argument("--min-frames", type=int, default=100)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "csi":
        csi = summarize_csi(_read_lines(args.log))
        print(json.dumps(asdict(csi), ensure_ascii=False, sort_keys=True))
        return 0 if (
            csi.frames >= args.min_frames
            and bool(csi.lengths)
            and csi.sample_rate_hz > 0
        ) else 1

    if args.command == "sensing":
        sensing = summarize_sensing(_read_lines(args.log))
        print(json.dumps(asdict(sensing), ensure_ascii=False, sort_keys=True))
        return 0 if (
            sensing.active_events > 0
            and sensing.inactive_events > 0
            and bool(sensing.ap_peers)
        ) else 1

    csi = summarize_csi(_read_lines(args.csi_log))
    sensing = summarize_sensing(_read_lines(args.sensing_log))
    result = stage_gate(csi, sensing, minimum_csi_frames=args.min_frames)
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
