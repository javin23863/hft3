use prometheus::{Counter, Gauge, Registry, opts};

/// Prometheus metrics for monitoring
pub struct Metrics {
    pub packets_sent: Counter,
    pub bytes_sent: Counter,
    pub transactions_processed: Counter,
    pub transactions_filtered: Counter,
    pub zmq_messages_received: Counter,
    pub tcp_connection_failures: Counter,
    pub mempool_size: Gauge,
    pub fee_mean: Gauge,
    pub fee_stddev: Gauge,
    registry: Registry,
}

impl Metrics {
    pub fn new() -> Self {
        let registry = Registry::new();
        
        let packets_sent = Counter::with_opts(
            opts!("edge_packets_sent_total", "Total packets sent to Chicago")
        ).unwrap();
        
        let bytes_sent = Counter::with_opts(
            opts!("edge_bytes_sent_total", "Total bytes sent to Chicago")
        ).unwrap();
        
        let transactions_processed = Counter::with_opts(
            opts!("edge_transactions_processed_total", "Total transactions processed")
        ).unwrap();
        
        let transactions_filtered = Counter::with_opts(
            opts!("edge_transactions_filtered_total", "Total transactions filtered out")
        ).unwrap();
        
        let zmq_messages_received = Counter::with_opts(
            opts!("edge_zmq_messages_received_total", "Total ZMQ messages received")
        ).unwrap();
        
        let tcp_connection_failures = Counter::with_opts(
            opts!("edge_tcp_connection_failures_total", "Total TCP connection failures")
        ).unwrap();
        
        let mempool_size = Gauge::with_opts(
            opts!("edge_mempool_size_bytes", "Current mempool size in bytes")
        ).unwrap();
        
        let fee_mean = Gauge::with_opts(
            opts!("edge_fee_mean_sat_vb", "Current mean fee rate")
        ).unwrap();
        
        let fee_stddev = Gauge::with_opts(
            opts!("edge_fee_stddev_sat_vb", "Current fee rate standard deviation")
        ).unwrap();
        
        registry.register(Box::new(packets_sent.clone())).unwrap();
        registry.register(Box::new(bytes_sent.clone())).unwrap();
        registry.register(Box::new(transactions_processed.clone())).unwrap();
        registry.register(Box::new(transactions_filtered.clone())).unwrap();
        registry.register(Box::new(zmq_messages_received.clone())).unwrap();
        registry.register(Box::new(tcp_connection_failures.clone())).unwrap();
        registry.register(Box::new(mempool_size.clone())).unwrap();
        registry.register(Box::new(fee_mean.clone())).unwrap();
        registry.register(Box::new(fee_stddev.clone())).unwrap();
        
        Self {
            packets_sent,
            bytes_sent,
            transactions_processed,
            transactions_filtered,
            zmq_messages_received,
            tcp_connection_failures,
            mempool_size,
            fee_mean,
            fee_stddev,
            registry,
        }
    }
    
    /// Export metrics in Prometheus format
    pub fn export(&self) -> String {
        use prometheus::Encoder;
        let encoder = prometheus::TextEncoder::new();
        let metric_families = self.registry.gather();
        let mut buffer = Vec::new();
        encoder.encode(&metric_families, &mut buffer).unwrap();
        String::from_utf8(buffer).unwrap()
    }
}
