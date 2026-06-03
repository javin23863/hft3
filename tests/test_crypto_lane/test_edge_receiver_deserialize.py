"""Tests for edge receiver protobuf deserialization."""

import struct

from crypto_lane.src.ingest.edge_receiver import EdgeReceiver


def _varint(value: int) -> bytes:
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _tag(field_number: int, wire_type: int) -> bytes:
    return _varint((field_number << 3) | wire_type)


def _uint(field_number: int, value: int) -> bytes:
    return _tag(field_number, 0) + _varint(value)


def _double(field_number: int, value: float) -> bytes:
    return _tag(field_number, 1) + struct.pack("<d", value)


def _bytes(field_number: int, value: bytes) -> bytes:
    return _tag(field_number, 2) + _varint(len(value)) + value


def test_deserialize_protobuf_packet_with_scalar_fields_and_delta():
    delta_payload = b"".join(
        [
            _uint(1, 2),
            _bytes(2, bytes.fromhex("11" * 32)),
            _double(3, 22.5),
            _uint(4, 225),
            _uint(5, 123456790),
            _uint(6, 1),
            _bytes(7, bytes.fromhex("22" * 32)),
        ]
    )
    packet_payload = b"".join(
        [
            _uint(1, 123456789),
            _uint(2, 42),
            _double(3, 18.5),
            _double(4, 2.25),
            _double(5, -0.75),
            _uint(6, 1000),
            _double(7, 10.0),
            _double(8, 15.0),
            _double(9, 20.0),
            _double(10, 30.0),
            _uint(11, 250000),
            _uint(12, 75000000),
            _double(13, 0.83),
            _bytes(14, delta_payload),
            _double(15, 7.5),
            _uint(16, 12),
            _uint(17, 1),
            _uint(18, 3600),
            _uint(19, 88),
            _uint(20, 4096),
        ]
    )

    decoded = EdgeReceiver()._deserialize_packet(packet_payload)

    assert decoded is not None
    assert decoded.timestamp_ns == 123456789
    assert decoded.sequence_number == 42
    assert decoded.fee_mean_sat_vb == 18.5
    assert decoded.fee_stddev_sat_vb == 2.25
    assert decoded.fee_zscore_latest == -0.75
    assert decoded.fee_sample_count == 1000
    assert decoded.fee_p20 == 10.0
    assert decoded.fee_p40 == 15.0
    assert decoded.fee_p60 == 20.0
    assert decoded.fee_p80 == 30.0
    assert decoded.mempool_tx_count == 250000
    assert decoded.mempool_bytes == 75000000
    assert decoded.blockspace_stress_score == 0.83
    assert decoded.min_fee_threshold == 7.5
    assert decoded.filtered_tx_count == 12
    assert decoded.delta_count == 1
    assert decoded.uptime_seconds == 3600
    assert decoded.packets_sent == 88
    assert decoded.bytes_sent == 4096
    assert decoded.deltas == [
        {
            "type": 2,
            "txid": "11" * 32,
            "fee_rate": 22.5,
            "size_bytes": 225,
            "timestamp_ns": 123456790,
            "removal_reason": 1,
            "old_txid": "22" * 32,
        }
    ]
