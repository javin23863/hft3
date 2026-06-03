"""
Chicago-side receiver for Bitcoin edge daemon packets.

Receives Protocol Buffer packets from the edge daemon via TCP,
deserializes them, and integrates with the crypto_lane feature matrix.
"""
import socket
import struct
import logging
from dataclasses import dataclass
from typing import Optional, Generator
from pathlib import Path

logger = logging.getLogger(__name__)

_EDGE_FEATURE_PACKET_MESSAGE_CLASS = None


def _build_edge_feature_packet_message_class():
    """Build the EdgeFeaturePacket protobuf class without generated code."""
    global _EDGE_FEATURE_PACKET_MESSAGE_CLASS
    if _EDGE_FEATURE_PACKET_MESSAGE_CLASS is not None:
        return _EDGE_FEATURE_PACKET_MESSAGE_CLASS

    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    field_types = descriptor_pb2.FieldDescriptorProto.Type
    labels = descriptor_pb2.FieldDescriptorProto.Label

    file_proto = descriptor_pb2.FileDescriptorProto(
        name="edge_features.proto",
        package="edge_features",
        syntax="proto3",
    )

    removal_reason = file_proto.enum_type.add(name="RemovalReason")
    for name, number in (
        ("BLOCK_INCLUSION", 0),
        ("RBF", 1),
        ("EXPIRY", 2),
        ("CONFLICT", 3),
    ):
        removal_reason.value.add(name=name, number=number)

    delta = file_proto.message_type.add(name="MempoolDelta")
    delta_type = delta.enum_type.add(name="DeltaType")
    for name, number in (("ADD", 0), ("REMOVE", 1), ("REPLACE", 2)):
        delta_type.value.add(name=name, number=number)

    for name, number, field_type, type_name in (
        ("type", 1, field_types.Value("TYPE_ENUM"), ".edge_features.MempoolDelta.DeltaType"),
        ("txid", 2, field_types.Value("TYPE_BYTES"), None),
        ("fee_rate", 3, field_types.Value("TYPE_DOUBLE"), None),
        ("size_bytes", 4, field_types.Value("TYPE_UINT32"), None),
        ("timestamp_ns", 5, field_types.Value("TYPE_UINT64"), None),
        ("removal_reason", 6, field_types.Value("TYPE_ENUM"), ".edge_features.RemovalReason"),
        ("old_txid", 7, field_types.Value("TYPE_BYTES"), None),
    ):
        field = delta.field.add(
            name=name,
            number=number,
            label=labels.Value("LABEL_OPTIONAL"),
            type=field_type,
        )
        if type_name:
            field.type_name = type_name

    packet = file_proto.message_type.add(name="EdgeFeaturePacket")
    for name, number, field_type, label, type_name in (
        ("timestamp_ns", 1, field_types.Value("TYPE_UINT64"), labels.Value("LABEL_OPTIONAL"), None),
        ("sequence_number", 2, field_types.Value("TYPE_UINT64"), labels.Value("LABEL_OPTIONAL"), None),
        ("fee_mean_sat_vb", 3, field_types.Value("TYPE_DOUBLE"), labels.Value("LABEL_OPTIONAL"), None),
        ("fee_stddev_sat_vb", 4, field_types.Value("TYPE_DOUBLE"), labels.Value("LABEL_OPTIONAL"), None),
        ("fee_zscore_latest", 5, field_types.Value("TYPE_DOUBLE"), labels.Value("LABEL_OPTIONAL"), None),
        ("fee_sample_count", 6, field_types.Value("TYPE_UINT64"), labels.Value("LABEL_OPTIONAL"), None),
        ("fee_p20", 7, field_types.Value("TYPE_DOUBLE"), labels.Value("LABEL_OPTIONAL"), None),
        ("fee_p40", 8, field_types.Value("TYPE_DOUBLE"), labels.Value("LABEL_OPTIONAL"), None),
        ("fee_p60", 9, field_types.Value("TYPE_DOUBLE"), labels.Value("LABEL_OPTIONAL"), None),
        ("fee_p80", 10, field_types.Value("TYPE_DOUBLE"), labels.Value("LABEL_OPTIONAL"), None),
        ("mempool_tx_count", 11, field_types.Value("TYPE_UINT32"), labels.Value("LABEL_OPTIONAL"), None),
        ("mempool_bytes", 12, field_types.Value("TYPE_UINT64"), labels.Value("LABEL_OPTIONAL"), None),
        ("blockspace_stress_score", 13, field_types.Value("TYPE_DOUBLE"), labels.Value("LABEL_OPTIONAL"), None),
        ("deltas", 14, field_types.Value("TYPE_MESSAGE"), labels.Value("LABEL_REPEATED"), ".edge_features.MempoolDelta"),
        ("min_fee_threshold", 15, field_types.Value("TYPE_DOUBLE"), labels.Value("LABEL_OPTIONAL"), None),
        ("filtered_tx_count", 16, field_types.Value("TYPE_UINT32"), labels.Value("LABEL_OPTIONAL"), None),
        ("delta_count", 17, field_types.Value("TYPE_UINT32"), labels.Value("LABEL_OPTIONAL"), None),
        ("uptime_seconds", 18, field_types.Value("TYPE_UINT64"), labels.Value("LABEL_OPTIONAL"), None),
        ("packets_sent", 19, field_types.Value("TYPE_UINT64"), labels.Value("LABEL_OPTIONAL"), None),
        ("bytes_sent", 20, field_types.Value("TYPE_UINT64"), labels.Value("LABEL_OPTIONAL"), None),
    ):
        field = packet.field.add(
            name=name,
            number=number,
            label=label,
            type=field_type,
        )
        if type_name:
            field.type_name = type_name

    pool = descriptor_pool.DescriptorPool()
    pool.Add(file_proto)
    descriptor = pool.FindMessageTypeByName("edge_features.EdgeFeaturePacket")
    _EDGE_FEATURE_PACKET_MESSAGE_CLASS = message_factory.GetMessageClass(descriptor)
    return _EDGE_FEATURE_PACKET_MESSAGE_CLASS


@dataclass
class EdgeFeaturePacket:
    """Deserialized edge feature packet from Bitcoin node."""
    timestamp_ns: int
    sequence_number: int
    fee_mean_sat_vb: float
    fee_stddev_sat_vb: float
    fee_zscore_latest: float
    fee_sample_count: int
    fee_p20: float
    fee_p40: float
    fee_p60: float
    fee_p80: float
    mempool_tx_count: int
    mempool_bytes: int
    blockspace_stress_score: float
    min_fee_threshold: float
    filtered_tx_count: int
    delta_count: int
    uptime_seconds: int
    packets_sent: int
    bytes_sent: int
    
    # Delta updates (optional, parsed separately)
    deltas: list = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for DataFrame integration."""
        return {
            'timestamp_ns': self.timestamp_ns,
            'sequence_number': self.sequence_number,
            'fee_mean_sat_vb': self.fee_mean_sat_vb,
            'fee_stddev_sat_vb': self.fee_stddev_sat_vb,
            'fee_zscore_latest': self.fee_zscore_latest,
            'fee_sample_count': self.fee_sample_count,
            'fee_p20': self.fee_p20,
            'fee_p40': self.fee_p40,
            'fee_p60': self.fee_p60,
            'fee_p80': self.fee_p80,
            'mempool_tx_count': self.mempool_tx_count,
            'mempool_bytes': self.mempool_bytes,
            'blockspace_stress_score': self.blockspace_stress_score,
            'min_fee_threshold': self.min_fee_threshold,
            'filtered_tx_count': self.filtered_tx_count,
            'delta_count': self.delta_count,
            'uptime_seconds': self.uptime_seconds,
            'packets_sent': self.packets_sent,
            'bytes_sent': self.bytes_sent,
        }


class EdgeReceiver:
    """
    TCP receiver for edge daemon packets.
    
    Listens on a local port for incoming packets from the Bitcoin edge daemon
    (typically via SSH tunnel), deserializes Protocol Buffer messages, and
    provides a generator interface for packet consumption.
    """
    
    def __init__(self, host: str = '127.0.0.1', port: int = 9876):
        self.host = host
        self.port = port
        self.server_socket: Optional[socket.socket] = None
        self.client_socket: Optional[socket.socket] = None
        self.last_sequence = -1
        
    def start(self):
        """Start listening for connections."""
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(1)
        
        logger.info(f"Edge receiver listening on {self.host}:{self.port}")
        logger.info("Waiting for edge daemon connection...")
        
        self.client_socket, addr = self.server_socket.accept()
        logger.info(f"Edge daemon connected from {addr}")
        
    def stop(self):
        """Stop the receiver and close connections."""
        if self.client_socket:
            self.client_socket.close()
        if self.server_socket:
            self.server_socket.close()
        logger.info("Edge receiver stopped")
        
    def receive_packets(self) -> Generator[EdgeFeaturePacket, None, None]:
        """
        Generator that yields deserialized packets as they arrive.
        
        Packet format:
        - 4 bytes: length (big-endian uint32)
        - N bytes: Protocol Buffer encoded EdgeFeaturePacket
        """
        if not self.client_socket:
            raise RuntimeError("Receiver not started. Call start() first.")
        
        while True:
            try:
                # Read length prefix (4 bytes)
                length_data = self._recv_exact(4)
                if not length_data:
                    logger.warning("Connection closed by edge daemon")
                    break
                
                length = struct.unpack('>I', length_data)[0]
                
                # Read packet data
                packet_data = self._recv_exact(length)
                if not packet_data:
                    logger.warning("Connection closed during packet read")
                    break
                
                # Deserialize packet
                packet = self._deserialize_packet(packet_data)
                if packet:
                    # Check for sequence gaps
                    if packet.sequence_number != self.last_sequence + 1:
                        if self.last_sequence >= 0:
                            gap = packet.sequence_number - self.last_sequence - 1
                            logger.warning(f"Sequence gap detected: {gap} packets lost")
                    self.last_sequence = packet.sequence_number
                    
                    yield packet
                    
            except Exception as e:
                logger.error(f"Error receiving packet: {e}")
                break
    
    def _recv_exact(self, n: int) -> Optional[bytes]:
        """Receive exactly n bytes from socket."""
        data = b''
        while len(data) < n:
            try:
                chunk = self.client_socket.recv(n - len(data))
                if not chunk:
                    return None
                data += chunk
            except socket.error as e:
                logger.error(f"Socket error: {e}")
                return None
        return data
    
    def _deserialize_packet(self, data: bytes) -> Optional[EdgeFeaturePacket]:
        """Deserialize Protocol Buffer packet."""
        try:
            pb_packet = _build_edge_feature_packet_message_class()()
            pb_packet.ParseFromString(data)

            return EdgeFeaturePacket(
                timestamp_ns=pb_packet.timestamp_ns,
                sequence_number=pb_packet.sequence_number,
                fee_mean_sat_vb=pb_packet.fee_mean_sat_vb,
                fee_stddev_sat_vb=pb_packet.fee_stddev_sat_vb,
                fee_zscore_latest=pb_packet.fee_zscore_latest,
                fee_sample_count=pb_packet.fee_sample_count,
                fee_p20=pb_packet.fee_p20,
                fee_p40=pb_packet.fee_p40,
                fee_p60=pb_packet.fee_p60,
                fee_p80=pb_packet.fee_p80,
                mempool_tx_count=pb_packet.mempool_tx_count,
                mempool_bytes=pb_packet.mempool_bytes,
                blockspace_stress_score=pb_packet.blockspace_stress_score,
                min_fee_threshold=pb_packet.min_fee_threshold,
                filtered_tx_count=pb_packet.filtered_tx_count,
                delta_count=pb_packet.delta_count,
                uptime_seconds=pb_packet.uptime_seconds,
                packets_sent=pb_packet.packets_sent,
                bytes_sent=pb_packet.bytes_sent,
                deltas=[
                    {
                        "type": delta.type,
                        "txid": delta.txid.hex(),
                        "fee_rate": delta.fee_rate,
                        "size_bytes": delta.size_bytes,
                        "timestamp_ns": delta.timestamp_ns,
                        "removal_reason": delta.removal_reason,
                        "old_txid": delta.old_txid.hex(),
                    }
                    for delta in pb_packet.deltas
                ],
            )

        except Exception as e:
            logger.error(f"Failed to deserialize packet: {e}")
            return None


def run_receiver(host: str = '127.0.0.1', port: int = 9876):
    """
    Run the edge receiver as a standalone service.
    
    Prints received packets to stdout for debugging.
    """
    receiver = EdgeReceiver(host, port)
    
    try:
        receiver.start()
        
        print("Receiving packets from edge daemon...")
        print("-" * 80)
        
        for packet in receiver.receive_packets():
            print(f"[{packet.sequence_number}] "
                  f"fee_mean={packet.fee_mean_sat_vb:.2f} sat/vB, "
                  f"zscore={packet.fee_zscore_latest:.2f}, "
                  f"mempool={packet.mempool_tx_count} txs, "
                  f"stress={packet.blockspace_stress_score:.2f}")
            
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        receiver.stop()


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    run_receiver()
