#pragma once

// capture_format.hpp — Binary capture file format for CC2 market-data daemon.
//
// Shared between:
//   rithmic_gateway/tools/capture_daemon.cpp   (writer)
//   rithmic_gateway/tests/test_capture_format.cpp  (offline verifier)
//
// ENDIANNESS: all multi-byte values are written little-endian (native on x86-64
// colo boxes).  Consumers on the same architecture read them directly.
//
// FILE LAYOUT:
//   [CaptureFileHeader]  (fixed size, one per file)
//   [CaptureRecord, ...]  (zero or more, fixed size)
//
// TRADE-DATE CONVENTION:
//   CME Group rolls trade date at 17:00 America/Chicago ("CT").
//   An event arriving after 17:00 CT on day D belongs to trade date D+1
//   (the next business day, skipping weekends: Sat → Mon, Sun open → Mon).
//
//   This is implemented with UTC offset arithmetic rather than calling
//   setenv("TZ",...) — setenv is not thread-safe and can race with other
//   threads that call localtime().  Instead we call gmtime_r, apply a fixed
//   CT offset (UTC-6 standard / UTC-5 daylight), and derive day-of-week and
//   time-of-day in CT.  The caller is responsible for passing the correct
//   UTC offset; the daemon samples it at startup and at each daily rotation.
//
// RECORD SIZE: 40 bytes (static_assert below).

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>

#pragma pack(push, 1)

// File header — written once at the start of each per-symbol session file.
// Total size (packed): 8+4+4+8+8+16+8+12+4 = 72 bytes.
struct CaptureFileHeader {
    char     magic[8];          // "HFT3CAP1" (no null terminator)
    uint32_t version;           // 1
    uint32_t record_size;       // sizeof(CaptureRecord) == 40
    uint64_t wall_ns_at_open;   // wall-clock CLOCK_REALTIME nanoseconds at file open
    uint64_t mono_ns_at_open;   // CLOCK_MONOTONIC nanoseconds at file open
    char     symbol[16];        // null-padded symbol name (e.g. "MESU6")
    char     exchange[8];       // null-padded exchange name (e.g. "CME")
    char     trade_date[12];    // "YYYY-MM-DD\0\0" (null-padded to 12 bytes)
    char     reserved[4];       // zero-filled; reserved for future use (flags, etc.)
    // 8+4+4+8+8+16+8+12+4 = 72
};

// Per-event record — one appended for every MarketDataEvent drained from the SPSC queue.
struct CaptureRecord {
    uint64_t ts_exch_ns;       // exchange timestamp (MarketDataEvent::timestamp_ns)
    uint64_t ts_recv_mono_ns;  // receive monotonic time (MarketDataEvent::callback_monotonic_ns)
    uint64_t order_id;         // MarketDataEvent::order_id (0 for BBO quotes)
    double   price;            // MarketDataEvent::price
    int32_t  size;             // MarketDataEvent::size
    uint16_t symbol_id;        // MarketDataEvent::symbol_id
    char     action;           // MarketDataEvent::action ('T' trade, 'M' BBO, 'A'/'C' depth)
    char     side;             // MarketDataEvent::side ('B' bid, 'A' ask, ' ' unknown)
};

#pragma pack(pop)

static_assert(sizeof(CaptureRecord) == 40,
    "CaptureRecord must be 40 bytes — update format version if you change fields");
static_assert(sizeof(CaptureFileHeader) == 72,
    "CaptureFileHeader size changed — update format version");

// ---------------------------------------------------------------------------
// Trade-date derivation
// ---------------------------------------------------------------------------

// CT UTC offsets (seconds): standard time is UTC-6, daylight (CDT) is UTC-5.
static constexpr int kCtUtcOffsetStd = -6 * 3600;   // CST
static constexpr int kCtUtcOffsetDst = -5 * 3600;   // CDT

// US DST rules (post-2007 Energy Policy Act):
//   Starts: 2nd Sunday of March at 02:00 local
//   Ends:   1st Sunday of November at 02:00 local
// We approximate with a simple calendar check: we test whether wall_utc_sec
// falls between Mar 2nd-Sun/02:00 UTC-6 and Nov 1st-Sun/02:00 UTC-5.
// For a 24/7 trading system this approximation is correct every year through
// 2099 unless Congress changes the rule.
//
// Returns the CT UTC offset in seconds for the given UTC epoch second.
static int ct_utc_offset(time_t utc_sec) {
    // Find the year of this timestamp.
    struct tm gm{};
#if defined(_WIN32)
    gmtime_s(&gm, &utc_sec);
#else
    gmtime_r(&utc_sec, &gm);
#endif
    int year = gm.tm_year + 1900;

    // Compute the start of DST: 2nd Sunday of March at 02:00 CST (= 08:00 UTC).
    // Start with March 1 of the year, then advance to first Sunday, then +7 days.
    struct tm dst_start{};
    dst_start.tm_year = year - 1900;
    dst_start.tm_mon  = 2;   // March (0-indexed)
    dst_start.tm_mday = 1;
    dst_start.tm_hour = 8;   // 02:00 CST = 08:00 UTC
    dst_start.tm_isdst = -1;
    time_t dst_start_utc = timegm(&dst_start);
    {
        // Advance to first Sunday.
        struct tm tmp{};
#if defined(_WIN32)
        gmtime_s(&tmp, &dst_start_utc);
#else
        gmtime_r(&dst_start_utc, &tmp);
#endif
        int wday = tmp.tm_wday;          // 0=Sun
        int days_to_sun = (wday == 0) ? 0 : (7 - wday);
        dst_start_utc += days_to_sun * 86400;
        dst_start_utc += 7 * 86400;     // second Sunday
    }

    // Compute the end of DST: 1st Sunday of November at 02:00 CDT (= 07:00 UTC).
    struct tm dst_end{};
    dst_end.tm_year = year - 1900;
    dst_end.tm_mon  = 10;   // November
    dst_end.tm_mday = 1;
    dst_end.tm_hour = 7;    // 02:00 CDT = 07:00 UTC
    dst_end.tm_isdst = -1;
    time_t dst_end_utc = timegm(&dst_end);
    {
        struct tm tmp{};
#if defined(_WIN32)
        gmtime_s(&tmp, &dst_end_utc);
#else
        gmtime_r(&dst_end_utc, &tmp);
#endif
        int wday = tmp.tm_wday;
        int days_to_sun = (wday == 0) ? 0 : (7 - wday);
        dst_end_utc += days_to_sun * 86400;
    }

    if (utc_sec >= dst_start_utc && utc_sec < dst_end_utc) {
        return kCtUtcOffsetDst;   // CDT: UTC-5
    }
    return kCtUtcOffsetStd;       // CST: UTC-6
}

// CtDateTime — broken-down time in America/Chicago.
struct CtDateTime {
    int year;     // e.g. 2025
    int month;    // 1-12
    int mday;     // 1-31
    int hour;     // 0-23
    int minute;   // 0-59
    int second;   // 0-59
    int wday;     // 0=Sun, 1=Mon, ..., 6=Sat
};

// Convert a UTC epoch second to a broken-down CT time.
static CtDateTime utc_to_ct(time_t utc_sec) {
    int offset = ct_utc_offset(utc_sec);
    time_t ct_sec = utc_sec + offset;
    struct tm t{};
#if defined(_WIN32)
    gmtime_s(&t, &ct_sec);
#else
    gmtime_r(&ct_sec, &t);
#endif
    CtDateTime r;
    r.year   = t.tm_year + 1900;
    r.month  = t.tm_mon + 1;
    r.mday   = t.tm_mday;
    r.hour   = t.tm_hour;
    r.minute = t.tm_min;
    r.second = t.tm_sec;
    r.wday   = t.tm_wday;
    return r;
}

// Derive the CME trade date (YYYY-MM-DD) for a given UTC epoch second.
//
// Rules:
//   1. Convert UTC → CT.
//   2. If CT time >= 17:00, the event belongs to the NEXT trading day.
//   3. "Next trading day" skips weekends:
//        - Friday 17:00 CT onward  → Monday trade date
//        - Saturday (any time)     → Monday trade date (Sat session opens
//                                    Sunday evening; we attribute Sat events
//                                    to the next Mon because Sat is not a
//                                    trading day)
//        - Sunday before 17:00 CT  → Friday trade date (still Friday session)
//          [NOTE: the Sunday evening session that opens at 17:00 CT belongs
//           to Monday, handled by rule 2.]
//        - Otherwise, next calendar day, skipping Sat/Sun.
//
// Calendar Sunday note: the Chicago CME Globex week opens Sunday 17:00 CT.
// Events received Sunday before 17:00 carry Friday's trade date (the last
// session that was active).  Events Sunday >= 17:00 carry Monday's trade date.
//
// Fills out_date with "YYYY-MM-DD" (11 chars + null).
static void cme_trade_date(time_t utc_sec, char out_date[12]) {
    CtDateTime ct = utc_to_ct(utc_sec);

    // Check whether we need to advance to the next trading session.
    bool advance = (ct.hour >= 17);

    if (!advance) {
        // Before 17:00 CT: the trade date is today unless today is Saturday.
        // Saturday at any time belongs to Monday (no Saturday session).
        if (ct.wday == 6 /* Sat */) {
            advance = true;  // will land on Sunday, then skip to Mon below
        } else if (ct.wday == 0 /* Sun */ && ct.hour < 17) {
            // Sunday before 17:00: Friday's session is still "active" from a
            // trade-date perspective.  Walk back to Friday.
            time_t fri = utc_sec - 2 * 86400;
            CtDateTime fri_ct = utc_to_ct(fri);
            // Make sure we landed on Friday (wday==5).
            while (fri_ct.wday != 5) {
                fri -= 86400;
                fri_ct = utc_to_ct(fri);
            }
            std::snprintf(out_date, 12, "%04d-%02d-%02d",
                          fri_ct.year, fri_ct.month, fri_ct.mday);
            return;
        }
    }

    if (!advance) {
        std::snprintf(out_date, 12, "%04d-%02d-%02d",
                      ct.year, ct.month, ct.mday);
        return;
    }

    // Advance one calendar day and then skip weekends.
    // Use UTC arithmetic to avoid DST ambiguity when crossing midnight.
    time_t next = utc_sec + 86400;
    CtDateTime nct = utc_to_ct(next);
    // Skip Saturday and Sunday (no trading sessions start on those days).
    while (nct.wday == 0 || nct.wday == 6) {
        next += 86400;
        nct = utc_to_ct(next);
    }
    std::snprintf(out_date, 12, "%04d-%02d-%02d",
                  nct.year, nct.month, nct.mday);
}

// Check whether a CT hour/minute falls inside the CME daily maintenance break
// (16:00–17:00 CT, i.e. 16:00 <= time < 17:00).
static inline bool in_daily_break_ct(int ct_hour, int ct_minute) {
    (void)ct_minute;
    return ct_hour == 16;   // 16:00:00 – 16:59:59 CT
}

// Check whether the current CT time is inside an expected-active trading window:
//   Sun 17:00 CT through Fri 16:00 CT, excluding the daily 16:00–17:00 break.
static bool in_active_trading_window(const CtDateTime& ct) {
    // Fri >= 16:00 → inactive (break starts + weekend approach)
    if (ct.wday == 5 && ct.hour >= 16) return false;
    // Sat → always inactive
    if (ct.wday == 6) return false;
    // Sun before 17:00 → inactive (pre-open)
    if (ct.wday == 0 && ct.hour < 17) return false;
    // Daily maintenance break 16:00–16:59
    if (in_daily_break_ct(ct.hour, ct.minute)) return false;
    return true;
}
