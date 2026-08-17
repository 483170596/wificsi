import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from wificsi.protocol import (
    AckStatus,
    Command,
    CommandAck,
    CommandOpcode,
    CsiFrame,
    Header,
    Heartbeat,
    Hello,
    InitStage,
    MessageType,
    Packet,
    ProcessState,
    ProtocolError,
    SensingConfig,
    SensingState,
    StableState,
    decode_packet,
    encode_packet,
    sequence_relation,
)


NODE_ID = bytes.fromhex("288485872bf4")
PEER_ID = bytes.fromhex("5ee388d95b42")


def header(*, sequence: int = 0xFFFFFFFE, minor: int = 0, extension: bytes = b""):
    return Header(
        node_id=NODE_ID,
        boot_id=0x10203040,
        sequence=sequence,
        device_time_us=0x0102030405060708,
        minor=minor,
        extension=extension,
    )


CONFIG = SensingConfig(0.5, 0.25, 0.125, 300)


@pytest.mark.parametrize(
    "payload",
    [
        Hello(1, 1, 0, 0, 0x000F, 612, 512, 1000, 1000, 5501, PEER_ID),
        CsiFrame(
            PEER_ID,
            NODE_ID,
            -80,
            -95,
            4,
            0,
            0x11223344,
            128,
            11,
            1,
            3,
            0,
            0x0021,
            0,
            0,
            False,
            bytes(range(8)),
        ),
        SensingState(
            PEER_ID,
            StableState.ACTIVE,
            ProcessState.ACTIVE,
            InitStage.STABLE,
            0x07,
            1,
            0.5,
            0.25,
            10,
            20,
            5,
            0.125,
            0.25,
            CONFIG,
        ),
        Heartbeat(1_000_000, 300_000, 250_000, 50, 49, 1, 0),
        Command(7, CommandOpcode.GET_CONFIG),
        Command(8, CommandOpcode.SET_CONFIG, CONFIG),
        CommandAck(7, CommandOpcode.GET_CONFIG, AckStatus.OK, CONFIG),
    ],
)
def test_every_message_round_trips(payload):
    packet = Packet(header(), payload)

    assert decode_packet(encode_packet(packet)) == packet


def test_hello_has_exact_wire_size():
    packet = Packet(
        header(),
        Hello(1, 1, 0, 0, 0x000F, 612, 512, 1000, 1000, 5501, PEER_ID),
    )

    assert len(encode_packet(packet)) == 64


def test_csi_preserves_variable_iq_length():
    for iq in (bytes(range(8)), bytes(range(128)), bytes(range(256))):
        payload = CsiFrame(
            PEER_ID, NODE_ID, -80, -95, 4, 0, 1, 128, 11, 1, 3, 0, 0, 0, 0,
            True, iq,
        )

        decoded = decode_packet(encode_packet(Packet(header(), payload)))

        assert decoded.payload.iq == iq
        assert decoded.payload.first_word_invalid is True


def test_crc_uses_ieee_reference_value():
    assert zlib.crc32(b"123456789") == 0xCBF43926


def test_higher_minor_skips_header_extension():
    packet = Packet(
        header(minor=1, extension=b"NEXT"),
        Heartbeat(1, 2, 3, 4, 5, 6, 7),
    )

    encoded = encode_packet(packet)

    assert struct.unpack_from("!H", encoded, 8)[0] == 44
    assert decode_packet(encoded) == packet


@pytest.mark.parametrize(
    ("offset", "replacement", "code"),
    [
        (0, b"FAIL", "magic"),
        (4, b"\x02", "version"),
        (7, b"\x01", "flags"),
        (8, b"\x00\x27", "header_length"),
        (20, b"\x00\x00\x00\x00", "boot_id"),
    ],
)
def test_invalid_header_fields_are_rejected(offset, replacement, code):
    encoded = bytearray(encode_packet(Packet(header(), Heartbeat(1, 2, 3, 4, 5, 6, 7))))
    encoded[offset : offset + len(replacement)] = replacement

    with pytest.raises(ProtocolError) as raised:
        decode_packet(bytes(encoded))

    assert raised.value.code == code


def test_bad_crc_is_rejected():
    encoded = bytearray(encode_packet(Packet(header(), Heartbeat(1, 2, 3, 4, 5, 6, 7))))
    encoded[-1] ^= 0x01

    with pytest.raises(ProtocolError) as raised:
        decode_packet(bytes(encoded))

    assert raised.value.code == "crc"


def test_sequence_relation_handles_wrap_gap_duplicate_and_old():
    assert sequence_relation(0xFFFFFFFF, 0) == "next"
    assert sequence_relation(10, 13) == "gap"
    assert sequence_relation(10, 10) == "duplicate"
    assert sequence_relation(10, 9) == "old"


def test_oversize_datagram_is_rejected_before_payload_decode():
    with pytest.raises(ProtocolError) as raised:
        decode_packet(b"x" * 1201)

    assert raised.value.code == "size"


def test_committed_vectors_decode_and_reencode_identically():
    vector_path = Path("protocol/vectors/wcsi_v1.json")
    vectors = json.loads(vector_path.read_text(encoding="utf-8"))

    assert {vector["name"] for vector in vectors} == {
        "hello",
        "csi_8",
        "sensing_active",
        "heartbeat",
        "get_config",
        "set_config",
        "get_config_ack",
    }
    for vector in vectors:
        encoded = bytes.fromhex(vector["encoded_hex"])
        assert encode_packet(decode_packet(encoded)) == encoded


def test_c_vector_generator_is_deterministic(tmp_path: Path):
    first = tmp_path / "first.h"
    second = tmp_path / "second.h"
    command = [
        sys.executable,
        "tools/generate_wcsi_c_vectors.py",
        "--input",
        "protocol/vectors/wcsi_v1.json",
    ]

    first_run = subprocess.run(
        [*command, "--output", str(first)], capture_output=True, text=True
    )
    second_run = subprocess.run(
        [*command, "--output", str(second)], capture_output=True, text=True
    )

    assert first_run.returncode == 0, first_run.stderr
    assert second_run.returncode == 0, second_run.stderr
    assert first.read_bytes() == second.read_bytes()
    assert b"WCSI_VECTOR_HELLO" in first.read_bytes()
