use std::collections::HashMap;

/// Local mempool state tracking for delta encoding
pub struct MempoolState {
    transactions: HashMap<[u8; 32], TxMeta>,
    total_bytes: u64,
}

#[derive(Clone)]
pub struct TxMeta {
    pub txid: [u8; 32],
    pub fee_rate: f64,  // sat/vB
    pub size: u32,      // bytes
    pub timestamp_ns: u64,
}

impl MempoolState {
    pub fn new() -> Self {
        Self {
            transactions: HashMap::new(),
            total_bytes: 0,
        }
    }
    
    /// Add transaction to mempool
    pub fn add(&mut self, txid: [u8; 32], fee_rate: f64, size: u32, timestamp_ns: u64) {
        let meta = TxMeta {
            txid,
            fee_rate,
            size,
            timestamp_ns,
        };
        
        self.total_bytes += size as u64;
        self.transactions.insert(txid, meta);
    }
    
    /// Remove transaction from mempool
    pub fn remove(&mut self, txid: &[u8; 32]) -> Option<TxMeta> {
        if let Some(meta) = self.transactions.remove(txid) {
            self.total_bytes -= meta.size as u64;
            Some(meta)
        } else {
            None
        }
    }
    
    /// Get transaction metadata
    pub fn get(&self, txid: &[u8; 32]) -> Option<&TxMeta> {
        self.transactions.get(txid)
    }
    
    /// Number of transactions in mempool
    pub fn tx_count(&self) -> usize {
        self.transactions.len()
    }
    
    /// Total mempool size in bytes
    pub fn total_bytes(&self) -> u64 {
        self.total_bytes
    }
    
    /// Compute blockspace stress score (0.0-1.0)
    /// Based on mempool size relative to typical block capacity
    pub fn stress_score(&self) -> f64 {
        // Assume typical block is ~1MB, mempool stress increases as we exceed multiple blocks
        let block_size = 1_000_000u64;
        let blocks_worth = self.total_bytes as f64 / block_size as f64;
        
        // Stress score: 0.0 at 0 blocks, 1.0 at 10+ blocks
        (blocks_worth / 10.0).min(1.0)
    }
    
    /// Clear all transactions (e.g., on restart)
    pub fn clear(&mut self) {
        self.transactions.clear();
        self.total_bytes = 0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_mempool_add_remove() {
        let mut mempool = MempoolState::new();
        
        let txid = [1u8; 32];
        mempool.add(txid, 10.0, 250, 1000);
        
        assert_eq!(mempool.tx_count(), 1);
        assert_eq!(mempool.total_bytes(), 250);
        
        let meta = mempool.get(&txid).unwrap();
        assert_eq!(meta.fee_rate, 10.0);
        assert_eq!(meta.size, 250);
        
        mempool.remove(&txid);
        
        assert_eq!(mempool.tx_count(), 0);
        assert_eq!(mempool.total_bytes(), 0);
    }
    
    #[test]
    fn test_stress_score() {
        let mut mempool = MempoolState::new();
        
        // Empty mempool = 0 stress
        assert_eq!(mempool.stress_score(), 0.0);
        
        // Add 5MB worth of transactions = 0.5 stress
        for i in 0..20 {
            let txid = [i as u8; 32];
            mempool.add(txid, 10.0, 250_000, 1000);
        }
        
        let stress = mempool.stress_score();
        assert!(stress > 0.4 && stress < 0.6);
        
        // Add 10MB worth = 1.0 stress (capped)
        for i in 20..60 {
            let txid = [i as u8; 32];
            mempool.add(txid, 10.0, 250_000, 1000);
        }
        
        assert_eq!(mempool.stress_score(), 1.0);
    }
}
