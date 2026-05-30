// CHI404 Rithmic application latency probe (order path RTT).
// BLOCKED: requires R|API+ SDK wiring on colo; not built in CI until available.

#include <cstdio>

int main() {
    std::fprintf(stderr,
        "rithmic_latency_probe: BLOCKED — R|API+ not wired. "
        "Implement tick→ack timestamps via R|API+ on CHI404 only.\n");
    return 2;
}
