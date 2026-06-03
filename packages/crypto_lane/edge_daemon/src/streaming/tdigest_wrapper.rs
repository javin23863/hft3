use std::collections::VecDeque;

/// Streaming quantile estimation using a sorted buffer
/// 
/// Maintains a fixed-size sorted buffer for quantile estimation.
/// Memory footprint: ~800 bytes for 100 samples.
pub struct FeeQuantiles {
    buffer: VecDeque<f64>,
    max_size: usize,
}

pub struct FeeQuintiles {
    pub p20: f64,
    pub p40: f64,
    pub p60: f64,
    pub p80: f64,
}

impl FeeQuantiles {
    pub fn new() -> Self {
        Self {
            buffer: VecDeque::with_capacity(100),
            max_size: 100,
        }
    }
    
    /// Add a new fee rate observation
    pub fn add(&mut self, fee_rate: f64) {
        // Insert in sorted order
        let pos = self.buffer.partition_point(|&x| x < fee_rate);
        if pos < self.buffer.len() {
            self.buffer.insert(pos, fee_rate);
        } else {
            self.buffer.push_back(fee_rate);
        }
        
        // Maintain max size by removing oldest if needed
        if self.buffer.len() > self.max_size {
            self.buffer.pop_front();
        }
    }
    
    /// Get current quantile estimates
    pub fn quantiles(&self) -> FeeQuintiles {
        FeeQuintiles {
            p20: self.quantile(0.20),
            p40: self.quantile(0.40),
            p60: self.quantile(0.60),
            p80: self.quantile(0.80),
        }
    }
    
    /// Get arbitrary quantile
    pub fn quantile(&self, q: f64) -> f64 {
        if self.buffer.is_empty() {
            return 0.0;
        }
        
        let index = (q * (self.buffer.len() - 1) as f64).round() as usize;
        self.buffer[index]
    }
    
    /// Reset the digest
    pub fn reset(&mut self) {
        self.buffer.clear();
    }
    
    /// Number of observations
    pub fn count(&self) -> usize {
        self.buffer.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_tdigest_basic() {
        let mut quantiles = FeeQuantiles::new();
        
        // Add 100 values from 1 to 100
        for i in 1..=100 {
            quantiles.add(i as f64);
        }
        
        let q = quantiles.quantiles();
        
        // p20 should be around 20
        assert!((q.p20 - 20.0).abs() < 5.0);
        
        // p40 should be around 40
        assert!((q.p40 - 40.0).abs() < 5.0);
        
        // p60 should be around 60
        assert!((q.p60 - 60.0).abs() < 5.0);
        
        // p80 should be around 80
        assert!((q.p80 - 80.0).abs() < 5.0);
    }
    
    #[test]
    fn test_tdigest_empty() {
        let quantiles = FeeQuantiles::new();
        let q = quantiles.quantiles();
        
        // Empty digest should return 0 or NaN
        assert!(q.p20.is_nan() || q.p20 == 0.0);
    }
    
    #[test]
    fn test_tdigest_reset() {
        let mut quantiles = FeeQuantiles::new();
        
        quantiles.add(10.0);
        quantiles.add(20.0);
        quantiles.add(30.0);
        
        assert_eq!(quantiles.count(), 3);
        
        quantiles.reset();
        
        assert_eq!(quantiles.count(), 0);
    }
}
