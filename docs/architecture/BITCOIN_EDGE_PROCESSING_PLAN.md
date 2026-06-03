# Bitcoin Edge Processing Architecture - Implementation Plan

**Status**: Approved for Implementation  
**Created**: 2026-06-03  
**Priority**: High  
**Timeline**: 6 weeks

## Executive Summary

Transform the Bitcoin node from a passive data source into an active edge processing layer that computes streaming features locally and transmits only high-density feature vectors to Chicago, reducing WAN bandwidth by 90-95% and improving T_avail from ~15 minutes to ~100-300ms.

## Problem Statement

Current architecture polls Bitcoin Core every 15 minutes via RPC, resulting in:
- **T_avail**: ~15 minutes (900,000 ms) - unacceptable for HFT
- **Bandwidth**: 150KB-2MB per poll when streaming raw mempool
- **Feature latency**: 15 minutes between observations
- **Computation**: Rolling window z-scores computed in Chicago with 48-sample history

## Solution: Edge Processing

Deploy a lightweight Rust daemon on the Bitcoin node that:
1. Subscribes to ZMQ mempool notifications (real-time)
2. Computes streaming features using O(1) algorithms (Welford's, t-digest)
3. Filters low-fee transactions (70-80% noise reduction)
4. Encodes deltas (Add/Remove/Replace operations)
5. Serializes to Protocol Buffers (12-40KB packets)
6. Streams to Chicago via persistent TCP connection

## Architecture

```
Bitcoin Node (Contabo VPS)
├── Bitcoin Core
│   └── ZMQ: rawtx (28333), rawblock (28332)
└── Edge Daemon (Rust)
    ├── ZMQ Subscriber (SO_TIMESTAMPNS)
    ├── Streaming Algorithms
    │   ├── Welford's (z-scores)
    │   └── t-digest (quantiles)
    ├── Fee Filter (estimatesmartfee threshold)
    ├── Delta Encoder (A/R/C operations)
    ├── Protobuf Serializer
    └── TCP Sender → SSH Tunnel → Chicago

Chicago (CHI404)
└── Edge Receiver (Python)
    ├── Protobuf Deserializer
    ├── Sequence Validator
    └── Feature Matrix Integration
```

## Components

### 1. Edge Daemon (Rust)

**Location**: `packages/crypto_lane/edge_daemon/`

**Why Rust**:
- Zero-cost abstractions for streaming algorithms
- Memory safety without GC pauses
- Native ZMQ bindings (`zmq` crate)
- Protobuf support (`prost` crate)
- SO_TIMESTAMPNS socket options
- Single binary deployment (~5MB)

**Core Modules**:
- `zmq_subscriber.rs` - ZMQ subscription with kernel timestamps
- `streaming/welford.rs` - O(1) mean/variance/z-score computation
- `streaming/tdigest.rs` - Streaming quantile estimation
- `fee_filter.rs` - Dynamic threshold pruning
- `delta_encoder.rs` - Mempool state tracking + delta encoding
- `serializer.rs` - Protobuf serialization
- `tcp_sender.rs` - Persistent TCP streaming to Chicago
- `metrics.rs` - Prometheus metrics export

### 2. Streaming Algorithms

#### Welford's Algorithm (O(1) z-score)

For each incoming transaction fee x_n:
```
μ_n = μ_{n-1} + (x_n - μ_{n-1}) / n
M_n = M_{n-1} + (x_n - μ_{n-1})(x_n - μ_n)
σ_n = sqrt(M_n / (n-1))
Z_n = (x_n - μ_n) / σ_n
```

**Benefits**: O(1) memory, O(1) computation, numerically stable

#### t-digest (Streaming Quantiles)

Adaptive clustering algorithm for quantile estimation:
- Compression parameter: δ=100
- Memory: ~2KB for 100 centroids
- Provides: p20, p40, p60, p80 quintiles
- No sorting required

### 3. Dynamic Threshold Pruning

Use `estimatesmartfee(1)` to determine minimum fee rate for next block inclusion.
Filter out 70-80% of low-fee transactions at source.

### 4. Delta Encoding

Track mempool state locally, transmit only changes:
- **Add**: New high-fee transaction enters mempool
- **Remove**: Transaction included in block or expired
- **Replace**: RBF (Replace-by-Fee) transaction replacement

### 5. Protocol Buffers Schema

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
}
```

**Expected packet size**: 12-40 KB (vs 150KB-2MB raw JSON)

### 6. Chicago Integration

**New Module**: `packages/crypto_lane/src/ingest/edge_receiver.py`

- TCP server listening on localhost:9876
- Protobuf deserialization
- Sequence number validation
- Integration with `feature_matrix.py`
- Replace `rolling_fee_zscore()` with edge-computed values

## Performance Targets

| Metric | Current (Polling) | Target (Edge) | Improvement |
|--------|-------------------|---------------|-------------|
| T_avail | ~15 min (900,000 ms) | <300 ms | **3000x** |
| Bandwidth | 150KB-2MB per poll | 12-40KB continuous | **90-95% reduction** |
| Feature latency | 15 min | Real-time (50-200 ms) | **4500x** |
| CPU overhead | 2-5 ms (Chicago) | 0.1-0.5 ms (edge) | **10x** |
| Memory | 48-sample window | O(1) streaming | **Constant** |

## Deployment Strategy

### Phase 1: Development (2 weeks)
- Build edge daemon locally
- Test with mock ZMQ publisher
- Validate streaming algorithms
- Test Chicago receiver

### Phase 2: Bitcoin Node Deployment (1 week)
- Deploy to Contabo VPS
- Configure systemd service
- Validate ZMQ connection
- Test packet generation

### Phase 3: Chicago Integration (1 week)
- Deploy receiver on CHI404
- Configure SSH tunnel (RemoteForward)
- End-to-end testing
- Validate packet delivery

### Phase 4: Production Cutover (2 weeks)
- Run in parallel with polling (1 week)
- Validate accuracy (edge vs polling within 1%)
- Monitor T_avail improvement
- Cutover to edge-only
- Cleanup polling code

## Security Considerations

### CRITICAL: Credential Rotation Required

Before any deployment, ALL exposed credentials must be rotated:
- Bitcoin RPC password
- SSH keys (id_ed25519, ssh-key-2026-05-12.key)
- B2 API keys
- FRED API key
- Contabo object storage keys
- All other API keys

### Edge Daemon Security

1. **No secrets in code** - Load from environment variables or encrypted config
2. **SSH tunnel encryption** - All traffic flows through SSH tunnel
3. **Minimal attack surface** - Edge daemon only connects outbound
4. **Monitoring** - Log all connections, alert on failures

## Testing Strategy

### Unit Tests
- Welford's algorithm accuracy (compare with numpy)
- t-digest quantile accuracy (compare with sorted array)
- Delta encoding state consistency
- Protobuf round-trip serialization

### Integration Tests
- Mock ZMQ publisher → edge daemon → TCP → Chicago receiver
- Validate packet delivery and sequence numbers
- Load test with 10,000 tx/sec

### Production Validation
- Parallel run (1 week) comparing edge vs polling features
- Backtest comparison (Sharpe ratios)
- T_avail measurement

## Rollback Plan

If edge processing fails:
1. Stop edge daemon service
2. Re-enable polling in `feature_matrix.py`
3. No data loss during transition

## Timeline

| Phase | Duration | Deliverables |
|-------|----------|--------------|
| Phase 1: Development | 2 weeks | Edge daemon, Chicago receiver, unit tests |
| Phase 2: Node Deployment | 1 week | Deploy to Contabo, validate ZMQ |
| Phase 3: Chicago Integration | 1 week | Deploy receiver, SSH tunnel, end-to-end test |
| Phase 4: Production Cutover | 2 weeks | Parallel run, validation, cutover |
| **Total** | **6 weeks** | Production-ready edge processing |

## Success Criteria

1. T_avail < 300ms (from 15 minutes)
2. Bandwidth reduction > 90%
3. Feature accuracy within 1% of polling baseline
4. Zero packet loss over 1 week production run
5. No security incidents
6. Backtest Sharpe ratio maintained or improved

## Next Steps

1. ✅ Commit this plan
2. ⏳ Begin Phase 1: Create Rust project structure
3. ⏳ Implement streaming algorithms (Welford's, t-digest)
4. ⏳ Build ZMQ subscriber
5. ⏳ Implement delta encoder
6. ⏳ Add Protobuf serialization
7. ⏳ Build TCP sender
8. ⏳ Create Chicago receiver
9. ⏳ Write comprehensive tests
10. ⏳ Deploy to Bitcoin node
11. ⏳ Integrate with Chicago
12. ⏳ Production cutover

## References

- Welford's Algorithm: https://en.wikipedia.org/wiki/Algorithms_for_calculating_variance#Welford's_online_algorithm
- t-digest: https://github.com/tdunning/t-digest
- Bitcoin Core ZMQ: https://github.com/bitcoin/bitcoin/blob/master/doc/zmq.md
- Protocol Buffers: https://protobuf.dev/
