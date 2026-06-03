# Rithmic Diamond Cutter Access Sample

Purpose: provide Rithmic with a meaningful, reproducible C++ sample showing that
HFT3 already has a native R|API+ order path, can send tagged test orders, can
measure submit-to-ack latency from callbacks, and has a concrete reason to
request Diamond / Diamond Cutter access.

Do not include credentials in this packet. Runtime credentials live in operator
environment files and must not be committed or emailed.

## Request Summary

We are requesting access to Rithmic Diamond / Diamond Cutter, or the nearest
Rithmic Test Chicago/Aurora co-located connection parameters, for a Linux C++
evaluation on CHI404.

Current status:

- API currently integrated: R|API+ C++ 13.7.0.0.
- Current authorized system: Rithmic Test / Orangeburg.
- Current host: CHI404, QuantVPS Chicago bare metal.
- Current hot sample: `rithmic_gateway/tools/rithmic_capi_latency_probe.cpp`.
- Current bottleneck: Test/Orangeburg route, not Python or local app overhead.

Target:

- Validate a sub-millisecond co-located order path.
- If approved for Diamond, port the current sample to the Diamond C/C++ API and
  run the same tagged-order latency harness under Rithmic supervision.

## Included Sample

The meaningful sample is the native C++ C API probe:

```text
rithmic_gateway/tools/rithmic_capi_latency_probe.cpp
```

The sample:

- Loads Rithmic connection parameters from a YAML config file.
- Accepts `RITHMIC_CONFIG_PATH` to switch systems/gateways without rebuilding.
- Accepts connect-point overrides:
  - `RITHMIC_MD_CONNECT_POINT`
  - `RITHMIC_TS_CONNECT_POINT`
  - `RITHMIC_REP_CONNECT_POINT`
  - `RITHMIC_PNL_CONNECT_POINT`
  - `RITHMIC_IH_CONNECT_POINT`
- Initializes the native R|API+ adapter through `librithmic_gateway_shared`.
- Logs into repository, order, market data, PnL, and history connect points.
- Warms price-increment metadata once, outside the measured order loop.
- Sends tagged limit orders using unique `user_msg` values.
- Pairs order callbacks by `user_msg` / tag.
- Measures submit-to-callback latency in native C++ using monotonic time.
- Cancels after ack when configured.
- Emits machine-readable per-order results and summary percentiles.

The adapter side is implemented in:

```text
rithmic_gateway/src/rithmic_adapter.cpp
rithmic_gateway/src/c_api.cpp
rithmic_gateway/include/c_api.hpp
rithmic_gateway/include/rithmic_adapter.hpp
```

## Build And Run

Build on CHI404:

```bash
cd /root/hft3/repo
cmake --build build --target rithmic_capi_latency_probe -j2
```

Run against the currently provided Rithmic Test / Orangeburg config:

```bash
cd /root/hft3/repo
set -a
source /root/hft3/.env
set +a

export HFT3_REPO_DIR=/root/hft3/repo
export RITHMIC_CONFIG_PATH=/root/hft3/repo/packages/data_system/config/rithmic_api_test.yaml
export RITHMIC_PROBE_SYMBOL=MESM6
export RITHMIC_PROBE_EXCHANGE=CME
export RITHMIC_PROBE_ORDER_PRICE=5000.0
export RITHMIC_PROBE_ORDER_COUNT=20
export RITHMIC_PROBE_ORDER_QTY=1
export RITHMIC_PROBE_ORDER_TIMEOUT_MS=7000
export RITHMIC_PROBE_ORDER_INTERVAL_US=0
export RITHMIC_PROBE_CANCEL_AFTER_ACK=1
export RITHMIC_PROBE_SKIP_EXTERNAL_WARM=0

timeout 90s chrt -f 80 taskset -c 0,1 \
  ./build/rithmic_gateway/rithmic_capi_latency_probe
```

When Rithmic provides a Chicago/Aurora Test or Diamond-compatible config, the
same harness should be run with only `RITHMIC_CONFIG_PATH` and any connect-point
names changed.

## Observed Evidence

Native C++ R|API+ burst on CHI404, Rithmic Test / Orangeburg:

```text
count=20
ack=20
reject=0
failure=0
timeout=0
min_us=20527.312
avg_us=20807.716
p50_us=20859.855
p90_us=20957.558
p99_us=21045.502
max_us=21045.502
```

Config-driven probe regression check after the latest hotpatch:

```text
count=5
ack=5
reject=0
failure=0
timeout=0
min_us=20487.886
avg_us=20561.015
p50_us=20563.789
p90_us=20646.353
p99_us=20646.353
max_us=20646.353
```

Network check from CHI404 to the provided Test / Orangeburg host:

```text
rituz00100.00.rithmic.com -> 38.79.0.86
ping avg ~= 19.37 ms
```

Older R|Trader logs on CHI404 show a Rithmic Paper Trading / Chicago Area route
using `rithmic_paper_prod_domain` and `_paperc` connect points. Resolved admin
IPs from that route pinged around 2.16 ms and 4.07 ms from CHI404. Those logs
demonstrate a faster Chicago route exists, but that route is not the same as the
current Rithmic Test / Orangeburg API credentials and parameters.

## Why Diamond / Diamond Cutter Is Needed

The current native C++ hot path is not the primary latency wall. The measured
order ack distribution is closely aligned with the network RTT to the provided
Orangeburg Test host. That makes the current environment unsuitable for
sub-millisecond co-located evaluation.

Rithmic's public API Suite page describes:

- R|API+ as the current C++/.NET API path.
- R|Diamond as the Linux C/C++ co-location API path for ultra-low-latency
  tick-to-trade evaluation.

The current HFT3 sample is ready to be used as the starting conformance artifact
for a Diamond / Diamond Cutter evaluation. It shows a real, auditable order
submission loop, callback pairing, latency measurement, and config-driven
gateway switching.

## What We Need From Rithmic

Preferred:

```text
Rithmic Diamond / Diamond Cutter Linux C/C++ evaluation access
Rithmic Test or supervised evaluation credentials
Chicago/Aurora co-located gateway / connection parameters
Any required conformance script or order-flow constraints
```

Fallback:

```text
Rithmic Test Chicago/Aurora R|API+ connection_params.txt
Authorized credentials for that Test gateway
Expected connect points for repository, order, market data, PnL, and history
```

## Vendor-Facing Message

```text
Hello Rithmic API team,

We have completed a native Linux C++ R|API+ integration and would like to
request Diamond / Diamond Cutter evaluation access, or the closest supervised
co-located Test path available.

Our current sample is a C++ tagged-order latency harness that connects through
R|API+, sends test limit orders, pairs order callbacks by user message/tag, and
emits per-order submit-to-ack latency percentiles. It runs on CHI404, a
Chicago-hosted bare-metal server.

Using the Rithmic Test / Orangeburg parameters currently provided to us, the
native C++ order ack distribution is approximately:

  p50: 20.86 ms
  p99: 21.05 ms
  rejects/timeouts: 0

The CHI404 RTT to the provided Orangeburg Test host is approximately 19.37 ms,
so the current limitation appears to be route/topology rather than application
runtime overhead. We are seeking Diamond / Diamond Cutter access so we can
evaluate the intended co-located Linux C/C++ path under your guidance.

We can provide the C++ source file, build command, sanitized run output, and
connection-parameter mapping on request. Credentials are not embedded in the
sample and are supplied only via runtime environment variables.
```

