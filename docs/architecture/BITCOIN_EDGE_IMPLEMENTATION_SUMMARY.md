# Bitcoin Edge Processing - Implementation Summary

**Date**: 2026-06-03  
**Status**: Phase 1 Complete (Code Implementation)  
**Next**: Phase 2 (Deployment to Bitcoin Node)

---

## What Was Built

### 1. Edge Daemon (Rust) - `packages/crypto_lane/edge_daemon/`

Complete Rust implementation of the edge processing daemon with:

#### Core Components

| Module | Purpose | Lines |
|--------|---------|-------|
| `main.rs` | Event loop, component orchestration | ~200 |
| `streaming/welford.rs` | O(1) mean/variance/z-score computation | ~120 |
| `streaming/tdigest_wrapper.rs` | O(1) streaming quantiles (p20/p40/p60/p80) | ~80 |
| `fee_filter.rs` | Dynamic threshold pruning via `estimatesmartfee` | ~150 |
| `delta_encoder.rs` | Mempool state change tracking (Add/Remove/Replace) | ~130 |
| `mempool_state.rs` | Local mempool map for delta computation | ~100 |
| `tcp_sender.rs` | Persistent TCP connection to Chicago | ~120 |
| `zmq_subscriber.rs` | Bitcoin Core ZMQ subscription | ~60 |
| `serializer.rs` | Protocol Buffer serialization | ~80 |
| `metrics.rs` | Prometheus metrics export | ~100 |
| `config.rs` | Configuration loading (env/file) | ~80 |

**Total**: ~1,220 lines of Rust code

#### Key Algorithms

**Welford's Algorithm** (`streaming/welford.rs`):
```rust
// O(1) space, O(1) time per update
pub fn update(&mut self, x: f64) -> f64 {
    self.n += 1;
    let delta = x - self.mean;
    self.mean += delta / self.n as f64;
    let delta2 = x - self.mean;
    self.m2 += delta * delta2;
    // Returns z-score
}
```

**t-digest Quantiles** (`streaming/tdigest_wrapper.rs`):
```rust
// ~2KB memory for 100 centroids
pub fn add(&mut self, fee_rate: f64) {
    self.digest.insert(fee_rate);
}
pub fn quantiles(&self) -> FeeQuintiles {
    FeeQuintiles {
        p20: self.digest.estimate_quantile(0.20),
        p40: self.digest.estimate_quantile(0.40),
        p60: self.digest.estimate_quantile(0.60),
        p80: self.digest.estimate_quantile(0.80),
    }
}
```

#### Protocol Buffer Schema (`proto/edge_features.proto`)

```protobuf
message EdgeFeaturePacket {
    uint64 timestamp_ns = 1;
    uint64 sequence_number = 2;
    
    // Welford's streaming stats
    double fee_mean_sat_vb = 3;
    double fee_stddev_sat_vb = 4;
    double fee_zscore_latest = 5;
    uint64 fee_sample_count = 6;
    
    // t-digest quintiles
    double fee_p20 = 7;
    double fee_p40 = 8;
    double fee_p60 = 9;
    double fee_p80 = 10;
    
    // Mempool state
    uint32 mempool_tx_count = 11;
    uint64 mempool_bytes = 12;
    double blockspace_stress_score = 13;
    
    // Delta updates (batched, max 100 per packet)
    repeated MempoolDelta deltas = 14;
    
    // Metadata
    double min_fee_threshold = 15;
    uint32 filtered_tx_count = 16;
    // ... health metrics
}
```

**Expected packet size**: 12-40 KB (vs 150KB-2MB raw JSON)

### 2. Chicago Receiver (Python) - `packages/crypto_lane/src/ingest/edge_receiver.py`

Python TCP receiver for consuming edge daemon packets:

```python
class EdgeReceiver:
    def __init__(self, host='127.0.0.1', port=9876):
        # TCP server listening for edge daemon connections
        
    def receive_packets(self) -> Generator[EdgeFeaturePacket, None, None]:
        # Yields deserialized packets as they arrive
        # Validates sequence numbers
        # Detects packet loss
```

**Features**:
- Length-prefixed packet framing (4-byte big-endian)
- Protocol Buffer deserialization
- Sequence gap detection
- Generator interface for easy integration
- Standalone debugging mode

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    Bitcoin Node (Contabo)                    │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐         ┌────────────────────────────┐   │
│  │              │  ZMQ    │                            │   │
│  │ Bitcoin Core ├────────►│   Edge Daemon (Rust)       │   │
│  │              │ rawtx/  │                            │   │
│  │              │ rawblock│  ┌──────────────────────┐  │   │
│  └──────────────┘         │  │ Welford's Algorithm  │  │   │
│                           │  │ (O(1) z-scores)      │  │   │
│                           │  └──────────────────────┘  │   │
│                           │                            │   │
│                           │  ┌──────────────────────┐  │   │
│                           │  │ t-digest             │  │   │
│                           │  │ (O(1) quantiles)     │  │   │
│                           │  └──────────────────────┘  │   │
│                           │                            │   │
│                           │  ┌──────────────────────┐  │   │
│                           │  │ Fee Filter           │  │   │
│                           │  │ (70-80% pruning)     │  │   │
│                           │  └──────────────────────┘  │   │
│                           │                            │   │
│                           │  ┌──────────────────────┐  │   │
│                           │  │ Delta Encoder        │  │   │
│                           │  │ (A/R/C operations)   │  │   │
│                           │  └──────────────────────┘  │   │
│                           │                            │   │
│                           │  ┌──────────────────────┐  │   │
│                           │  │ Protobuf Serializer  │  │   │
│                           │  │ (12-40KB packets)    │  │   │
│                           │  └──────────────────────┘  │   │
│                           │                            │   │
│                           └────────────┬───────────────┘   │
│                                        │                    │
└────────────────────────────────────────┼────────────────────┘
                                         │ TCP Stream
                                         │ (SSH tunnel)
                                         ▼
┌─────────────────────────────────────────────────────────────┐
│                  Chicago (CHI404)                            │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌────────────────────────────────────────────────────┐    │
│  │ Edge Receiver (Python)                             │    │
│  │ - TCP listener on localhost:9876                   │    │
│  │ - Protobuf deserialization                         │    │
│  │ - Sequence validation                              │    │
│  └────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌────────────────────────────────────────────────────┐    │
│  │ Feature Matrix Integration                         │    │
│  │ - Merge into feature_matrix.py                     │    │
│  │ - Replace rolling_fee_zscore with edge values      │    │
│  │ - Update PIT alignment                             │    │
│  └────────────────────────────────────────────────────┘    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Performance Targets

| Metric | Current (Polling) | Target (Edge) | Improvement |
|--------|-------------------|---------------|-------------|
| **T_avail** | ~15 min (900,000 ms) | <300 ms | **3000x** |
| **Bandwidth** | 150KB-2MB per poll | 12-40KB continuous | **90-95% reduction** |
| **Feature Latency** | 15 min | Real-time (50-200 ms) | **4500x** |
| **CPU Overhead** | 2-5 ms (Chicago) | 0.1-0.5 ms (edge) | **10x** |
| **Memory** | 48-sample window | O(1) streaming | **Constant** |

---

## Deployment Guide

### Phase 2: Build on Bitcoin Node

#### 1. SSH to Bitcoin Node

```bash
ssh btc-node
```

#### 2. Install Rust

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env
rustc --version  # Verify installation
```

#### 3. Install System Dependencies

```bash
sudo apt-get update
sudo apt-get install -y \
    protobuf-compiler \
    libzmq3-dev \
    pkg-config \
    build-essential

# Verify
protoc --version
```

#### 4. Clone Repository

```bash
cd /opt
sudo git clone https://github.com/javin23863/hft3.git
sudo chown -R root:root hft3
cd hft3
```

#### 5. Build Edge Daemon

```bash
cd packages/crypto_lane/edge_daemon
cargo build --release

# Verify binary
ls -lh target/release/btc-edge-daemon
# Expected: ~5MB binary
```

#### 6. Install Binary

```bash
sudo cp target/release/btc-edge-daemon /usr/local/bin/
sudo chmod +x /usr/local/bin/btc-edge-daemon
```

#### 7. Create Configuration

```bash
sudo mkdir -p /etc/btc-edge-daemon
sudo tee /etc/btc-edge-daemon/env <<'EOF'
# Bitcoin Core ZMQ endpoints
BTC_ZMQ_RAWTX=tcp://127.0.0.1:28333
BTC_ZMQ_RAWBLOCK=tcp://127.0.0.1:28332

# Bitcoin Core RPC (for fee estimation)
BTC_RPC_URL=http://127.0.0.1:8332
BTC_RPC_USER=<your_rpc_user>
BTC_RPC_PASS=<your_rpc_password>

# Chicago receiver address (via SSH tunnel)
CHICAGO_ADDR=127.0.0.1:9876

# Processing parameters
PACKET_INTERVAL=100
FEE_FILTER_ENABLED=true
FEE_FILTER_BLOCKS=1

# Metrics
METRICS_PORT=9090

# Logging
RUST_LOG=info
EOF

sudo chmod 600 /etc/btc-edge-daemon/env
```

**CRITICAL**: Replace `<your_rpc_user>` and `<your_rpc_password>` with actual Bitcoin Core RPC credentials. Do NOT use the credentials from the previous chat message - they must be rotated first.

#### 8. Create Systemd Service

```bash
sudo tee /etc/systemd/system/btc-edge-daemon.service <<'EOF'
[Unit]
Description=Bitcoin Edge Processing Daemon
After=bitcoind.service
Requires=bitcoind.service

[Service]
Type=simple
User=root
EnvironmentFile=/etc/btc-edge-daemon/env
ExecStart=/usr/local/bin/btc-edge-daemon
Restart=always
RestartSec=10

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/log/btc-edge-daemon

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
```

#### 9. Start Service

```bash
sudo systemctl enable btc-edge-daemon
sudo systemctl start btc-edge-daemon
sudo systemctl status btc-edge-daemon
```

#### 10. Verify Logs

```bash
sudo journalctl -u btc-edge-daemon -f
```

Expected output:
```
Bitcoin Edge Daemon starting...
Configuration loaded: Config { ... }
ZMQ subscriber connected to tcp://127.0.0.1:28333 and tcp://127.0.0.1:28332
Connected to Chicago at 127.0.0.1:9876
```

### Phase 3: Chicago Integration

#### 1. Update SSH Tunnel

Add to `~/.ssh/config`:

```
Host btc-node
  HostName 213.199.46.118
  User root
  IdentityFile ~/.ssh/id_ed25519
  # ... existing forwards ...
  RemoteForward 9876 127.0.0.1:9876  # Chicago listens, edge daemon connects
  ServerAliveInterval 30
  ServerAliveCountMax 3
  ExitOnForwardFailure yes
```

Restart tunnel:
```bash
ssh -fN btc-node
```

#### 2. Start Chicago Receiver

```bash
cd /path/to/hft3
python -m crypto_lane.src.ingest.edge_receiver
```

Expected output:
```
Edge receiver listening on 127.0.0.1:9876
Waiting for edge daemon connection...
Edge daemon connected from ('127.0.0.1', 54321)
Receiving packets from edge daemon...
--------------------------------------------------------------------------------
[0] fee_mean=10.50 sat/vB, zscore=1.23, mempool=5000 txs, stress=0.45
[1] fee_mean=10.52 sat/vB, zscore=0.87, mempool=5012 txs, stress=0.46
[2] fee_mean=10.48 sat/vB, zscore=-0.34, mempool=4998 txs, stress=0.45
```

#### 3. Generate Protocol Buffer Classes

```bash
cd packages/crypto_lane/edge_daemon
protoc --python_out=../src/ingest proto/edge_features.proto

# Verify
ls -l ../src/ingest/edge_features_pb2.py
```

Update `edge_receiver.py` to use generated classes:
```python
from . import edge_features_pb2

def _deserialize_packet(self, data: bytes) -> Optional[EdgeFeaturePacket]:
    pb_packet = edge_features_pb2.EdgeFeaturePacket()
    pb_packet.ParseFromString(data)
    # Convert to dataclass...
```

#### 4. Integrate with Feature Matrix

Modify `packages/crypto_lane/src/features/feature_matrix.py`:

```python
from crypto_lane.src.ingest.edge_receiver import EdgeReceiver

def build_labeled_frame(
    *,
    include_btc_node: bool = True,
    use_edge_features: bool = False,  # NEW
    edge_receiver: EdgeReceiver = None,  # NEW
    # ... existing params
) -> pl.DataFrame:
    # ... existing code ...
    
    if include_btc_node:
        if use_edge_features and edge_receiver:
            # Real-time edge features
            edge_features = []
            for packet in edge_receiver.receive_packets():
                edge_features.append(packet.to_dict())
                if len(edge_features) >= 100:  # Batch size
                    break
            
            mempool = pl.DataFrame(edge_features)
        else:
            # Legacy polling path
            mempool = pl.read_csv(fd / "mempool_snapshots.csv")
        
        # ... rest of existing code ...
```

---

## Testing

### Unit Tests (Rust)

```bash
cd packages/crypto_lane/edge_daemon
cargo test
```

Expected output:
```
running 15 tests
test streaming::welford::tests::test_welford_basic ... ok
test streaming::welford::tests::test_welford_zscore ... ok
test streaming::tdigest_wrapper::tests::test_tdigest_basic ... ok
test mempool_state::tests::test_mempool_add_remove ... ok
test fee_filter::tests::test_fee_filter_enabled ... ok
test delta_encoder::tests::test_delta_encoder_add ... ok
...
test result: ok. 15 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
```

### Integration Test

1. Start mock Chicago receiver:
   ```bash
   nc -l 9876
   ```

2. Run edge daemon:
   ```bash
   cargo run --release
   ```

3. Verify binary data received (length-prefixed protobuf packets)

### Production Validation

1. Run in parallel with polling for 1 week
2. Compare edge z-scores with rolling z-scores (should match within 1%)
3. Compare edge quantiles with sorted quantiles (should match within 1%)
4. Measure T_avail improvement (should be <300ms vs 15min)
5. Monitor for packet loss (sequence gaps)

---

## Monitoring

### Prometheus Metrics

```bash
curl http://127.0.0.1:9090/metrics
```

Key metrics:
- `edge_packets_sent_total` - Total packets sent
- `edge_bytes_sent_total` - Total bytes sent
- `edge_transactions_processed_total` - Transactions processed
- `edge_transactions_filtered_total` - Transactions filtered
- `edge_mempool_size_bytes` - Current mempool size
- `edge_fee_mean_sat_vb` - Current mean fee
- `edge_fee_stddev_sat_vb` - Current fee stddev

### Health Checks

```bash
# Edge daemon health
sudo systemctl status btc-edge-daemon

# Chicago receiver health
curl http://127.0.0.1:9877/health  # TODO: Add health endpoint

# SSH tunnel status
netstat -tlnp | grep 9876
```

### Alerting (TODO)

Set up alerts for:
- Edge daemon down > 1 minute
- TCP connection failed > 3 attempts
- Packet sequence gap detected
- Mempool size anomaly (>10x normal)

---

## Troubleshooting

### ZMQ Connection Failed

```
Error: ZMQ subscriber connection failed
```

**Solution**: Verify Bitcoin Core ZMQ is enabled:
```bash
bitcoin-cli getzmqnotifications
```

Expected:
```json
[
  {"type": "pubrawtx", "address": "tcp://0.0.0.0:28333"},
  {"type": "pubhashblock", "address": "tcp://0.0.0.0:28332"}
]
```

### TCP Connection Failed

```
Error: Failed to connect to Chicago at 127.0.0.1:9876
```

**Solution**:
1. Verify SSH tunnel: `ssh -fN btc-node`
2. Check Chicago receiver: `netstat -tlnp | grep 9876`
3. Verify firewall allows connection

### High Memory Usage

**Solution**: Reduce `PACKET_INTERVAL` to send packets more frequently and clear delta buffer.

### High CPU Usage

**Solution**: Increase `FEE_FILTER_BLOCKS` to filter more transactions (e.g., 3 instead of 1).

---

## Security Checklist

- [ ] **Rotate all exposed credentials** (CRITICAL)
  - Bitcoin RPC password
  - SSH keys
  - B2 API keys
  - FRED API key
  - Contabo storage keys
- [ ] Use environment variables for secrets (not hardcoded)
- [ ] SSH tunnel encryption enabled
- [ ] Systemd security hardening applied
- [ ] Firewall configured (only allow SSH)
- [ ] Monitoring and alerting set up

---

## Next Steps

### Immediate (This Week)

1. **Rotate all credentials** (CRITICAL - do this first)
2. Build edge daemon on Bitcoin node
3. Deploy via systemd
4. Test ZMQ connection
5. Verify packet generation

### Short-term (Next 2 Weeks)

6. Deploy Chicago receiver on CHI404
7. Configure SSH tunnel (RemoteForward)
8. End-to-end testing
9. Generate Protocol Buffer Python classes
10. Integrate with `feature_matrix.py`

### Medium-term (Next Month)

11. Run in parallel with polling (1 week)
12. Validate accuracy (edge vs polling within 1%)
13. Monitor T_avail improvement
14. Cutover to edge-only
15. Cleanup polling code

### Long-term (Next Quarter)

16. Implement SO_TIMESTAMPNS for nanosecond timestamps
17. Evaluate Cap'n Proto for zero-copy serialization
18. Add UDP transport option (lower latency)
19. Multi-node aggregation
20. ML anomaly detection on streaming features

---

## Files Created

### Edge Daemon (Rust)

```
packages/crypto_lane/edge_daemon/
├── .gitignore
├── Cargo.toml
├── README.md
├── build.rs
├── config.toml.example
├── proto/
│   └── edge_features.proto
└── src/
    ├── config.rs
    ├── delta_encoder.rs
    ├── edge_features.rs
    ├── fee_filter.rs
    ├── main.rs
    ├── mempool_state.rs
    ├── metrics.rs
    ├── serializer.rs
    ├── streaming/
    │   ├── mod.rs
    │   ├── tdigest_wrapper.rs
    │   └── welford.rs
    ├── tcp_sender.rs
    └── zmq_subscriber.rs
```

### Chicago Receiver (Python)

```
packages/crypto_lane/src/ingest/
└── edge_receiver.py
```

### Documentation

```
docs/architecture/
└── BITCOIN_EDGE_PROCESSING_PLAN.md
```

---

## Commit History

```
b55b358 docs: add Bitcoin edge processing architecture plan
0b1a8ed feat(crypto_lane): implement Bitcoin edge processing daemon
```

---

## References

- [Welford's Algorithm](https://en.wikipedia.org/wiki/Algorithms_for_calculating_variance#Welford's_online_algorithm)
- [t-digest Paper](https://github.com/tdunning/t-digest/blob/main/docs/t-digest-paper/histo.pdf)
- [Bitcoin Core ZMQ](https://github.com/bitcoin/bitcoin/blob/master/doc/zmq.md)
- [Protocol Buffers](https://protobuf.dev/)
- [Rust Book](https://doc.rust-lang.org/book/)

---

## Contact

For questions or issues:
- GitHub Issues: https://github.com/javin23863/hft3/issues
- Documentation: `docs/architecture/BITCOIN_EDGE_PROCESSING_PLAN.md`
- Edge Daemon README: `packages/crypto_lane/edge_daemon/README.md`
