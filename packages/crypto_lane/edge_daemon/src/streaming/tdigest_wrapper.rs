use tdigest::TDigest;

/// Streaming quantile estimation using t-digest algorithm
/// 
/// Provides accurate quantile estimates with minimal memory footprint (~2KB for 100 centroids)
pub struct FeeQuantiles {
    digest: TDigest,
    compression: f64,
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
            digest: TDigest::new_with_size(100),
            compression: 100.0,
        }
    }
    
    /// Add a new fee rate observation
    pub fn add(&mut self, fee_rate: f64) {
        self.digest = self.digest.insert(fee_rate);
    }
    
    /// Get current quantile estimates
    pub fn quantiles(&self) -> FeeQuintiles {
        FeeQuintiles {
            p20: self.digest.quantile(0.20),
            p40: self.digest.quantile(0.40),
            p60: self.digest.quantile(0.60),
            p80: self.digest.quantile(0.80),
        }
    }
    
    /// Get arbitrary quantile
    pub fn quantile(&self, q: f64) -> f64 {
        self.digest.quantile(q)
    }
    
    /// Reset the digest
    pub fn reset(&mut self) {
        self.digest = TDigest::new_with_size(self.compression as usize);
    }
    
    /// Number of observations
    pub fn count(&self) -> usize {
        self.digest.count() as usize
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
