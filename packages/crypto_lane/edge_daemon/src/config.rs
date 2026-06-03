use anyhow::Result;
use serde::Deserialize;
use std::path::PathBuf;

#[derive(Debug, Deserialize, Clone)]
pub struct Config {
    // Bitcoin Core connection
    pub zmq_rawtx: String,
    pub zmq_rawblock: String,
    pub rpc_url: String,
    pub rpc_user: String,
    pub rpc_password: String,
    
    // Chicago connection
    pub chicago_addr: String,
    
    // Processing parameters
    pub packet_interval: u64,  // Send packet every N transactions
    pub fee_filter_enabled: bool,
    pub fee_filter_blocks: u32,  // Target next N blocks for fee threshold
    
    // Metrics
    pub metrics_port: u16,
}

impl Config {
    pub fn load() -> Result<Self> {
        // Try loading from environment variables first
        if let Ok(config) = Self::from_env() {
            return Ok(config);
        }
        
        // Fall back to config file
        let config_path = std::env::var("BTC_EDGE_CONFIG")
            .unwrap_or_else(|_| "/etc/btc-edge-daemon/config.toml".to_string());
        
        let config = config::Config::builder()
            .add_source(config::File::with_name(&config_path))
            .add_source(config::Environment::with_prefix("BTC_EDGE"))
            .build()?;
        
        Ok(config.try_deserialize()?)
    }
    
    fn from_env() -> Result<Self> {
        Ok(Self {
            zmq_rawtx: std::env::var("BTC_ZMQ_RAWTX")?,
            zmq_rawblock: std::env::var("BTC_ZMQ_RAWBLOCK")?,
            rpc_url: std::env::var("BTC_RPC_URL")?,
            rpc_user: std::env::var("BTC_RPC_USER")?,
            rpc_password: std::env::var("BTC_RPC_PASS")?,
            chicago_addr: std::env::var("CHICAGO_ADDR")
                .unwrap_or_else(|_| "127.0.0.1:9876".to_string()),
            packet_interval: std::env::var("PACKET_INTERVAL")
                .unwrap_or_else(|_| "100".to_string())
                .parse()?,
            fee_filter_enabled: std::env::var("FEE_FILTER_ENABLED")
                .unwrap_or_else(|_| "true".to_string())
                .parse()?,
            fee_filter_blocks: std::env::var("FEE_FILTER_BLOCKS")
                .unwrap_or_else(|_| "1".to_string())
                .parse()?,
            metrics_port: std::env::var("METRICS_PORT")
                .unwrap_or_else(|_| "9090".to_string())
                .parse()?,
        })
    }
}
