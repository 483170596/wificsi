"""Strict WCSI version 1 binary protocol codec."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum
from typing import Literal, TypeAlias


MAGIC = b"WCSI"
MAJOR = 1
MINOR = 0
MAX_DATAGRAM = 1200
COMMON_HEADER = struct.Struct("!4sBBBBHH6sHIIQI")
HELLO_STRUCT = struct.Struct("!HBBBBHHHHHH6s")
CSI_PREFIX = struct.Struct("!6s6sbbBBIHHBBBBHBBB3x")
SENSING_STRUCT = struct.Struct("!6sBBBBBBffIIIfffffI")
HEARTBEAT_STRUCT = struct.Struct("!QIIIIII")
COMMAND_PREFIX = struct.Struct("!IB3x")
ACK_PREFIX = struct.Struct("!IBB2x")
CONFIG_BODY = struct.Struct("!fffI")


class MessageType(IntEnum):
    HELLO = 1
    CSI_FRAME = 2
    SENSING_STATE = 3
    HEARTBEAT = 4
    COMMAND = 5
    COMMAND_ACK = 6


class StableState(IntEnum):
    INACTIVE = 0
    ACTIVE = 1
    UNKNOWN = 2


class ProcessState(IntEnum):
    IDLE = 0
    DEBOUNCE_ACTIVE = 1
    ACTIVE = 2
    DEBOUNCE_INACTIVE = 3


class InitStage(IntEnum):
    START = 0
    GOLD_FOUND = 1
    STABLE = 2


class CommandOpcode(IntEnum):
    GET_CONFIG = 1
    RESET_BASELINE = 2
    SET_CONFIG = 3


class AckStatus(IntEnum):
    OK = 0
    INVALID_ARGUMENT = 1
    NOT_READY = 2
    UNSUPPORTED = 3
    INTERNAL_ERROR = 4


class ProtocolError(ValueError):
    """A datagram violated a named part of the WCSI contract."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code


@dataclass(frozen=True, slots=True)
class Header:
    node_id: bytes
    boot_id: int
    sequence: int
    device_time_us: int
    minor: int = MINOR
    flags: int = 0
    extension: bytes = b""


@dataclass(frozen=True, slots=True)
class SensingConfig:
    motion_sensitivity: float
    presence_sensitivity: float
    active_jitter_min: float
    active_filter_ms: int


@dataclass(frozen=True, slots=True)
class Hello:
    chip_model: int
    firmware_major: int
    firmware_minor: int
    firmware_patch: int
    capabilities: int
    max_csi_length: int
    queue_capacity: int
    heartbeat_interval_ms: int
    state_interval_ms: int
    listen_port: int
    ap_bssid: bytes


@dataclass(frozen=True, slots=True)
class CsiFrame:
    source_mac: bytes
    destination_mac: bytes
    rssi: int
    noise_floor: int
    channel: int
    secondary_channel: int
    rx_timestamp: int
    signal_length: int
    rate: int
    signal_mode: int
    mcs: int
    bandwidth: int
    phy_flags: int
    antenna: int
    rx_state: int
    first_word_invalid: bool
    iq: bytes


@dataclass(frozen=True, slots=True)
class SensingState:
    peer_mac: bytes
    stable_state: StableState
    process_state: ProcessState
    init_stage: InitStage
    flags: int
    reason: int
    jitter: float
    wander: float
    smooth_scaled: int
    enter_level_scaled: int
    exit_level_scaled: int
    presence_wander_average: float
    presence_someone_threshold: float
    config: SensingConfig


@dataclass(frozen=True, slots=True)
class Heartbeat:
    uptime_us: int
    free_heap: int
    minimum_free_heap: int
    csi_accepted: int
    csi_sent: int
    queue_dropped: int
    udp_send_errors: int


@dataclass(frozen=True, slots=True)
class Command:
    correlation_id: int
    opcode: CommandOpcode
    config: SensingConfig | None = None


@dataclass(frozen=True, slots=True)
class CommandAck:
    correlation_id: int
    opcode: CommandOpcode
    status: AckStatus
    config: SensingConfig | None = None


Payload: TypeAlias = Hello | CsiFrame | SensingState | Heartbeat | Command | CommandAck


@dataclass(frozen=True, slots=True)
class Packet:
    header: Header
    payload: Payload


def _require_mac(value: bytes, field: str) -> bytes:
    if len(value) != 6:
        raise ProtocolError("payload", f"{field} must contain 6 bytes")
    return value


def _message_type(payload: Payload) -> MessageType:
    mapping = {
        Hello: MessageType.HELLO,
        CsiFrame: MessageType.CSI_FRAME,
        SensingState: MessageType.SENSING_STATE,
        Heartbeat: MessageType.HEARTBEAT,
        Command: MessageType.COMMAND,
        CommandAck: MessageType.COMMAND_ACK,
    }
    try:
        return mapping[type(payload)]
    except KeyError as error:
        raise ProtocolError("message_type", "unsupported payload class") from error


def _encode_config(config: SensingConfig) -> bytes:
    try:
        return CONFIG_BODY.pack(
            config.motion_sensitivity,
            config.presence_sensitivity,
            config.active_jitter_min,
            config.active_filter_ms,
        )
    except (OverflowError, struct.error) as error:
        raise ProtocolError("payload", "invalid sensing config") from error


def _decode_config(data: bytes) -> SensingConfig:
    if len(data) != CONFIG_BODY.size:
        raise ProtocolError("payload", "config body must contain 16 bytes")
    return SensingConfig(*CONFIG_BODY.unpack(data))


def _encode_payload(payload: Payload) -> bytes:
    try:
        if isinstance(payload, Hello):
            return HELLO_STRUCT.pack(
                payload.chip_model,
                payload.firmware_major,
                payload.firmware_minor,
                payload.firmware_patch,
                0,
                payload.capabilities,
                payload.max_csi_length,
                payload.queue_capacity,
                payload.heartbeat_interval_ms,
                payload.state_interval_ms,
                payload.listen_port,
                _require_mac(payload.ap_bssid, "ap_bssid"),
            )
        if isinstance(payload, CsiFrame):
            if not payload.iq:
                raise ProtocolError("payload", "CSI data must not be empty")
            prefix = CSI_PREFIX.pack(
                _require_mac(payload.source_mac, "source_mac"),
                _require_mac(payload.destination_mac, "destination_mac"),
                payload.rssi,
                payload.noise_floor,
                payload.channel,
                payload.secondary_channel,
                payload.rx_timestamp,
                payload.signal_length,
                len(payload.iq),
                payload.rate,
                payload.signal_mode,
                payload.mcs,
                payload.bandwidth,
                payload.phy_flags,
                payload.antenna,
                payload.rx_state,
                int(payload.first_word_invalid),
            )
            return prefix + payload.iq
        if isinstance(payload, SensingState):
            return SENSING_STRUCT.pack(
                _require_mac(payload.peer_mac, "peer_mac"),
                int(payload.stable_state),
                int(payload.process_state),
                int(payload.init_stage),
                payload.flags,
                payload.reason,
                0,
                payload.jitter,
                payload.wander,
                payload.smooth_scaled,
                payload.enter_level_scaled,
                payload.exit_level_scaled,
                payload.presence_wander_average,
                payload.presence_someone_threshold,
                payload.config.motion_sensitivity,
                payload.config.presence_sensitivity,
                payload.config.active_jitter_min,
                payload.config.active_filter_ms,
            )
        if isinstance(payload, Heartbeat):
            return HEARTBEAT_STRUCT.pack(
                payload.uptime_us,
                payload.free_heap,
                payload.minimum_free_heap,
                payload.csi_accepted,
                payload.csi_sent,
                payload.queue_dropped,
                payload.udp_send_errors,
            )
        if isinstance(payload, Command):
            prefix = COMMAND_PREFIX.pack(payload.correlation_id, int(payload.opcode))
            if payload.opcode is CommandOpcode.SET_CONFIG:
                if payload.config is None:
                    raise ProtocolError("payload", "SET_CONFIG requires config")
                return prefix + _encode_config(payload.config)
            if payload.config is not None:
                raise ProtocolError("payload", "command body is not allowed")
            return prefix
        if isinstance(payload, CommandAck):
            prefix = ACK_PREFIX.pack(
                payload.correlation_id,
                int(payload.opcode),
                int(payload.status),
            )
            if payload.config is None:
                return prefix
            if payload.opcode not in (CommandOpcode.GET_CONFIG, CommandOpcode.SET_CONFIG):
                raise ProtocolError("payload", "ACK config is not allowed")
            return prefix + _encode_config(payload.config)
    except (OverflowError, struct.error, ValueError) as error:
        if isinstance(error, ProtocolError):
            raise
        raise ProtocolError("payload", "field does not fit wire representation") from error
    raise ProtocolError("message_type", "unsupported payload class")


def encode_packet(packet: Packet) -> bytes:
    header = packet.header
    _require_mac(header.node_id, "node_id")
    if header.boot_id == 0:
        raise ProtocolError("boot_id", "boot ID must be nonzero")
    if header.minor == 0 and header.extension:
        raise ProtocolError("header_length", "minor zero cannot extend the header")
    if header.flags != 0:
        raise ProtocolError("flags", "version 1 flags must be zero")

    payload = _encode_payload(packet.payload)
    header_length = COMMON_HEADER.size + len(header.extension)
    encoded_header = COMMON_HEADER.pack(
        MAGIC,
        MAJOR,
        header.minor,
        int(_message_type(packet.payload)),
        header.flags,
        header_length,
        len(payload),
        header.node_id,
        0,
        header.boot_id,
        header.sequence,
        header.device_time_us,
        0,
    )
    datagram = bytearray(encoded_header + header.extension + payload)
    if len(datagram) > MAX_DATAGRAM:
        raise ProtocolError("size", "datagram exceeds 1200 bytes")
    struct.pack_into("!I", datagram, 36, zlib.crc32(datagram))
    return bytes(datagram)


def _enum(enum_type: type[IntEnum], value: int, field: str):
    try:
        return enum_type(value)
    except ValueError as error:
        raise ProtocolError("payload", f"unknown {field} value") from error


def _decode_payload(message_type: MessageType, payload: bytes) -> Payload:
    try:
        if message_type is MessageType.HELLO:
            if len(payload) != HELLO_STRUCT.size:
                raise ProtocolError("payload", "HELLO must contain 24 bytes")
            values = HELLO_STRUCT.unpack(payload)
            return Hello(values[0], values[1], values[2], values[3], *values[5:])
        if message_type is MessageType.CSI_FRAME:
            if len(payload) <= CSI_PREFIX.size:
                raise ProtocolError("payload", "CSI data must not be empty")
            values = CSI_PREFIX.unpack_from(payload)
            iq = payload[CSI_PREFIX.size :]
            if values[8] != len(iq):
                raise ProtocolError("payload", "CSI length does not match payload")
            if values[16] not in (0, 1):
                raise ProtocolError("payload", "first_word_invalid must be boolean")
            return CsiFrame(
                values[0], values[1], values[2], values[3], values[4], values[5],
                values[6], values[7], values[9], values[10], values[11], values[12],
                values[13], values[14], values[15], bool(values[16]), iq,
            )
        if message_type is MessageType.SENSING_STATE:
            if len(payload) != SENSING_STRUCT.size:
                raise ProtocolError("payload", "SENSING_STATE must contain 56 bytes")
            values = SENSING_STRUCT.unpack(payload)
            config = SensingConfig(values[14], values[15], values[16], values[17])
            return SensingState(
                values[0],
                _enum(StableState, values[1], "stable state"),
                _enum(ProcessState, values[2], "process state"),
                _enum(InitStage, values[3], "init stage"),
                values[4], values[5], values[7], values[8], values[9], values[10],
                values[11], values[12], values[13], config,
            )
        if message_type is MessageType.HEARTBEAT:
            if len(payload) != HEARTBEAT_STRUCT.size:
                raise ProtocolError("payload", "HEARTBEAT must contain 32 bytes")
            return Heartbeat(*HEARTBEAT_STRUCT.unpack(payload))
        if message_type is MessageType.COMMAND:
            if len(payload) not in (COMMAND_PREFIX.size, COMMAND_PREFIX.size + CONFIG_BODY.size):
                raise ProtocolError("payload", "invalid COMMAND length")
            correlation_id, opcode_value = COMMAND_PREFIX.unpack_from(payload)
            opcode = _enum(CommandOpcode, opcode_value, "command opcode")
            body = payload[COMMAND_PREFIX.size :]
            if opcode is CommandOpcode.SET_CONFIG:
                return Command(correlation_id, opcode, _decode_config(body))
            if body:
                raise ProtocolError("payload", "command body is not allowed")
            return Command(correlation_id, opcode)
        if message_type is MessageType.COMMAND_ACK:
            if len(payload) not in (ACK_PREFIX.size, ACK_PREFIX.size + CONFIG_BODY.size):
                raise ProtocolError("payload", "invalid COMMAND_ACK length")
            correlation_id, opcode_value, status_value = ACK_PREFIX.unpack_from(payload)
            opcode = _enum(CommandOpcode, opcode_value, "command opcode")
            status = _enum(AckStatus, status_value, "ACK status")
            body = payload[ACK_PREFIX.size :]
            if body and opcode not in (CommandOpcode.GET_CONFIG, CommandOpcode.SET_CONFIG):
                raise ProtocolError("payload", "ACK config is not allowed")
            return CommandAck(
                correlation_id,
                opcode,
                status,
                _decode_config(body) if body else None,
            )
    except struct.error as error:
        raise ProtocolError("payload", "payload cannot be unpacked") from error
    raise ProtocolError("message_type", "unsupported message type")


def decode_packet(data: bytes) -> Packet:
    if len(data) > MAX_DATAGRAM or len(data) < COMMON_HEADER.size:
        raise ProtocolError("size", "datagram size is outside the valid range")
    values = COMMON_HEADER.unpack_from(data)
    magic, major, minor, type_value, flags = values[:5]
    header_length, payload_length = values[5:7]
    node_id, _, boot_id, sequence, device_time_us, received_crc = values[7:]

    if magic != MAGIC:
        raise ProtocolError("magic", "unexpected magic")
    if major != MAJOR:
        raise ProtocolError("version", "unsupported major version")
    if flags != 0:
        raise ProtocolError("flags", "version 1 flags must be zero")
    if header_length < COMMON_HEADER.size or (minor == 0 and header_length != COMMON_HEADER.size):
        raise ProtocolError("header_length", "header length is invalid for minor version")
    if header_length > len(data):
        raise ProtocolError("header_length", "header extends beyond datagram")
    if header_length + payload_length != len(data):
        raise ProtocolError("payload_length", "payload length does not match datagram")
    if boot_id == 0:
        raise ProtocolError("boot_id", "boot ID must be nonzero")

    crc_input = bytearray(data)
    struct.pack_into("!I", crc_input, 36, 0)
    if zlib.crc32(crc_input) != received_crc:
        raise ProtocolError("crc", "CRC-32 mismatch")
    try:
        message_type = MessageType(type_value)
    except ValueError as error:
        raise ProtocolError("message_type", "unknown message type") from error

    header = Header(
        node_id=node_id,
        boot_id=boot_id,
        sequence=sequence,
        device_time_us=device_time_us,
        minor=minor,
        flags=flags,
        extension=data[COMMON_HEADER.size:header_length],
    )
    return Packet(header, _decode_payload(message_type, data[header_length:]))


def sequence_relation(
    previous: int,
    current: int,
) -> Literal["next", "gap", "duplicate", "old"]:
    delta = (current - previous) & 0xFFFFFFFF
    if delta == 0:
        return "duplicate"
    if delta == 1:
        return "next"
    if delta < 0x80000000:
        return "gap"
    return "old"
