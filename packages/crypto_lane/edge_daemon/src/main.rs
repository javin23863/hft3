use anyhow::Result;
use log::{info, error};
use std::sync::Arc;
use tokio::sync::RwLock;

mod config;
mod zmq_subscriber;
mod streaming;
mod mempool_state;
mod fee_filter;
mod delta_encoder;
mod serializer;
mod tcp_sender;
mod metrics;
mod edge_features;

use config::Config;
use zmq_subscriber::ZmqSubscriber;
use streaming::{WelfordState, FeeQuantiles};
use mempool_state::MempoolState;
use fee_filter::FeeFilter;
use delta_encoder::DeltaEncoder;
use tcp_sender::TcpSender;
use metrics::Metrics;

#[tokio::main]
async fn main() -> Result<()> {
    // Initialize logging
    env_logger::init();
    info!("Bitcoin Edge Daemon starting...");
    
    // Load configuration
    let config = Config::load()?;
    info!("Configuration loaded: {:?}", config);
    
    // Initialize components
    let mempool_state = Arc::new(RwLock::new(MempoolState::new()));
    let welford = Arc::new(RwLock::new(WelfordState::new()));
    let quantiles = Arc::new(RwLock::new(FeeQuantiles::new()));
    let fee_filter = Arc::new(RwLock::new(FeeFilter::new(&config).await?));
    let delta_encoder = Arc::new(DeltaEncoder::new());
    let metrics = Arc::new(Metrics::new());
    
    // Connect to Chicago
    let mut tcp_sender = TcpSender::connect(&config.chicago_addr).await?;
    info!("Connected to Chicago at {}", config.chicago_addr);
    
    // Subscribe to Bitcoin Core ZMQ
    let mut zmq_sub = ZmqSubscriber::connect(
        &config.zmq_rawtx,
        &config.zmq_rawblock,
    )?;
    info!("Subscribed to ZMQ: rawtx={}, rawblock={}", 
          config.zmq_rawtx, config.zmq_rawblock);
    
    // Main event loop
    let mut sequence_number = 0u64;
    
    loop {
        match zmq_sub.recv_multipart() {
            Ok(parts) => {
                if parts.len() < 2 {
                    continue;
                }
                
                let topic = &parts[0];
                let message = &parts[1];
                
                match topic.as_slice() {
                    b"rawtx" => {
                        // Process transaction
                        if let Err(e) = process_transaction(
                            message,
                            &mempool_state,
                            &welford,
                            &quantiles,
                            &fee_filter,
                            &delta_encoder,
                            &metrics,
                        ).await {
                            error!("Error processing transaction: {}", e);
                        }
                    }
                    b"hashblock" => {
                        // Process block
                        if let Err(e) = process_block(
                            message,
                            &mempool_state,
                            &fee_filter,
                            &metrics,
                        ).await {
                            error!("Error processing block: {}", e);
                        }
                    }
                    _ => {}
                }
                
                // Send periodic feature packet
                if sequence_number % config.packet_interval == 0 {
                    let packet = build_feature_packet(
                        sequence_number,
                        &mempool_state,
                        &welford,
                        &quantiles,
                        &fee_filter,
                        &delta_encoder,
                        &metrics,
                    ).await;
                    
                    if let Err(e) = tcp_sender.send_packet(&packet).await {
                        error!("Error sending packet: {}", e);
                        // Attempt reconnection
                        match TcpSender::connect(&config.chicago_addr).await {
                            Ok(sender) => tcp_sender = sender,
                            Err(e) => error!("Reconnection failed: {}", e),
                        }
                    } else {
                        metrics.packets_sent.inc();
                        sequence_number += 1;
                    }
                }
            }
            Err(e) => {
                error!("ZMQ receive error: {}", e);
                tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
            }
        }
    }
}

async fn process_transaction(
    raw_tx: &[u8],
    mempool_state: &Arc<RwLock<MempoolState>>,
    welford: &Arc<RwLock<WelfordState>>,
    quantiles: &Arc<RwLock<FeeQuantiles>>,
    fee_filter: &Arc<RwLock<FeeFilter>>,
    delta_encoder: &Arc<DeltaEncoder>,
    metrics: &Arc<Metrics>,
) -> Result<()> {
    // TODO: Parse raw transaction
    // TODO: Extract fee rate
    // TODO: Apply fee filter
    // TODO: Update streaming statistics
    // TODO: Encode delta
    
    metrics.transactions_processed.inc();
    Ok(())
}

async fn process_block(
    block_hash: &[u8],
    mempool_state: &Arc<RwLock<MempoolState>>,
    fee_filter: &Arc<RwLock<FeeFilter>>,
    metrics: &Arc<Metrics>,
) -> Result<()> {
    // TODO: Fetch block details via RPC
    // TODO: Remove included transactions from mempool
    // TODO: Update fee filter threshold
    
    Ok(())
}

async fn build_feature_packet(
    sequence_number: u64,
    mempool_state: &Arc<RwLock<MempoolState>>,
    welford: &Arc<RwLock<WelfordState>>,
    quantiles: &Arc<RwLock<FeeQuantiles>>,
    fee_filter: &Arc<RwLock<FeeFilter>>,
    delta_encoder: &Arc<DeltaEncoder>,
    metrics: &Arc<Metrics>,
) -> edge_features::EdgeFeaturePacket {
    let mempool = mempool_state.read().await;
    let welford_state = welford.read().await;
    let quantile_state = quantiles.read().await;
    let filter = fee_filter.read().await;
    
    let quintiles = quantile_state.quantiles();
    
    edge_features::EdgeFeaturePacket {
        timestamp_ns: chrono::Utc::now().timestamp_nanos_opt().unwrap_or(0) as u64,
        sequence_number,
        fee_mean_sat_vb: welford_state.mean(),
        fee_stddev_sat_vb: welford_state.stddev(),
        fee_zscore_latest: welford_state.last_zscore(),
        fee_sample_count: welford_state.count(),
        fee_p20: quintiles.p20,
        fee_p40: quintiles.p40,
        fee_p60: quintiles.p60,
        fee_p80: quintiles.p80,
        mempool_tx_count: mempool.tx_count() as u32,
        mempool_bytes: mempool.total_bytes(),
        blockspace_stress_score: mempool.stress_score(),
        deltas: delta_encoder.drain_deltas(),
        min_fee_threshold: filter.threshold(),
        filtered_tx_count: filter.filtered_count() as u32,
        delta_count: 0, // Will be set after drain
        uptime_seconds: 0, // TODO: Track uptime
        packets_sent: metrics.packets_sent.get() as u64,
        bytes_sent: metrics.bytes_sent.get() as u64,
    }
}
