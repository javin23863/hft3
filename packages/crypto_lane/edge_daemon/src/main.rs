use anyhow::Result;
use log::{error, info};
use prost::Message;
use std::sync::Arc;
use tokio::sync::RwLock;

mod config;
mod delta_encoder;
mod edge_features;
mod fee_filter;
mod mempool_state;
mod metrics;
mod serializer;
mod streaming;
mod tcp_sender;
mod zmq_subscriber;

use config::Config;
use delta_encoder::DeltaEncoder;
use edge_features::RemovalReason;
use fee_filter::FeeFilter;
use mempool_state::MempoolState;
use metrics::Metrics;
use streaming::{FeeQuantiles, WelfordState};
use tcp_sender::TcpSender;
use zmq_subscriber::ZmqSubscriber;

#[tokio::main]
async fn main() -> Result<()> {
    // Initialize logging
    env_logger::init();
    info!("Bitcoin Edge Daemon starting...");

    // Load configuration
    let config = Config::load()?;
    info!(
        "Configuration loaded: zmq_rawtx={}, zmq_rawblock={}, rpc_url={}, chicago_addr={}, packet_interval={}, fee_filter_enabled={}, fee_filter_blocks={}, metrics_port={}",
        config.zmq_rawtx,
        config.zmq_rawblock,
        redact_rpc_url(&config.rpc_url),
        config.chicago_addr,
        config.packet_interval,
        config.fee_filter_enabled,
        config.fee_filter_blocks,
        config.metrics_port,
    );

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
    let mut zmq_sub = ZmqSubscriber::connect(&config.zmq_rawtx, &config.zmq_rawblock)?;
    info!(
        "Subscribed to ZMQ: rawtx={}, rawblock={}",
        config.zmq_rawtx, config.zmq_rawblock
    );

    // Main event loop
    let mut sequence_number = 0u64;
    let mut message_count = 0u64;
    let packet_interval = config.packet_interval.max(1) as u64;

    loop {
        match zmq_sub.recv_multipart() {
            Ok(parts) => {
                if parts.len() < 2 {
                    continue;
                }

                let topic = &parts[0];
                let message = &parts[1];
                metrics.zmq_messages_received.inc();
                message_count += 1;

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
                        )
                        .await
                        {
                            error!("Error processing transaction: {}", e);
                        }
                    }
                    b"hashblock" => {
                        // Process block
                        if let Err(e) = process_block(
                            message,
                            &mempool_state,
                            &fee_filter,
                            &delta_encoder,
                            &metrics,
                        )
                        .await
                        {
                            error!("Error processing block: {}", e);
                        }
                    }
                    _ => {}
                }

                // Send periodic feature packet
                if message_count % packet_interval == 0 {
                    let packet = build_feature_packet(
                        sequence_number,
                        &mempool_state,
                        &welford,
                        &quantiles,
                        &fee_filter,
                        &delta_encoder,
                        &metrics,
                    )
                    .await;

                    if let Err(e) = tcp_sender.send_packet(&packet).await {
                        error!("Error sending packet: {}", e);
                        // Attempt reconnection
                        match TcpSender::connect(&config.chicago_addr).await {
                            Ok(sender) => tcp_sender = sender,
                            Err(e) => error!("Reconnection failed: {}", e),
                        }
                    } else {
                        metrics.packets_sent.inc();
                        metrics.bytes_sent.inc_by((packet.encoded_len() + 4) as f64);
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
    let meta = {
        let filter = fee_filter.read().await;
        filter.fetch_mempool_entry_from_raw_tx(raw_tx).await?
    };

    let Some(meta) = meta else {
        return Ok(());
    };

    {
        let mempool = mempool_state.read().await;
        if mempool.get(&meta.txid).is_some() {
            return Ok(());
        }
    }

    if !fee_filter.write().await.should_process(meta.fee_rate) {
        metrics.transactions_filtered.inc();
        return Ok(());
    }

    let timestamp_ns = chrono::Utc::now().timestamp_nanos_opt().unwrap_or(0) as u64;
    let mempool_bytes = {
        let mut mempool = mempool_state.write().await;
        mempool.add(meta.txid, meta.fee_rate, meta.size, timestamp_ns);
        mempool.total_bytes()
    };

    let (fee_mean, fee_stddev) = {
        let mut stats = welford.write().await;
        stats.update(meta.fee_rate);
        (stats.mean(), stats.stddev())
    };

    quantiles.write().await.add(meta.fee_rate);
    delta_encoder.add_tx(meta.txid, meta.fee_rate, meta.size, timestamp_ns);

    metrics.mempool_size.set(mempool_bytes as f64);
    metrics.fee_mean.set(fee_mean);
    metrics.fee_stddev.set(fee_stddev);

    metrics.transactions_processed.inc();
    Ok(())
}

async fn process_block(
    block_hash: &[u8],
    mempool_state: &Arc<RwLock<MempoolState>>,
    fee_filter: &Arc<RwLock<FeeFilter>>,
    delta_encoder: &Arc<DeltaEncoder>,
    metrics: &Arc<Metrics>,
) -> Result<()> {
    let txids_result = {
        let filter = fee_filter.read().await;
        filter.fetch_block_txids_from_zmq_hash(block_hash).await
    };

    match &txids_result {
        Ok(txids) if !txids.is_empty() => {
            let mut mempool = mempool_state.write().await;
            for txid in txids {
                if mempool.remove(txid).is_some() {
                    delta_encoder.remove_tx(*txid, RemovalReason::BlockInclusion);
                }
            }
        }
        _ => {}
    }

    fee_filter.write().await.update_threshold().await?;
    metrics
        .mempool_size
        .set(mempool_state.read().await.total_bytes() as f64);
    txids_result?;

    Ok(())
}

fn redact_rpc_url(url: &str) -> String {
    if let Some(scheme_end) = url.find("://") {
        let authority_start = scheme_end + 3;
        if let Some(at_offset) = url[authority_start..].find('@') {
            let at = authority_start + at_offset;
            return format!("{}<redacted>@{}", &url[..authority_start], &url[at + 1..]);
        }
    }
    url.to_string()
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

    let deltas = delta_encoder.drain_deltas();
    let delta_count = deltas.len() as u32;

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
        deltas,
        min_fee_threshold: filter.threshold(),
        filtered_tx_count: filter.filtered_count() as u32,
        delta_count,
        uptime_seconds: 0, // TODO: Track uptime
        packets_sent: metrics.packets_sent.get() as u64,
        bytes_sent: metrics.bytes_sent.get() as u64,
    }
}
