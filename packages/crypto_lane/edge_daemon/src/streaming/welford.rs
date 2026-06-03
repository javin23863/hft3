/// Welford's online algorithm for computing mean and variance in O(1) space
/// 
/// Reference: https://en.wikipedia.org/wiki/Algorithms_for_calculating_variance#Welford's_online_algorithm
pub struct WelfordState {
    n: u64,
    mean: f64,
    m2: f64,
    last_zscore: f64,
}

impl WelfordState {
    pub fn new() -> Self {
        Self {
            n: 0,
            mean: 0.0,
            m2: 0.0,
            last_zscore: 0.0,
        }
    }
    
    /// Update statistics with new fee rate value
    /// Returns the z-score of the new value
    pub fn update(&mut self, x: f64) -> f64 {
        self.n += 1;
        
        // Update mean
        let delta = x - self.mean;
        self.mean += delta / self.n as f64;
        
        // Update M2 (sum of squares of differences from the current mean)
        let delta2 = x - self.mean;
        self.m2 += delta * delta2;
        
        // Compute z-score
        let zscore = if self.n > 1 {
            let variance = self.m2 / (self.n - 1) as f64;
            let stddev = variance.sqrt();
            if stddev > 0.0 {
                (x - self.mean) / stddev
            } else {
                0.0
            }
        } else {
            0.0
        };
        
        self.last_zscore = zscore;
        zscore
    }
    
    pub fn mean(&self) -> f64 {
        self.mean
    }
    
    pub fn variance(&self) -> f64 {
        if self.n > 1 {
            self.m2 / (self.n - 1) as f64
        } else {
            0.0
        }
    }
    
    pub fn stddev(&self) -> f64 {
        self.variance().sqrt()
    }
    
    pub fn count(&self) -> u64 {
        self.n
    }
    
    pub fn last_zscore(&self) -> f64 {
        self.last_zscore
    }
    
    /// Reset statistics (e.g., after block inclusion)
    pub fn reset(&mut self) {
        self.n = 0;
        self.mean = 0.0;
        self.m2 = 0.0;
        self.last_zscore = 0.0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_welford_basic() {
        let mut welford = WelfordState::new();
        
        // Add values: 1.0, 2.0, 3.0, 4.0, 5.0
        welford.update(1.0);
        welford.update(2.0);
        welford.update(3.0);
        welford.update(4.0);
        welford.update(5.0);
        
        assert_eq!(welford.count(), 5);
        assert!((welford.mean() - 3.0).abs() < 1e-10);
        
        // Variance of [1,2,3,4,5] = 2.5
        assert!((welford.variance() - 2.5).abs() < 1e-10);
        
        // Stddev = sqrt(2.5) ≈ 1.581
        assert!((welford.stddev() - 1.5811388).abs() < 1e-6);
    }
    
    #[test]
    fn test_welford_zscore() {
        let mut welford = WelfordState::new();
        
        // Add values with mean=10, stddev=2
        for i in 0..100 {
            let value = 10.0 + (i as f64 - 50.0) * 0.2;
            welford.update(value);
        }
        
        // Add outlier at 20 (5 stddev above mean)
        let zscore = welford.update(20.0);
        
        // Z-score should be approximately 5
        assert!(zscore > 4.0 && zscore < 6.0);
    }
    
    #[test]
    fn test_welford_empty() {
        let welford = WelfordState::new();
        assert_eq!(welford.count(), 0);
        assert_eq!(welford.mean(), 0.0);
        assert_eq!(welford.variance(), 0.0);
        assert_eq!(welford.stddev(), 0.0);
    }
    
    #[test]
    fn test_welford_reset() {
        let mut welford = WelfordState::new();
        welford.update(1.0);
        welford.update(2.0);
        welford.update(3.0);
        
        assert_eq!(welford.count(), 3);
        
        welford.reset();
        
        assert_eq!(welford.count(), 0);
        assert_eq!(welford.mean(), 0.0);
    }
}
