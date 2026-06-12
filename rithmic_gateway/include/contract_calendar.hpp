#pragma once

// contract_calendar.hpp — CME front-month contract calendar for CC2 roll logic.
//
// Provides:
//   - RollCycle enum (QuarterlyEquity, MonthlyEnergy, Gold)
//   - third_friday(year, month) — day-of-month of the third Friday
//   - biz_days_before(year, month, day, n) — step back n business days
//     (weekend-only; CME holidays are deliberately ignored because the
//      two-contract overlap window absorbs the ±1-day error)
//   - eligible_contracts(root, cycle, ct_year, ct_month, ct_day, count)
//     — returns the first `count` eligible contract codes in chronological
//       order, formatted as ROOT + month-code + last-digit-of-year
//       (e.g. "MESU6").  Searches forward up to 24 delivery months.
//
// All functions are pure (no statics, no time() calls).  The caller is
// responsible for obtaining the current CT calendar date and passing it in.
//
// Compile (standalone, no CMake required):
//   g++ -std=c++17 -O2 -D_DEFAULT_SOURCE \
//       -I rithmic_gateway/include \
//       rithmic_gateway/tests/test_contract_calendar.cpp \
//       -o build/test_contract_calendar
//   ./build/test_contract_calendar

#include <cstdint>
#include <string>
#include <vector>

// ---------------------------------------------------------------------------
// Month codes — CME convention: F G H J K M N Q U V X Z  (Jan .. Dec)
// ---------------------------------------------------------------------------

static constexpr char kMonthCodes[14] = " FGHJKMNQUVXZ";  // index 1..12 (+ null)

inline char month_code(int month) {
    return (month >= 1 && month <= 12) ? kMonthCodes[month] : '?';
}

// ---------------------------------------------------------------------------
// Roll cycles
// ---------------------------------------------------------------------------

enum class RollCycle {
    QuarterlyEquity,   // Mar/Jun/Sep/Dec delivery (e.g. ES, MES)
    MonthlyEnergy,     // Every month is a delivery month (e.g. CL, MCL)
    Gold               // Feb/Apr/Jun/Aug/Oct/Dec delivery (e.g. GC, MGC)
};

// ---------------------------------------------------------------------------
// Calendar helpers
// ---------------------------------------------------------------------------

// Returns true if the given year is a Gregorian leap year.
inline bool is_leap_year(int year) {
    return (year % 4 == 0 && year % 100 != 0) || (year % 400 == 0);
}

// Days in a calendar month (1-indexed).
inline int days_in_month(int year, int month) {
    static const int kDays[13] = { 0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31 };
    if (month == 2 && is_leap_year(year)) return 29;
    return kDays[month];
}

// Compute the day-of-week for the first day of the given month.
// Returns 0=Sun, 1=Mon, ..., 6=Sat using Tomohiko Sakamoto's algorithm.
inline int first_day_of_month_wday(int year, int month) {
    static const int t[] = { 0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4 };
    if (month < 3) --year;
    return (year + year / 4 - year / 100 + year / 400 + t[month - 1] + 1) % 7;
}

// Returns the day-of-month (1-based) of the third Friday of the given month.
//
// The third Friday is the standard expiration anchor for CME equity index
// options and futures.  It is used by QuarterlyEquity roll logic to derive
// the last-eligible capture date.
inline int third_friday(int year, int month) {
    // Find the weekday of the 1st of the month, then step forward to the
    // first Friday (wday == 5), then add 14 days for the third occurrence.
    int wday1 = first_day_of_month_wday(year, month);
    // Days until first Friday from day 1.
    int days_to_fri = (5 - wday1 + 7) % 7;  // 0 if 1st is already Friday
    int first_fri = 1 + days_to_fri;
    return first_fri + 14;  // third Friday
}

// Step backward `n` business days from (year, month, day), skipping only
// Saturdays (wday=6) and Sundays (wday=0).  CME exchange holidays are
// deliberately ignored — the two-contract overlap window absorbs the ±1-day
// error introduced on the handful of holiday edge cases per year.
//
// Fills *out_year, *out_month, *out_day with the result.
inline void biz_days_before(int year, int month, int day, int n,
                             int* out_year, int* out_month, int* out_day) {
    // Walk backwards one calendar day at a time, decrementing the business-day
    // counter only on Mon-Fri.
    while (n > 0) {
        // Step back one calendar day.
        --day;
        if (day < 1) {
            --month;
            if (month < 1) {
                month = 12;
                --year;
            }
            day = days_in_month(year, month);
        }
        // Determine day-of-week for (year, month, day).
        // Tomohiko Sakamoto, inline to avoid external dependencies.
        static const int t[] = { 0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4 };
        int y = year;
        int m = month;
        if (m < 3) --y;
        int wday = (y + y / 4 - y / 100 + y / 400 + t[m - 1] + day) % 7;
        if (wday != 0 && wday != 6) {  // Mon–Fri
            --n;
        }
    }
    *out_year  = year;
    *out_month = month;
    *out_day   = day;
}

// ---------------------------------------------------------------------------
// Eligibility predicates
// Evaluated against a CT calendar date (ct_year, ct_month, ct_day).
// ---------------------------------------------------------------------------

// QuarterlyEquity: delivery months Mar/Jun/Sep/Dec.
// Contract is eligible while ct_date < (third_friday(delivery) - 9 calendar days).
//
// Example (spec): for June delivery, third Friday Jun 19 → cutoff Jun 10.
// A position on Jun 10 still observes Jun 10 < Jun 10 → false (rolled already).
// On Jun 9: Jun 9 < Jun 10 → eligible.
inline bool quarterly_equity_eligible(int ct_year, int ct_month, int ct_day,
                                      int del_year, int del_month) {
    // Delivery months only.
    if (del_month != 3 && del_month != 6 && del_month != 9 && del_month != 12) {
        return false;
    }
    int tf = third_friday(del_year, del_month);
    int cutoff_day   = tf - 9;
    int cutoff_month = del_month;
    int cutoff_year  = del_year;
    if (cutoff_day < 1) {
        // Underflowed into prior month (would require tf <= 9, which cannot happen
        // since third Friday >= 15; included for robustness).
        --cutoff_month;
        if (cutoff_month < 1) { cutoff_month = 12; --cutoff_year; }
        cutoff_day += days_in_month(cutoff_year, cutoff_month);
    }
    // ct_date < cutoff_date
    if (ct_year  != cutoff_year)  return ct_year  < cutoff_year;
    if (ct_month != cutoff_month) return ct_month < cutoff_month;
    return ct_day < cutoff_day;
}

// MonthlyEnergy: every month is a delivery month.
//
// Last trade date = 3 business days before the 25th of the MONTH PRECEDING
// delivery (e.g. July 2026 delivery → count back from June 25, 2026).
// Contract is eligible while ct_date < (last_trade_date - 2 calendar days).
inline bool monthly_energy_eligible(int ct_year, int ct_month, int ct_day,
                                    int del_year, int del_month) {
    // Month preceding delivery.
    int pre_month = del_month - 1;
    int pre_year  = del_year;
    if (pre_month < 1) { pre_month = 12; --pre_year; }

    // 3 business days before the 25th of the preceding month.
    int ltd_year, ltd_month, ltd_day;
    biz_days_before(pre_year, pre_month, 25, 3, &ltd_year, &ltd_month, &ltd_day);

    // Eligible while ct_date < (last_trade_date - 2 calendar days).
    // Subtract 2 calendar days from LTD.
    int cutoff_day   = ltd_day - 2;
    int cutoff_month = ltd_month;
    int cutoff_year  = ltd_year;
    if (cutoff_day < 1) {
        --cutoff_month;
        if (cutoff_month < 1) { cutoff_month = 12; --cutoff_year; }
        cutoff_day += days_in_month(cutoff_year, cutoff_month);
    }

    if (ct_year  != cutoff_year)  return ct_year  < cutoff_year;
    if (ct_month != cutoff_month) return ct_month < cutoff_month;
    return ct_day < cutoff_day;
}

// Gold: delivery months Feb/Apr/Jun/Aug/Oct/Dec.
// Contract is eligible while the delivery month/year is strictly after the
// current ct month/year (i.e., front = first cycle month after today's
// calendar month).
inline bool gold_eligible(int ct_year, int ct_month,
                          int del_year, int del_month) {
    // Delivery months only.
    if (del_month != 2 && del_month != 4 && del_month != 6 &&
        del_month != 8 && del_month != 10 && del_month != 12) {
        return false;
    }
    // Strictly after: delivery > current calendar month.
    if (del_year != ct_year) return del_year > ct_year;
    return del_month > ct_month;
}

// ---------------------------------------------------------------------------
// eligible_contracts
// ---------------------------------------------------------------------------

// Returns the first `count` eligible contract codes for the given root and
// roll cycle, evaluated against the CT calendar date (ct_year, ct_month,
// ct_day).  Contracts are returned in chronological (delivery) order.
//
// Format: ROOT + month_code(delivery_month) + last_digit_of_year
//   e.g. root="MES", delivery Mar 2026  → "MESH6"
//        root="MES", delivery Sep 2026  → "MESU6"
//
// Searches forward from (ct_year, ct_month) up to 24 months ahead.
// If fewer than `count` eligible contracts are found within 24 months,
// the returned vector is shorter than `count`.
inline std::vector<std::string> eligible_contracts(const std::string& root,
                                                    RollCycle cycle,
                                                    int ct_year, int ct_month,
                                                    int ct_day,
                                                    int count) {
    std::vector<std::string> result;
    result.reserve(static_cast<size_t>(count));

    // Start scanning from the current calendar month.
    int scan_year  = ct_year;
    int scan_month = ct_month;

    for (int step = 0; step < 24 && static_cast<int>(result.size()) < count; ++step) {
        bool eligible = false;
        switch (cycle) {
            case RollCycle::QuarterlyEquity:
                eligible = quarterly_equity_eligible(
                    ct_year, ct_month, ct_day,
                    scan_year, scan_month);
                break;
            case RollCycle::MonthlyEnergy:
                eligible = monthly_energy_eligible(
                    ct_year, ct_month, ct_day,
                    scan_year, scan_month);
                break;
            case RollCycle::Gold:
                eligible = gold_eligible(ct_year, ct_month, scan_year, scan_month);
                break;
        }

        if (eligible) {
            // Build the contract code: ROOT + month_code + last_digit_of_year
            std::string code = root;
            code += month_code(scan_month);
            code += static_cast<char>('0' + (scan_year % 10));
            result.push_back(std::move(code));
        }

        // Advance to the next calendar month.
        ++scan_month;
        if (scan_month > 12) {
            scan_month = 1;
            ++scan_year;
        }
    }

    return result;
}

// ---------------------------------------------------------------------------
// Parse a cycle name string (as written in YAML) into a RollCycle value.
// Returns false if the string is not recognized.
// ---------------------------------------------------------------------------

inline bool parse_roll_cycle(const std::string& s, RollCycle& out) {
    if (s == "quarterly_equity") { out = RollCycle::QuarterlyEquity; return true; }
    if (s == "monthly_energy")   { out = RollCycle::MonthlyEnergy;   return true; }
    if (s == "gold")             { out = RollCycle::Gold;            return true; }
    return false;
}

inline const char* roll_cycle_name(RollCycle c) {
    switch (c) {
        case RollCycle::QuarterlyEquity: return "quarterly_equity";
        case RollCycle::MonthlyEnergy:   return "monthly_energy";
        case RollCycle::Gold:            return "gold";
    }
    return "unknown";
}
