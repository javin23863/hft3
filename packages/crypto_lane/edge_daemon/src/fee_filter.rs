use anyhow::Result;
use crate::config::Config;

/// Dynamic fee threshold pruning
/// 
/// Filters out low-fee transactions that won't be included in next N blocks
pub struct FeeFilter {
    threshold: f64,  // sat/vB
    enabled: bool,
    target_blocks: u32,
    filtered_count: u64,
    rpc_client: Option<reqwest::Client>,
    rpc_url: String,
    rpc_user: String,
    rpc_password: String,
}

impl FeeFilter {
    pub async fn new(config: &Config) -> Result<Self> {
        let client = if config.fee_filter_enabled {
            Some(reqwest::Client::new())
        } else {
            None
        };
        
        let mut filter = Self {
            threshold: 1.0,  // Default 1 sat/vB
            enabled: config.fee_filter_enabled,
            target_blocks: config.fee_filter_blocks,
            filtered_count: 0,
            rpc_client: client,
            rpc_url: config.rpc_url.clone(),
            rpc_user: config.rpc_user.clone(),
            rpc_password: config.rpc_password.clone(),
        };
        
        // Fetch initial threshold
        if filter.enabled {
            if let Err(e) = filter.update_threshold().await {
                log::warn!("Failed to fetch initial fee threshold: {}", e);
            }
        }
        
        Ok(filter)
    }
    
    /// Check if transaction should be processed (passes fee filter)
    pub fn should_process(&mut self, fee_rate: f64) -> bool {
        if !self.enabled {
            return true;
        }
        
        if fee_rate >= self.threshold {
            true
        } else {
            self.filtered_count += 1;
            false
        }
    }
    
    /// Update fee threshold from Bitcoin Core RPC
    pub async fn update_threshold(&mut self) -> Result<()> {
        if !self.enabled || self.rpc_client.is_none() {
            return Ok(());
        }
        
        let client = self.rpc_client.as_ref().unwrap();
        
        // Call estimatesmartfee RPC
        let response = client
            .post(&self.rpc_url)
            .basic_auth(&self.rpc_user, Some(&self.rpc_password))
            .json(&serde_json::json!({
                "jsonrpc": "1.0",
                "id": "edge-daemon",
                "method": "estimatesmartfee",
                "params": [self.target_blocks]
            }))
            .send()
            .await?;
        
        let result: serde_json::Value = response.json().await?;
        
        if let Some(fee_rate_btc_kvb) = result["result"]["feerate"].as_f64() {
            // Convert BTC/kvB to sat/vB
            // 1 BTC = 100,000,000 sat
            // 1 kvB = 1000 vB
            let fee_rate_sat_vb = fee_rate_btc_kvb * 100_000_000.0 / 1000.0;
            self.threshold = fee_rate_sat_vb.max(1.0);  // Minimum 1 sat/vB
            
            log::debug!("Updated fee threshold: {:.2} sat/vB", self.threshold);
        }
        
        Ok(())
    }
    
    pub fn threshold(&self) -> f64 {
        self.threshold
    }
    
    pub fn filtered_count(&self) -> u64 {
        self.filtered_count
    }
    
    pub fn reset_filtered_count(&mut self) {
        self.filtered_count = 0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_fee_filter_disabled() {
        let mut filter = FeeFilter {
            threshold: 10.0,
            enabled: false,
            target_blocks: 1,
            filtered_count: 0,
            rpc_client: None,
            rpc_url: String::new(),
            rpc_user: String::new(),
            rpc_password: String::new(),
        };
        
        // Should process all transactions when disabled
        assert!(filter.should_process(1.0));
        assert!(filter.should_process(5.0));
        assert!(filter.should_process(100.0));
        assert_eq!(filter.filtered_count(), 0);
    }
    
    #[test]
    fn test_fee_filter_enabled() {
        let mut filter = FeeFilter {
            threshold: 10.0,
            enabled: true,
            target_blocks: 1,
            filtered_count: 0,
            rpc_client: None,
            rpc_url: String::new(),
            rpc_user: String::new(),
            rpc_password: String::new(),
        };
        
        // Should filter low-fee transactions
        assert!(!filter.should_process(5.0));
        assert!(!filter.should_process(9.9));
        assert!(filter.should_process(10.0));
        assert!(filter.should_process(20.0));
        
        assert_eq!(filter.filtered_count(), 2);
    }
}
