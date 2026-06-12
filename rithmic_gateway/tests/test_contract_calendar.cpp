// test_contract_calendar.cpp — Standalone unit tests for contract_calendar.hpp.
//
// Compile:
//   g++ -std=c++17 -O2 -I rithmic_gateway/include \
//       rithmic_gateway/tests/test_contract_calendar.cpp \
//       -o build/test_contract_calendar
//
// Run:
//   ./build/test_contract_calendar

#include "contract_calendar.hpp"

#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

// ---------------------------------------------------------------------------
// Minimal test harness
// ---------------------------------------------------------------------------

static int g_pass = 0;
static int g_fail = 0;

static void check_bool(const char* file, int line, const char* expr,
                       bool got, bool expected) {
    if (got == expected) {
        ++g_pass;
    } else {
        ++g_fail;
        std::fprintf(stderr, "FAIL %s:%d  %s  got=%s expected=%s\n",
                     file, line, expr,
                     got ? "true" : "false",
                     expected ? "true" : "false");
    }
}

static void check_int(const char* file, int line, const char* expr,
                      int got, int expected) {
    if (got == expected) {
        ++g_pass;
    } else {
        ++g_fail;
        std::fprintf(stderr, "FAIL %s:%d  %s  got=%d expected=%d\n",
                     file, line, expr, got, expected);
    }
}

static void check_str(const char* file, int line, const char* expr,
                      const std::string& got, const std::string& expected) {
    if (got == expected) {
        ++g_pass;
    } else {
        ++g_fail;
        std::fprintf(stderr, "FAIL %s:%d  %s  got=\"%s\" expected=\"%s\"\n",
                     file, line, expr, got.c_str(), expected.c_str());
    }
}

static void check_vec(const char* file, int line, const char* expr,
                      const std::vector<std::string>& got,
                      const std::vector<std::string>& expected) {
    if (got == expected) {
        ++g_pass;
        return;
    }
    ++g_fail;
    std::fprintf(stderr, "FAIL %s:%d  %s\n", file, line, expr);
    std::fprintf(stderr, "  got:     [");
    for (size_t i = 0; i < got.size(); ++i)
        std::fprintf(stderr, "%s%s", i ? "," : "", got[i].c_str());
    std::fprintf(stderr, "]\n");
    std::fprintf(stderr, "  expected:[");
    for (size_t i = 0; i < expected.size(); ++i)
        std::fprintf(stderr, "%s%s", i ? "," : "", expected[i].c_str());
    std::fprintf(stderr, "]\n");
}

#define CHECK_INT(got, expected) \
    check_int(__FILE__, __LINE__, #got, (got), (expected))

#define CHECK_STR(got, expected) \
    check_str(__FILE__, __LINE__, #got, (got), (expected))

#define CHECK_VEC(got, expected) \
    check_vec(__FILE__, __LINE__, #got, (got), (expected))

// ---------------------------------------------------------------------------
// third_friday tests
// ---------------------------------------------------------------------------

static void test_third_friday() {
    // 2026-06: June 1 is Monday (wday=1).  Days to first Friday = (5-1+7)%7 = 4.
    // First Friday = 5.  Third Friday = 5+14 = 19.
    CHECK_INT(third_friday(2026, 6), 19);

    // 2026-03: March 1 is Sunday (wday=0).  Days to first Friday = (5-0+7)%7 = 5.
    // First Friday = 6.  Third Friday = 6+14 = 20.
    CHECK_INT(third_friday(2026, 3), 20);

    // 2026-09: September 1 is Tuesday (wday=2).  Days to first Friday = (5-2+7)%7 = 3.
    // First Friday = 4.  Third Friday = 4+14 = 18.
    CHECK_INT(third_friday(2026, 9), 18);

    // 2026-12: December 1 is Tuesday (wday=2).  Same calculation → 18.
    CHECK_INT(third_friday(2026, 12), 18);
}

// ---------------------------------------------------------------------------
// biz_days_before tests
// ---------------------------------------------------------------------------

static void test_biz_days_before() {
    // 2026-06-25 is a Thursday.
    // Step back 3 business days: Wed 24 (count=1), Tue 23 (count=2), Mon 22 (count=3).
    // Result: 2026-06-22.
    int y, m, d;
    biz_days_before(2026, 6, 25, 3, &y, &m, &d);
    CHECK_INT(y, 2026);
    CHECK_INT(m, 6);
    CHECK_INT(d, 22);
}

// ---------------------------------------------------------------------------
// eligible_contracts tests
// ---------------------------------------------------------------------------

static void test_eligible_contracts_2026_06_12() {
    // --- MES / QuarterlyEquity / 2026-06-12 ---
    //
    // Jun 2026 delivery: third_friday(2026,6) = 19, cutoff = 10.
    //   Is 2026-06-12 < 2026-06-10?  No → NOT eligible.
    // Sep 2026 delivery: third_friday(2026,9) = 18, cutoff = 9.
    //   Is 2026-06-12 < 2026-09-09?  Yes → MESU6.
    // Dec 2026 delivery: third_friday(2026,12) = 18, cutoff = 9.
    //   Is 2026-06-12 < 2026-12-09?  Yes → MESZ6.
    CHECK_VEC(
        eligible_contracts("MES", RollCycle::QuarterlyEquity, 2026, 6, 12, 2),
        (std::vector<std::string>{"MESU6", "MESZ6"})
    );

    // --- MGC / Gold / 2026-06-12 ---
    //
    // Gold delivery months: Feb(2), Apr(4), Jun(6), Aug(8), Oct(10), Dec(12).
    // Jun 2026: del_month(6) > ct_month(6)?  No (strictly greater required) → NOT eligible.
    // Aug 2026: del_month(8) > ct_month(6)?  Yes → MGCQ6.  (Q = Aug)
    // Oct 2026: del_month(10) > 6?  Yes → MGCV6.  (V = Oct)
    CHECK_VEC(
        eligible_contracts("MGC", RollCycle::Gold, 2026, 6, 12, 2),
        (std::vector<std::string>{"MGCQ6", "MGCV6"})
    );

    // --- MCL / MonthlyEnergy / 2026-06-12 ---
    //
    // July 2026 delivery:
    //   Preceding month = June.  biz_days_before(2026,6,25,3) = 2026-06-22.
    //   LTD = Jun 22.  Cutoff = Jun 20.  Is 2026-06-12 < 2026-06-20?  Yes → MCLN6.
    // August 2026 delivery:
    //   Preceding month = July.  biz_days_before(2026,7,25,3):
    //     Jul 25 = Saturday → step back: Jul 24 (Fri, count=1), Jul 23 (Thu, count=2),
    //     Jul 22 (Wed, count=3).  LTD = Jul 22.  Cutoff = Jul 20.
    //   Is 2026-06-12 < 2026-07-20?  Yes → MCLQ6.
    CHECK_VEC(
        eligible_contracts("MCL", RollCycle::MonthlyEnergy, 2026, 6, 12, 2),
        (std::vector<std::string>{"MCLN6", "MCLQ6"})
    );
}

static void test_eligible_contracts_2026_06_21_mcl() {
    // --- MCL / MonthlyEnergy / 2026-06-21 ---
    //
    // July 2026 delivery: cutoff = Jun 20.  Is 2026-06-21 < 2026-06-20?  No → NOT eligible.
    // August 2026 delivery: cutoff = Jul 20.  Is 2026-06-21 < 2026-07-20?  Yes → MCLQ6.
    // September 2026 delivery:
    //   Preceding month = August.  biz_days_before(2026,8,25,3):
    //     Aug 25 = Tuesday.  Back: Aug 24 (Mon, count=1), Aug 23 (Sun, skip),
    //     Aug 22 (Sat, skip), Aug 21 (Fri, count=2), Aug 20 (Thu, count=3).
    //     LTD = Aug 20.  Cutoff = Aug 18.
    //   Is 2026-06-21 < 2026-08-18?  Yes → MCLU6.
    CHECK_VEC(
        eligible_contracts("MCL", RollCycle::MonthlyEnergy, 2026, 6, 21, 2),
        (std::vector<std::string>{"MCLQ6", "MCLU6"})
    );
}

static void test_eligible_contracts_2026_12_15_mgc() {
    // --- MGC / Gold / 2026-12-15 ---
    //
    // Gold months from Dec 2026:
    //   Dec 2026: del_year==ct_year(2026), del_month(12) > ct_month(12)?  No → NOT eligible.
    //   Feb 2027: del_year(2027) > ct_year(2026)?  Yes → MGCG7.  (G = Feb, 7 = 2027%10)
    //   Apr 2027: del_year > ct_year → MGCJ7.  (J = Apr)
    CHECK_VEC(
        eligible_contracts("MGC", RollCycle::Gold, 2026, 12, 15, 2),
        (std::vector<std::string>{"MGCG7", "MGCJ7"})
    );
}

static void test_eligible_contracts_2026_03_13_mes() {
    // --- MES / QuarterlyEquity / 2026-03-13 ---
    //
    // Roll window reasoning:
    //   Mar 2026 delivery: third_friday(2026,3) = 20.  Cutoff = 20 - 9 = 11 (Mar 11).
    //     Is 2026-03-13 < 2026-03-11?  No → MESH6 NOT eligible (past roll cutoff).
    //   Jun 2026 delivery: third_friday(2026,6) = 19.  Cutoff = 19 - 9 = 10 (Jun 10).
    //     Is 2026-03-13 < 2026-06-10?  Yes → MESM6 eligible.
    //   Sep 2026 delivery: third_friday(2026,9) = 18.  Cutoff = 18 - 9 = 9 (Sep 9).
    //     Is 2026-03-13 < 2026-09-09?  Yes → MESU6 eligible.
    //
    // Result: {MESM6, MESU6}.
    // (MESH6 is excluded because Mar 13 is 2 days past the Mar 11 cutoff.)
    CHECK_VEC(
        eligible_contracts("MES", RollCycle::QuarterlyEquity, 2026, 3, 13, 2),
        (std::vector<std::string>{"MESM6", "MESU6"})
    );
}

static void test_eligible_contracts_gold_year_wrap() {
    // Already covered by test_eligible_contracts_2026_12_15_mgc(), but add a
    // slightly different date to confirm year-wrap arithmetic.
    //
    // MGC / Gold / 2026-10-31:
    //   Oct 2026: del_month(10) > ct_month(10)?  No → NOT eligible.
    //   Dec 2026: del_month(12) > ct_month(10)?  Yes → MGCZ6.
    //   Feb 2027: del_year(2027) > ct_year(2026)?  Yes → MGCG7.
    CHECK_VEC(
        eligible_contracts("MGC", RollCycle::Gold, 2026, 10, 31, 2),
        (std::vector<std::string>{"MGCZ6", "MGCG7"})
    );
}

static void test_eligible_contracts_december_equity() {
    // MES / QuarterlyEquity / 2026-12-15:
    //
    //   Dec 2026 delivery: third_friday(2026,12) = 18.  Cutoff = 18 - 9 = 9.
    //     Is 2026-12-15 < 2026-12-09?  No → MESZ6 NOT eligible.
    //   Mar 2027 delivery: third_friday(2027,3): Mar 1, 2027 is Monday (wday=1).
    //     Days to first Friday = (5-1+7)%7 = 4.  First Friday = 5.  Third Friday = 19.
    //     Cutoff = 19 - 9 = 10 (Mar 10, 2027).
    //     Is 2026-12-15 < 2027-03-10?  Yes (different year, 2026 < 2027) → MESH7.
    //   Jun 2027 delivery: cutoff is in 2027; 2026 < 2027 → MESM7.
    CHECK_VEC(
        eligible_contracts("MES", RollCycle::QuarterlyEquity, 2026, 12, 15, 2),
        (std::vector<std::string>{"MESH7", "MESM7"})
    );
}

// ---------------------------------------------------------------------------
// parse_roll_cycle tests
// ---------------------------------------------------------------------------

static void test_parse_roll_cycle() {
    RollCycle c;

    c = RollCycle::Gold;  // set to something non-default
    CHECK_INT(parse_roll_cycle("quarterly_equity", c) ? 1 : 0, 1);
    CHECK_INT(static_cast<int>(c), static_cast<int>(RollCycle::QuarterlyEquity));

    c = RollCycle::QuarterlyEquity;
    CHECK_INT(parse_roll_cycle("monthly_energy", c) ? 1 : 0, 1);
    CHECK_INT(static_cast<int>(c), static_cast<int>(RollCycle::MonthlyEnergy));

    c = RollCycle::QuarterlyEquity;
    CHECK_INT(parse_roll_cycle("gold", c) ? 1 : 0, 1);
    CHECK_INT(static_cast<int>(c), static_cast<int>(RollCycle::Gold));

    CHECK_INT(parse_roll_cycle("unknown_cycle", c) ? 1 : 0, 0);
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int main() {
    test_third_friday();
    test_biz_days_before();
    test_eligible_contracts_2026_06_12();
    test_eligible_contracts_2026_06_21_mcl();
    test_eligible_contracts_2026_12_15_mgc();
    test_eligible_contracts_2026_03_13_mes();
    test_eligible_contracts_gold_year_wrap();
    test_eligible_contracts_december_equity();
    test_parse_roll_cycle();

    if (g_fail == 0) {
        std::printf("OK  %d/%d tests passed\n", g_pass, g_pass + g_fail);
        return 0;
    } else {
        std::printf("FAIL  %d passed, %d FAILED\n", g_pass, g_fail);
        return 1;
    }
}
