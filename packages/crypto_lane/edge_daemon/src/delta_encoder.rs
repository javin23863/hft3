use std::sync::Mutex;
use crate::edge_features::{MempoolDelta, RemovalReason};

/// Encodes mempool state changes as deltas
pub struct DeltaEncoder {
    deltas: Mutex<Vec<MempoolDelta>>,
    max_deltas_per_packet: usize,
}

impl DeltaEncoder {
    pub fn new() -> Self {
        Self {
            deltas: Mutex::new(Vec::new()),
            max_deltas_per_packet: 100,
        }
    }
    
    /// Record a transaction addition
    pub fn add_tx(&self, txid: [u8; 32], fee_rate: f64, size: u32, timestamp_ns: u64) {
        let mut deltas = self.deltas.lock().unwrap();
        
        if deltas.len() < self.max_deltas_per_packet {
            deltas.push(MempoolDelta {
                r#type: crate::edge_features::mempool_delta::DeltaType::Add as i32,
                txid: txid.to_vec(),
                fee_rate,
                size_bytes: size,
                timestamp_ns,
                removal_reason: 0,
                old_txid: Vec::new(),
            });
        }
    }
    
    /// Record a transaction removal
    pub fn remove_tx(&self, txid: [u8; 32], reason: RemovalReason) {
        let mut deltas = self.deltas.lock().unwrap();
        
        if deltas.len() < self.max_deltas_per_packet {
            deltas.push(MempoolDelta {
                r#type: crate::edge_features::mempool_delta::DeltaType::Remove as i32,
                txid: txid.to_vec(),
                fee_rate: 0.0,
                size_bytes: 0,
                timestamp_ns: 0,
                removal_reason: reason as i32,
                old_txid: Vec::new(),
            });
        }
    }
    
    /// Record a transaction replacement (RBF)
    pub fn replace_tx(&self, old_txid: [u8; 32], new_txid: [u8; 32], new_fee_rate: f64) {
        let mut deltas = self.deltas.lock().unwrap();
        
        if deltas.len() < self.max_deltas_per_packet {
            deltas.push(MempoolDelta {
                r#type: crate::edge_features::mempool_delta::DeltaType::Replace as i32,
                txid: new_txid.to_vec(),
                fee_rate: new_fee_rate,
                size_bytes: 0,
                timestamp_ns: 0,
                removal_reason: 0,
                old_txid: old_txid.to_vec(),
            });
        }
    }
    
    /// Drain accumulated deltas for packet inclusion
    pub fn drain_deltas(&self) -> Vec<MempoolDelta> {
        let mut deltas = self.deltas.lock().unwrap();
        std::mem::take(&mut *deltas)
    }
    
    /// Number of pending deltas
    pub fn pending_count(&self) -> usize {
        self.deltas.lock().unwrap().len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_delta_encoder_add() {
        let encoder = DeltaEncoder::new();
        
        let txid = [1u8; 32];
        encoder.add_tx(txid, 10.0, 250, 1000);
        
        assert_eq!(encoder.pending_count(), 1);
        
        let deltas = encoder.drain_deltas();
        assert_eq!(deltas.len(), 1);
        assert_eq!(deltas[0].r#type, crate::edge_features::mempool_delta::DeltaType::Add as i32);
        assert_eq!(deltas[0].fee_rate, 10.0);
        
        assert_eq!(encoder.pending_count(), 0);
    }
    
    #[test]
    fn test_delta_encoder_remove() {
        let encoder = DeltaEncoder::new();
        
        let txid = [2u8; 32];
        encoder.remove_tx(txid, RemovalReason::BlockInclusion);
        
        let deltas = encoder.drain_deltas();
        assert_eq!(deltas.len(), 1);
        assert_eq!(deltas[0].r#type, crate::edge_features::mempool_delta::DeltaType::Remove as i32);
        assert_eq!(deltas[0].removal_reason, RemovalReason::BlockInclusion as i32);
    }
    
    #[test]
    fn test_delta_encoder_replace() {
        let encoder = DeltaEncoder::new();
        
        let old_txid = [3u8; 32];
        let new_txid = [4u8; 32];
        encoder.replace_tx(old_txid, new_txid, 20.0);
        
        let deltas = encoder.drain_deltas();
        assert_eq!(deltas.len(), 1);
        assert_eq!(deltas[0].r#type, crate::edge_features::mempool_delta::DeltaType::Replace as i32);
        assert_eq!(deltas[0].fee_rate, 20.0);
        assert_eq!(deltas[0].old_txid, old_txid.to_vec());
    }
    
    #[test]
    fn test_delta_encoder_max_limit() {
        let encoder = DeltaEncoder::new();
        
        // Add 150 deltas (exceeds max of 100)
        for i in 0..150 {
            let txid = [i as u8; 32];
            encoder.add_tx(txid, 10.0, 250, 1000);
        }
        
        // Should only keep first 100
        assert_eq!(encoder.pending_count(), 100);
    }
}
