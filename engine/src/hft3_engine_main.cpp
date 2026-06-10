// engine/src/hft3_engine_main.cpp
//
// hft3_engine — fused hot loop binary (CHI404_RUNTIME.md §1).
//
// Usage:
//   hft3_engine --mode REPLAY --config <path> --weights <path> --feed <path> [--log <path>]
//   hft3_engine --mode LIVE   --config <path> --weights <path> --log <path>
//
// Startup sequence (§8):
//   Step 1  parse config + CLI  [this file]
//   Step 2  validate LIVE_* contract [assert_live_config]
//   Step 3  load weights binary [engine.load_weights]
//   Step 4  verify certification stamp [REPLAY: skipped; LIVE: future]
//   Step 5  init RiskManager limits [EngineConfig::to_risk_limits() inside EngineLoop ctor]
//   Step 6  pre-allocate queues / log ring [EngineLoop ctor; DrainThread::start]
//   Step 7  pin threads [LIVE-only; skipped in REPLAY — live-only comment below]
//   Step 8  connect Rithmic [LIVE-only; skipped in REPLAY]
//   Step 9  book snapshot [LIVE-only; skipped in REPLAY]
//   Step 10 feature warm-up [handled inside EngineLoop::run via warmup_count_]
//   Step 11 enter run mode [engine.run()]
//
// Exit codes (§9.7):
//   0 = clean shutdown
//   1 = operator halt (kill-switch)
//   2 = hard halt (restart required)
//   3 = startup validation failure

#include <chrono>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <string>

#include "engine_config.hpp"
#include "engine_loop.hpp"
#include "log_ring.hpp"
#include "replay_adapter.hpp"
#include "spsc_queue.hpp"
#include "rithmic_adapter.hpp"

// Forward declaration of the DrainThread type used below.
// Defined in log_ring.hpp as DrainThread<Capacity>.

static void print_usage(const char* argv0) {
    std::fprintf(stderr,
        "Usage: %s --mode REPLAY --config <path> --weights <path> --feed <path> [--log <path>]\n"
        "       %s --mode LIVE   --config <path> --weights <path> [--log <path>]\n",
        argv0, argv0);
}

int main(int argc, char** argv) {
    // --- Step 1: Parse CLI ---
    std::string mode_str;
    std::string config_path;
    std::string weights_path;
    std::string feed_path;
    std::string log_path = "hft3_engine.log";

    for (int i = 1; i < argc; ++i) {
        if      (std::strcmp(argv[i], "--mode")    == 0 && i+1 < argc) mode_str     = argv[++i];
        else if (std::strcmp(argv[i], "--config")  == 0 && i+1 < argc) config_path  = argv[++i];
        else if (std::strcmp(argv[i], "--weights") == 0 && i+1 < argc) weights_path = argv[++i];
        else if (std::strcmp(argv[i], "--feed")    == 0 && i+1 < argc) feed_path    = argv[++i];
        else if (std::strcmp(argv[i], "--log")     == 0 && i+1 < argc) log_path     = argv[++i];
    }

    // Load config from file (CLI overrides applied after).
    hft::engine::EngineConfig cfg;
    if (!config_path.empty()) {
        std::string err;
        if (!hft::engine::load_config(config_path, cfg, err)) {
            std::fprintf(stderr, "[hft3_engine] Config parse error: %s\n", err.c_str());
            return 3;
        }
    }

    // CLI overrides.
    if (!mode_str.empty()) {
        if      (mode_str == "REPLAY") cfg.mode = hft::engine::EngineMode::REPLAY;
        else if (mode_str == "PAPER")  cfg.mode = hft::engine::EngineMode::PAPER;
        else if (mode_str == "LIVE")   cfg.mode = hft::engine::EngineMode::LIVE;
        else {
            std::fprintf(stderr, "[hft3_engine] Unknown mode: %s\n", mode_str.c_str());
            return 3;
        }
    }
    if (!weights_path.empty()) cfg.weights_path = weights_path;
    if (!feed_path.empty())    cfg.feed_path    = feed_path;

    // --- Step 2: Validate LIVE_* contract ---
    {
        std::string err;
        if (!hft::engine::assert_live_config(cfg, err)) {
            std::fprintf(stderr, "[hft3_engine] LIVE config validation failed: %s\n", err.c_str());
            return 3;
        }
    }

    if (cfg.weights_path.empty()) {
        std::fprintf(stderr, "[hft3_engine] --weights path required\n");
        print_usage(argv[0]);
        return 3;
    }

    if (cfg.mode == hft::engine::EngineMode::REPLAY && cfg.feed_path.empty()) {
        std::fprintf(stderr, "[hft3_engine] --feed path required in REPLAY mode\n");
        print_usage(argv[0]);
        return 3;
    }

    if (cfg.mode == hft::engine::EngineMode::REPLAY) {
        // ===================================================================
        // REPLAY path
        // ===================================================================
        using Adapter = hft::engine::ReplayAdapter;
        using Loop    = hft::engine::EngineLoop<Adapter>;

        // Steps 5-6: EngineLoop ctor initialises RiskManager + preallocates.
        // Queue wiring: create adapter with null queues first, construct EngineLoop
        // (which allocates queues), then rebind adapter to those queues.
        hft::engine::ReplayAdapter adapter(
            nullptr, nullptr,  // queues wired below via rebind_queues
            cfg.feed_path,
            cfg.latency_ns);

        Loop engine(adapter, cfg);

        // Rebind adapter to the engine's owned queues (§8 step 6 analog).
        adapter.rebind_queues(&engine.mbo_queue(), &engine.order_queue());

        // --- Step 3: Load weights ---
        if (!engine.load_weights(cfg.weights_path)) {
            std::fprintf(stderr, "[hft3_engine] Failed to load weights: %s\n",
                         cfg.weights_path.c_str());
            return 3;
        }

        // --- Step 4: Certification stamp (REPLAY: skipped; LIVE: required) ---
        // LIVE-only: re-read stamp JSON from bundle; assert promotion_eligible==true etc.
        // (CHI404_RUNTIME.md §8 step 4 — skipped in REPLAY per §10 table)

        // --- Step 6: Start log drain thread ---
        hft::engine::DrainThread<Loop::LOG_RING_CAP> drain(engine.log_ring(), log_path);
        if (!drain.start()) {
            std::fprintf(stderr, "[hft3_engine] Failed to start drain thread: %s\n",
                         log_path.c_str());
            return 3;
        }

        // --- Step 7: Thread-pin (LIVE-only; skipped in REPLAY) ---
        // LIVE: pin hot thread → core 2; log-drain → core 3; sched_getaffinity verify.
        // (CHI404_RUNTIME.md §8 step 7 — not executed in REPLAY)

        // --- Step 8: Connect Rithmic (LIVE-only; skipped in REPLAY) ---
        // LIVE: RithmicAdapter.initialize() + connect(); abort on failure.
        // (CHI404_RUNTIME.md §8 step 8 — not executed in REPLAY)

        // --- Step 9: Book snapshot (LIVE-only; skipped in REPLAY) ---
        // LIVE: subscribe_mbo(); warm_price_increment(); receive full book snapshot;
        //       assert has_account() && has_trade_route().
        // REPLAY: feed is pre-loaded below.
        // (CHI404_RUNTIME.md §8 step 9 — live-only)

        // Load MBO feed records into adapter's buffer (REPLAY substitute for live ingestion).
        // Records are streamed into mbo_queue_ incrementally during run() via refill_queue().
        const int64_t loaded = adapter.load_feed();
        if (loaded < 0) {
            std::fprintf(stderr, "[hft3_engine] Failed to load feed: %s\n",
                         cfg.feed_path.c_str());
            drain.stop();
            return 3;
        }
        std::fprintf(stdout, "[hft3_engine] REPLAY: loaded %lld events from %s\n",
                     static_cast<long long>(loaded), cfg.feed_path.c_str());

        // --- Steps 10-11: Feature warm-up + run mode (handled inside run()) ---
        const auto t0 = std::chrono::steady_clock::now();
        engine.run(/*replay_finite=*/true);
        const auto t1 = std::chrono::steady_clock::now();

        const double elapsed_s =
            std::chrono::duration<double>(t1 - t0).count();
        const double throughput =
            (elapsed_s > 0.0) ? static_cast<double>(loaded) / elapsed_s : 0.0;

        std::fprintf(stdout,
            "[hft3_engine] REPLAY done: %lld events in %.3f s = %.0f events/s; "
            "submits=%lld blocks=%lld\n",
            static_cast<long long>(loaded),
            elapsed_s,
            throughput,
            static_cast<long long>(engine.submit_count()),
            static_cast<long long>(engine.block_count()));

        drain.stop();
        return 0;

    } else {
        // ===================================================================
        // LIVE / PAPER path — compile-guarded stub (§10)
        // ===================================================================
        // Live instantiation of EngineLoop<RithmicAdapter> is OUT OF SCOPE on this
        // workstation; the Rithmic SDK is not present here.  The binary compiles
        // conceptually; actual wiring happens on CHI404.
        //
        // To instantiate:
        //   #ifdef HFT3_LIVE_BUILD
        //   using LiveLoop = hft::engine::EngineLoop<hft::RithmicAdapter>;
        //   ... [follow §8 steps 7-9] ...
        //   #endif
        std::fprintf(stderr,
            "[hft3_engine] LIVE/PAPER mode requires CHI404 build with Rithmic SDK.\n"
            "This workstation binary supports REPLAY only.\n");
        return 3;
    }
}
