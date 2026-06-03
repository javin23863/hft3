use prost::Message;
use crate::edge_features::EdgeFeaturePacket;

/// Serialize feature packet to bytes with length prefix
pub fn serialize_packet(packet: &EdgeFeaturePacket) -> Vec<u8> {
    let mut buf = Vec::with_capacity(packet.encoded_len() + 4);
    
    // Encode packet
    let mut packet_buf = Vec::with_capacity(packet.encoded_len());
    packet.encode(&mut packet_buf).unwrap();
    
    // Write length prefix (4 bytes, big-endian)
    let len = packet_buf.len() as u32;
    buf.extend_from_slice(&len.to_be_bytes());
    
    // Write packet data
    buf.extend_from_slice(&packet_buf);
    
    buf
}

/// Deserialize packet from bytes (without length prefix)
pub fn deserialize_packet(data: &[u8]) -> Result<EdgeFeaturePacket, prost::DecodeError> {
    EdgeFeaturePacket::decode(data)
}

#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_serialize_deserialize() {
        let packet = EdgeFeaturePacket {
            timestamp_ns: 1000,
            sequence_number: 42,
            fee_mean_sat_vb: 10.5,
            fee_stddev_sat_vb: 2.0,
            fee_zscore_latest: 1.5,
            fee_sample_count: 100,
            fee_p20: 5.0,
            fee_p40: 8.0,
            fee_p60: 12.0,
            fee_p80: 20.0,
            mempool_tx_count: 5000,
            mempool_bytes: 10_000_000,
            blockspace_stress_score: 0.5,
            deltas: vec![],
            min_fee_threshold: 5.0,
            filtered_tx_count: 100,
            delta_count: 0,
            uptime_seconds: 3600,
            packets_sent: 1,
            bytes_sent: 100,
        };
        
        let serialized = serialize_packet(&packet);
        
        // Skip length prefix (first 4 bytes)
        let deserialized = deserialize_packet(&serialized[4..]).unwrap();
        
        assert_eq!(deserialized.sequence_number, 42);
        assert_eq!(deserialized.fee_mean_sat_vb, 10.5);
        assert_eq!(deserialized.mempool_tx_count, 5000);
    }
}
