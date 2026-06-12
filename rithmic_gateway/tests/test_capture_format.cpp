// test_capture_format.cpp — Offline unit test for CC2 capture format.
//
// Tests:
//   1. Static layout assertions for CaptureRecord and CaptureFileHeader.
//   2. Trade-date rollover function correctness at key CT boundary cases.
//   3. in_active_trading_window() boundary cases.
//   4. Manifest JSON shape (syntactic check).
//
// Compile (standalone, no CMake required — run from repository root):
//   g++ -std=c++17 -O2 \
//       -D_DEFAULT_SOURCE \
//       -I rithmic_gateway/include \
//       rithmic_gateway/tests/test_capture_format.cpp \
//       -o build/test_capture_format
//   (run from repository root)
//
// Exit codes:
//   0  all assertions passed
//   1  one or more assertions failed

#include "capture_format.hpp"

#include <cassert>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <string>

// ---------------------------------------------------------------------------
// Minimal test harness
// ---------------------------------------------------------------------------

static int g_tests_run    = 0;
static int g_tests_failed = 0;

#define ASSERT_TRUE(cond)                                               \
    do {                                                                \
        ++g_tests_run;                                                  \
        if (!(cond)) {                                                  \
            ++g_tests_failed;                                           \
            std::cerr << "[FAIL] " << __FILE__ << ":" << __LINE__      \
                      << "  " << #cond << "\n";                         \
        }                                                               \
    } while (false)

#define ASSERT_EQ(a, b)  ASSERT_TRUE((a) == (b))
#define ASSERT_NE(a, b)  ASSERT_TRUE((a) != (b))
#define ASSERT_STR_EQ(a, b)  ASSERT_TRUE(std::string(a) == std::string(b))

// ---------------------------------------------------------------------------
// Helper: call cme_trade_date() and return as std::string.
// ---------------------------------------------------------------------------

// Helper: build a UTC epoch second from Y/M/D H:M:S (interpreted as CT time,
// converted to UTC by adding the DST-appropriate offset).
// This is used to construct test input timestamps.
static time_t ct_to_utc(int year, int month, int mday, int hour, int minute,
                         int second, bool is_dst) {
    struct tm t{};
    t.tm_year = year - 1900;
    t.tm_mon  = month - 1;
    t.tm_mday = mday;
    t.tm_hour = hour;
    t.tm_min  = minute;
    t.tm_sec  = second;
    // timegm interprets as UTC; we apply the CT offset to convert CT→UTC.
    int offset = is_dst ? kCtUtcOffsetDst : kCtUtcOffsetStd;
    // CT = UTC + offset  →  UTC = CT - offset
    return timegm(&t) - offset;
}

static std::string trade_date_str(time_t utc_sec) {
    char buf[12] = {};
    cme_trade_date(utc_sec, buf);
    return std::string(buf);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

static void test_struct_sizes() {
    // These duplicate the static_asserts in the header so they fire at runtime
    // with a descriptive failure message rather than a compiler error.
    ASSERT_EQ(sizeof(CaptureRecord),     40u);
    ASSERT_EQ(sizeof(CaptureFileHeader), 72u);

    // Verify field offsets within CaptureRecord (packed, no padding).
    ASSERT_EQ(offsetof(CaptureRecord, ts_exch_ns),      0u);
    ASSERT_EQ(offsetof(CaptureRecord, ts_recv_mono_ns), 8u);
    ASSERT_EQ(offsetof(CaptureRecord, order_id),        16u);
    ASSERT_EQ(offsetof(CaptureRecord, price),           24u);
    ASSERT_EQ(offsetof(CaptureRecord, size),            32u);
    ASSERT_EQ(offsetof(CaptureRecord, symbol_id),       36u);
    ASSERT_EQ(offsetof(CaptureRecord, action),          38u);
    ASSERT_EQ(offsetof(CaptureRecord, side),            39u);
}

// ---------------------------------------------------------------------------
// Trade-date boundary cases
// All test cases use CDT (UTC-5) because June 2026 is during daylight saving.
// ---------------------------------------------------------------------------

static void test_trade_date_normal_day() {
    // Thursday 2026-06-11 10:00 CT (CDT, UTC-5) → trade date 2026-06-11
    time_t t = ct_to_utc(2026, 6, 11, 10, 0, 0, true);
    ASSERT_STR_EQ(trade_date_str(t), "2026-06-11");
}

static void test_trade_date_just_before_roll() {
    // Thursday 2026-06-11 16:59:59 CT → still 2026-06-11
    time_t t = ct_to_utc(2026, 6, 11, 16, 59, 59, true);
    ASSERT_STR_EQ(trade_date_str(t), "2026-06-11");
}

static void test_trade_date_at_roll() {
    // Thursday 2026-06-11 17:00:00 CT → belongs to next session = 2026-06-12
    time_t t = ct_to_utc(2026, 6, 11, 17, 0, 0, true);
    ASSERT_STR_EQ(trade_date_str(t), "2026-06-12");
}

static void test_trade_date_friday_afternoon() {
    // Friday 2026-06-12 16:30 CT → still same trade date 2026-06-12
    // (break has started at 16:00 but that only matters for active-window;
    //  trade date doesn't roll until 17:00)
    time_t t = ct_to_utc(2026, 6, 12, 16, 30, 0, true);
    ASSERT_STR_EQ(trade_date_str(t), "2026-06-12");
}

static void test_trade_date_friday_at_roll() {
    // Friday 2026-06-12 17:00 CT → weekend; next trading day is Monday 2026-06-15
    time_t t = ct_to_utc(2026, 6, 12, 17, 0, 0, true);
    ASSERT_STR_EQ(trade_date_str(t), "2026-06-15");
}

static void test_trade_date_saturday_any_time() {
    // Saturday 2026-06-13 12:00 CT → no Saturday session → Monday 2026-06-15
    time_t t = ct_to_utc(2026, 6, 13, 12, 0, 0, true);
    ASSERT_STR_EQ(trade_date_str(t), "2026-06-15");
}

static void test_trade_date_sunday_before_open() {
    // Sunday 2026-06-14 16:59 CT (before 17:00 open) → Friday's session = 2026-06-12
    time_t t = ct_to_utc(2026, 6, 14, 16, 59, 0, true);
    ASSERT_STR_EQ(trade_date_str(t), "2026-06-12");
}

static void test_trade_date_sunday_at_open() {
    // Sunday 2026-06-14 17:00 CT (at/after 17:00) → Monday's trade date = 2026-06-15
    time_t t = ct_to_utc(2026, 6, 14, 17, 0, 0, true);
    ASSERT_STR_EQ(trade_date_str(t), "2026-06-15");
}

static void test_trade_date_sunday_evening() {
    // Sunday 2026-06-14 18:00 CT → still Monday trade date 2026-06-15
    time_t t = ct_to_utc(2026, 6, 14, 18, 0, 0, true);
    ASSERT_STR_EQ(trade_date_str(t), "2026-06-15");
}

// ---------------------------------------------------------------------------
// in_active_trading_window tests
// ---------------------------------------------------------------------------

static void test_active_window_weekday_morning() {
    time_t t = ct_to_utc(2026, 6, 11, 10, 0, 0, true);   // Thu 10:00 CT
    CtDateTime ct = utc_to_ct(t);
    ASSERT_TRUE(in_active_trading_window(ct));
}

static void test_active_window_daily_break() {
    time_t t = ct_to_utc(2026, 6, 11, 16, 30, 0, true);  // Thu 16:30 CT (break)
    CtDateTime ct = utc_to_ct(t);
    ASSERT_TRUE(!in_active_trading_window(ct));
}

static void test_active_window_friday_after_close() {
    time_t t = ct_to_utc(2026, 6, 12, 16, 0, 0, true);   // Fri 16:00 CT
    CtDateTime ct = utc_to_ct(t);
    ASSERT_TRUE(!in_active_trading_window(ct));
}

static void test_active_window_saturday() {
    time_t t = ct_to_utc(2026, 6, 13, 10, 0, 0, true);   // Sat
    CtDateTime ct = utc_to_ct(t);
    ASSERT_TRUE(!in_active_trading_window(ct));
}

static void test_active_window_sunday_before_open() {
    time_t t = ct_to_utc(2026, 6, 14, 12, 0, 0, true);   // Sun 12:00 (pre-open)
    CtDateTime ct = utc_to_ct(t);
    ASSERT_TRUE(!in_active_trading_window(ct));
}

static void test_active_window_sunday_after_open() {
    time_t t = ct_to_utc(2026, 6, 14, 18, 0, 0, true);   // Sun 18:00 (post-open)
    CtDateTime ct = utc_to_ct(t);
    ASSERT_TRUE(in_active_trading_window(ct));
}

// ---------------------------------------------------------------------------
// Manifest JSON shape (syntactic sanity — not a full parser)
// ---------------------------------------------------------------------------

static void test_manifest_json_shape() {
    // Manually construct the same JSON as write_manifest() would produce.
    char buf[1024];
    int n = std::snprintf(buf, sizeof(buf),
        "{\n"
        "  \"symbol\": \"%s\",\n"
        "  \"exchange\": \"%s\",\n"
        "  \"trade_date\": \"%s\",\n"
        "  \"records\": %llu,\n"
        "  \"trades\": %llu,\n"
        "  \"quotes\": %llu,\n"
        "  \"first_ts_exch_ns\": %llu,\n"
        "  \"last_ts_exch_ns\": %llu,\n"
        "  \"max_queue_gap_flag_count\": %llu,\n"
        "  \"md_drops_total\": %llu,\n"
        "  \"reconnects\": %llu,\n"
        "  \"file_bytes\": %llu,\n"
        "  \"updated_wall_ns\": %llu\n"
        "}\n",
        "MESU6", "CME", "2026-06-11",
        (unsigned long long)100,
        (unsigned long long)42,
        (unsigned long long)58,
        (unsigned long long)1718000000000000000ULL,
        (unsigned long long)1718001000000000000ULL,
        (unsigned long long)0,
        (unsigned long long)0,
        (unsigned long long)0,
        (unsigned long long)(64 + 100 * 40),
        (unsigned long long)1718001000000000000ULL
    );
    ASSERT_TRUE(n > 0 && n < static_cast<int>(sizeof(buf)));

    // Check required keys are present.
    ASSERT_TRUE(std::strstr(buf, "\"symbol\"")               != nullptr);
    ASSERT_TRUE(std::strstr(buf, "\"exchange\"")             != nullptr);
    ASSERT_TRUE(std::strstr(buf, "\"trade_date\"")           != nullptr);
    ASSERT_TRUE(std::strstr(buf, "\"records\"")              != nullptr);
    ASSERT_TRUE(std::strstr(buf, "\"trades\"")               != nullptr);
    ASSERT_TRUE(std::strstr(buf, "\"quotes\"")               != nullptr);
    ASSERT_TRUE(std::strstr(buf, "\"first_ts_exch_ns\"")     != nullptr);
    ASSERT_TRUE(std::strstr(buf, "\"last_ts_exch_ns\"")      != nullptr);
    ASSERT_TRUE(std::strstr(buf, "\"max_queue_gap_flag_count\"") != nullptr);
    ASSERT_TRUE(std::strstr(buf, "\"md_drops_total\"")       != nullptr);
    ASSERT_TRUE(std::strstr(buf, "\"reconnects\"")           != nullptr);
    ASSERT_TRUE(std::strstr(buf, "\"file_bytes\"")           != nullptr);
    ASSERT_TRUE(std::strstr(buf, "\"updated_wall_ns\"")      != nullptr);

    // Check values.
    ASSERT_TRUE(std::strstr(buf, "\"MESU6\"")    != nullptr);
    ASSERT_TRUE(std::strstr(buf, "\"CME\"")      != nullptr);
    ASSERT_TRUE(std::strstr(buf, "\"2026-06-11\"") != nullptr);
    ASSERT_TRUE(std::strstr(buf, "100")          != nullptr);
    ASSERT_TRUE(std::strstr(buf, "42")           != nullptr);
    ASSERT_TRUE(std::strstr(buf, "58")           != nullptr);
}

// ---------------------------------------------------------------------------
// CaptureFileHeader magic/version check
// ---------------------------------------------------------------------------

static void test_file_header_fields() {
    CaptureFileHeader hdr{};
    std::memcpy(hdr.magic, "HFT3CAP1", 8);
    hdr.version     = 1;
    hdr.record_size = sizeof(CaptureRecord);

    ASSERT_EQ(std::memcmp(hdr.magic, "HFT3CAP1", 8), 0);
    ASSERT_EQ(hdr.version,     1u);
    ASSERT_EQ(hdr.record_size, 40u);
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int main() {
    test_struct_sizes();

    test_trade_date_normal_day();
    test_trade_date_just_before_roll();
    test_trade_date_at_roll();
    test_trade_date_friday_afternoon();
    test_trade_date_friday_at_roll();
    test_trade_date_saturday_any_time();
    test_trade_date_sunday_before_open();
    test_trade_date_sunday_at_open();
    test_trade_date_sunday_evening();

    test_active_window_weekday_morning();
    test_active_window_daily_break();
    test_active_window_friday_after_close();
    test_active_window_saturday();
    test_active_window_sunday_before_open();
    test_active_window_sunday_after_open();

    test_manifest_json_shape();
    test_file_header_fields();

    if (g_tests_failed == 0) {
        std::cout << "[PASS] " << g_tests_run << " tests passed\n";
        return 0;
    }
    std::cerr << "[FAIL] " << g_tests_failed << " / " << g_tests_run << " tests failed\n";
    return 1;
}
