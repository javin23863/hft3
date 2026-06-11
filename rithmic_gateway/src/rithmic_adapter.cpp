#include "rithmic_adapter.hpp"

#include <iostream>
#include <cstring>
#include <chrono>
#include <sstream>

#include "RApiPlus.h"

namespace hft {

static char report_type_char(RApi::OrderReport* pReport) {
    const char* p = pReport->sReportType.pData;
    int n = pReport->sReportType.iDataLen;
    if (!p || n <= 0) return '?';
    auto starts_with = [&](const char* s) {
        int m = static_cast<int>(std::strlen(s));
        return n >= m && std::memcmp(p, s, static_cast<size_t>(m)) == 0;
    };
    if (starts_with("OrderStatusReport"))        return 'A';
    if (starts_with("OrderFillReport"))          return 'F';
    if (starts_with("OrderCancelReport"))        return 'C';
    if (starts_with("OrderNotCancelledReport"))  return 'C';
    if (starts_with("OrderModifyReport"))        return 'M';
    if (starts_with("OrderNotModifiedReport"))   return 'M';
    if (starts_with("OrderRejectReport"))        return 'R';
    if (starts_with("OrderFailureReport"))       return 'X';
    if (starts_with("OrderBustReport"))          return 'X';
    if (starts_with("OrderTradeCorrectReport"))  return 'F';
    if (starts_with("OrderTriggerReport"))       return 'A';
    if (starts_with("OrderTriggerPulledReport")) return 'C';
    return '?';
}

static char buysell_to_side(const tsNCharcb& s) {
    if (s.iDataLen <= 0 || s.pData == nullptr) return ' ';
    char c = s.pData[0];
    if (c == 'B' || c == 'b') return 'B';
    if (c == 'S' || c == 's') return 'A';
    return c;
}

static char entrytype_to_ordertype(const tsNCharcb& s) {
    if (s.iDataLen <= 0 || s.pData == nullptr) return ' ';
    return s.pData[0];
}

static uint64_t order_id_to_u64(const tsNCharcb& s) {
    if (s.iDataLen <= 0 || s.pData == nullptr) return 0ULL;
    char tmp[32];
    int n = s.iDataLen < 31 ? s.iDataLen : 31;
    std::memcpy(tmp, s.pData, static_cast<size_t>(n));
    tmp[n] = '\0';
    char* endp = nullptr;
    return static_cast<uint64_t>(std::strtoull(tmp, &endp, 10));
}

static void copy_nchar(char* dst, size_t dst_len, const tsNCharcb& src) {
    if (!dst || dst_len == 0) return;
    dst[0] = '\0';
    if (src.iDataLen <= 0 || src.pData == nullptr) return;
    size_t n = static_cast<size_t>(src.iDataLen);
    if (n >= dst_len) n = dst_len - 1;
    std::memcpy(dst, src.pData, n);
    dst[n] = '\0';
}

static std::string nchar_to_string(const tsNCharcb& src) {
    if (src.iDataLen <= 0 || src.pData == nullptr) return std::string();
    return std::string(src.pData, src.pData + src.iDataLen);
}

static bool contains_text(const std::string& haystack, const char* needle) {
    return haystack.find(needle) != std::string::npos;
}

static uint64_t steady_now_ns() {
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()
        ).count()
    );
}

static uint64_t wall_now_ns() {
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch()
        ).count()
    );
}

static OrderEvent make_order_event(RApi::OrderReport* pReport, char event_type) {
    OrderEvent evt{};
    evt.timestamp_ns = static_cast<uint64_t>(pReport->iSsboe) * 1000000000ULL
                     + static_cast<uint64_t>(pReport->iUsecs) * 1000ULL;
    evt.callback_monotonic_ns = steady_now_ns();
    evt.callback_wall_ns = wall_now_ns();
    evt.order_id = order_id_to_u64(pReport->sOrderNum);
    evt.event_type = event_type;
    evt.side = buysell_to_side(pReport->sBuySellType);
    evt.order_type = entrytype_to_ordertype(pReport->sEntryType);
    if (pReport->bFillPriceFlag) {
        evt.price = pReport->dFillPrice;
    } else if (pReport->bPriceToFillFlag) {
        evt.price = pReport->dPriceToFill;
    } else {
        evt.price = 0.0;
    }
    evt.size = static_cast<int32_t>(pReport->llTotalFilled + pReport->llTotalUnfilled);
    evt.filled_size = static_cast<int32_t>(pReport->llFillSize);
    evt.total_filled = static_cast<int32_t>(pReport->llTotalFilled);
    evt.total_unfilled = static_cast<int32_t>(pReport->llTotalUnfilled);
    copy_nchar(evt.user_msg, sizeof(evt.user_msg), pReport->sUserMsg);
    copy_nchar(evt.tag, sizeof(evt.tag), pReport->sTag);
    return evt;
}

static char line_event_type(RApi::LineInfo* pInfo) {
    std::string status = nchar_to_string(pInfo->sStatus);
    std::string reason = nchar_to_string(pInfo->sCompletionReason);

    if (pInfo->iRpCode != 0) {
        if (contains_text(reason, "R") || contains_text(status, "reject")) return 'R';
        return 'X';
    }
    if (contains_text(reason, "FA") || contains_text(status, "fail")) return 'X';
    if (contains_text(reason, "R") || contains_text(status, "reject")) return 'R';
    if (pInfo->llCancelled > 0 || contains_text(status, "cancel")) return 'C';
    if (contains_text(status, "modified")) return 'M';
    if (pInfo->llFilled > 0 || contains_text(reason, "F") || contains_text(status, "partial")) return 'F';
    return 'A';
}

static OrderEvent make_line_event(RApi::LineInfo* pInfo) {
    OrderEvent evt{};
    evt.timestamp_ns = static_cast<uint64_t>(pInfo->iSsboe) * 1000000000ULL
                     + static_cast<uint64_t>(pInfo->iUsecs) * 1000ULL;
    evt.callback_monotonic_ns = steady_now_ns();
    evt.callback_wall_ns = wall_now_ns();
    evt.order_id = order_id_to_u64(pInfo->sOrderNum);
    evt.event_type = line_event_type(pInfo);
    evt.side = buysell_to_side(pInfo->sBuySellType);
    evt.order_type = entrytype_to_ordertype(pInfo->sEntryType);
    if (pInfo->bPriceToFillFlag) {
        evt.price = pInfo->dPriceToFill;
    } else if (pInfo->bAvgFillPriceFlag) {
        evt.price = pInfo->dAvgFillPrice;
    } else {
        evt.price = 0.0;
    }
    long long total_size = pInfo->llFilled + pInfo->llUnfilled;
    if (total_size <= 0) {
        total_size = pInfo->llQuantityToFill;
    }
    evt.size = static_cast<int32_t>(total_size);
    evt.filled_size = static_cast<int32_t>(pInfo->llFilled);
    evt.total_filled = static_cast<int32_t>(pInfo->llFilled);
    evt.total_unfilled = static_cast<int32_t>(pInfo->llUnfilled);
    copy_nchar(evt.user_msg, sizeof(evt.user_msg), pInfo->sUserMsg);
    copy_nchar(evt.tag, sizeof(evt.tag), pInfo->sTag);
    return evt;
}

class MyAdmCallbacks : public RApi::AdmCallbacks {
public:
    explicit MyAdmCallbacks(RithmicAdapter* adapter)
        : adapter_(adapter) {}
    ~MyAdmCallbacks() override = default;

    int Alert(RApi::AlertInfo* pInfo, void*, int* aiCode) override {
        // Forward the administrative alert into the adapter's severity tracker and
        // log it via the adapter's existing error string so it is visible on the
        // gateway loop without blocking this callback thread on I/O.
        if (pInfo) {
            // Map iAlertType to the 0-3 severity scale used throughout this adapter.
            // (RApi::AlertInfo carries no iSeverity field in RApiPlus 13.7.0.0.)
            int sev;
            switch (pInfo->iAlertType) {
                case RApi::ALERT_LOGIN_FAILED:
                case RApi::ALERT_FORCED_LOGOUT:
                case RApi::ALERT_SHUTDOWN_SIGNAL:
                    sev = 3; break;  // fatal
                case RApi::ALERT_CONNECTION_BROKEN:
                case RApi::ALERT_SERVICE_ERROR:
                case RApi::ALERT_TRADING_DISABLED:
                    sev = 2; break;  // error
                case RApi::ALERT_CONNECTION_CLOSED:
                    sev = 1; break;  // warning
                default:
                    sev = 0; break;  // info
            }
            // Raise the high-water severity atomically so the consumer can read it.
            int prev = adapter_->adm_alert_severity_.load(std::memory_order_relaxed);
            while (sev > prev &&
                   !adapter_->adm_alert_severity_.compare_exchange_weak(
                       prev, sev, std::memory_order_relaxed)) { /* retry */ }

            // Severe alerts (error/fatal) set the order halt flag; position state
            // after a fatal administrative alert is unknown.
            if (sev >= 2) {
                adapter_->order_halt_.store(true, std::memory_order_release);
            }
        }
        *aiCode = API_OK;
        return OK;
    }

    int EnvironmentList(RApi::EnvironmentListInfo* pInfo, void* pContext, int* aiCode) override {
        (void)pContext;
        if (pInfo && pInfo->iArrayLen > 0 && pInfo->asKeyArray) {
            for (int i = 0; i < pInfo->iArrayLen; ++i) {
                tsNCharcb& key = pInfo->asKeyArray[i];
                if (key.pData && key.iDataLen > 0) {
                    std::string env_key(key.pData, static_cast<size_t>(key.iDataLen));
                    if (adapter_->discovered_env_key_.empty()) {
                        adapter_->discovered_env_key_ = env_key;
                    }
                }
            }
        }
        adapter_->env_list_ready_.store(true);
        adapter_->env_list_cv_.notify_all();
        *aiCode = API_OK;
        return OK;
    }

    int Environment(RApi::EnvironmentInfo* pInfo, void* pContext, int* aiCode) override {
        (void)pContext;
        (void)pInfo;
        adapter_->env_ready_.store(true);
        adapter_->env_cv_.notify_all();
        *aiCode = API_OK;
        return OK;
    }

private:
    RithmicAdapter* adapter_;
};

class MyCallbacks : public RApi::RCallbacks {
public:
    explicit MyCallbacks(RithmicAdapter* adapter)
        : adapter_(adapter) {}
    ~MyCallbacks() override = default;

    int Alert(RApi::AlertInfo* pInfo, void*, int* aiCode) override {
        if (pInfo->iConnectionId == RApi::MARKET_DATA_CONNECTION_ID) {
            if (pInfo->iAlertType == RApi::ALERT_LOGIN_COMPLETE) {
                adapter_->md_login_status_ = RithmicAdapter::LOGIN_COMPLETE;
            } else if (pInfo->iAlertType == RApi::ALERT_LOGIN_FAILED
                       || pInfo->iAlertType == RApi::ALERT_CONNECTION_BROKEN) {
                adapter_->md_login_status_ = RithmicAdapter::LOGIN_FAILED;
            }
        }

        if (pInfo->iConnectionId == RApi::TRADING_SYSTEM_CONNECTION_ID) {
            if (pInfo->iAlertType == RApi::ALERT_LOGIN_COMPLETE) {
                adapter_->ts_login_status_ = RithmicAdapter::LOGIN_COMPLETE;
            } else if (pInfo->iAlertType == RApi::ALERT_LOGIN_FAILED
                       || pInfo->iAlertType == RApi::ALERT_CONNECTION_BROKEN) {
                adapter_->ts_login_status_ = RithmicAdapter::LOGIN_FAILED;
            }
        }

        if (pInfo->iConnectionId == RApi::REPOSITORY_CONNECTION_ID) {
            if (pInfo->iAlertType == RApi::ALERT_LOGIN_COMPLETE) {
                adapter_->rep_login_status_ = RithmicAdapter::LOGIN_COMPLETE;
            } else if (pInfo->iAlertType == RApi::ALERT_LOGIN_FAILED
                       || pInfo->iAlertType == RApi::ALERT_CONNECTION_BROKEN) {
                adapter_->rep_login_status_ = RithmicAdapter::LOGIN_FAILED;
            }
        }

        adapter_->login_cv_.notify_all();
        *aiCode = API_OK;
        return OK;
    }

    int AccountList(RApi::AccountListInfo* pInfo, void* pContext, int* aiCode) override {
        (void)pContext;
        if (pInfo->iArrayLen > 0) {
            const RApi::AccountInfo& a = pInfo->asAccountInfoArray[0];
            std::lock_guard<std::mutex> lk(adapter_->account_mutex_);
            if (a.sAccountId.pData && a.sAccountId.iDataLen > 0) {
                adapter_->account_id_.assign(a.sAccountId.pData,
                                             a.sAccountId.pData + a.sAccountId.iDataLen);
            }
            if (a.sFcmId.pData && a.sFcmId.iDataLen > 0) {
                adapter_->fcm_id_.assign(a.sFcmId.pData,
                                         a.sFcmId.pData + a.sFcmId.iDataLen);
            }
            if (a.sIbId.pData && a.sIbId.iDataLen > 0) {
                adapter_->ib_id_.assign(a.sIbId.pData,
                                        a.sIbId.pData + a.sIbId.iDataLen);
            }
            adapter_->account_ready_.store(true);
            adapter_->account_cv_.notify_all();
        }
        *aiCode = API_OK;
        return OK;
    }

    int AgreementList(RApi::AgreementListInfo* pInfo, void* pContext, int* aiCode) override {
        (void)pContext;
        std::lock_guard<std::mutex> lk(adapter_->agreement_mutex_);
        std::ostringstream os;
        if (pInfo) {
            os << "rp_code=" << pInfo->iRpCode
               << " bAccepted=" << (pInfo->bAccepted ? "true" : "false")
               << " count=" << pInfo->iArrayLen;
            int unaccepted_mandatory = 0;
            if (pInfo->iArrayLen > 0 && pInfo->asAgreementInfoArray) {
                os << "\n";
                for (int i = 0; i < pInfo->iArrayLen; ++i) {
                    RApi::AgreementInfo* a = &pInfo->asAgreementInfoArray[i];
                    if (!pInfo->bAccepted && a->bMandatory) {
                        ++unaccepted_mandatory;
                    }
                    std::string title, status, accept_status, market_data;
                    if (a->sTitle.pData && a->sTitle.iDataLen > 0)
                        title.assign(a->sTitle.pData, static_cast<size_t>(a->sTitle.iDataLen));
                    if (a->sStatus.pData && a->sStatus.iDataLen > 0)
                        status.assign(a->sStatus.pData, static_cast<size_t>(a->sStatus.iDataLen));
                    if (a->sEndUserAcceptanceStatus.pData && a->sEndUserAcceptanceStatus.iDataLen > 0)
                        accept_status.assign(a->sEndUserAcceptanceStatus.pData,
                                             static_cast<size_t>(a->sEndUserAcceptanceStatus.iDataLen));
                    if (a->sMarketDataUsageCapacity.pData && a->sMarketDataUsageCapacity.iDataLen > 0)
                        market_data.assign(a->sMarketDataUsageCapacity.pData,
                                           static_cast<size_t>(a->sMarketDataUsageCapacity.iDataLen));
                    os << "  [" << i << "]"
                       << " mandatory=" << (a->bMandatory ? "true" : "false")
                       << " status=" << status
                       << " accept=" << accept_status
                       << " md_capacity=" << market_data
                       << " title=\"" << title << "\""
                       << "\n";
                }
            }
            if (!pInfo->bAccepted) {
                adapter_->unaccepted_mandatory_agreements_.store(unaccepted_mandatory);
            }
        } else {
            os << "rp_code=-1 (no info)";
        }
        adapter_->last_agreement_list_text_ = os.str();
        adapter_->agreement_list_ready_.store(true);
        adapter_->agreement_cv_.notify_all();
        if (aiCode) *aiCode = API_OK;
        return OK;
    }

    int TradeRouteList(RApi::TradeRouteListInfo* pInfo, void* pContext, int* aiCode) override {
        (void)pContext;
        std::lock_guard<std::mutex> lk(adapter_->trade_route_mutex_);
        for (int i = 0; i < pInfo->iArrayLen; ++i) {
            const RApi::TradeRouteInfo& r = pInfo->asTradeRouteInfoArray[i];
            if (r.sTradeRoute.pData && r.sTradeRoute.iDataLen > 0
                && r.sStatus.pData && r.sStatus.iDataLen == 2
                && std::memcmp(r.sStatus.pData, "UP", 2) == 0) {
                adapter_->trade_route_.assign(r.sTradeRoute.pData,
                                              r.sTradeRoute.pData + r.sTradeRoute.iDataLen);
                adapter_->trade_route_ready_.store(true);
                break;
            }
        }
        adapter_->trade_route_cv_.notify_all();
        *aiCode = API_OK;
        return OK;
    }

    int PriceIncrUpdate(RApi::PriceIncrInfo* pInfo, void* pContext, int* aiCode) override {
        (void)pContext;
        (void)pInfo;
        adapter_->price_incr_ready_.store(true);
        adapter_->price_incr_cv_.notify_all();
        *aiCode = API_OK;
        return OK;
    }

    int LineUpdate(RApi::LineInfo* pInfo, void* pContext, int* aiCode) override {
        (void)pContext;
        if (pInfo) {
            OrderEvent evt = make_line_event(pInfo);
            if (adapter_->order_queue_ && !adapter_->order_queue_->push(evt)) {
                // Order state is now unknown — hard halt.  Never block this thread.
                adapter_->order_event_drops_.fetch_add(1, std::memory_order_relaxed);
                adapter_->order_halt_.store(true, std::memory_order_release);
            }
        }
        *aiCode = API_OK;
        return OK;
    }

    int TradePrint(RApi::TradeInfo* pInfo, void* pContext, int* aiCode) override {
        (void)pContext;
        MarketDataEvent evt{};
        evt.timestamp_ns = static_cast<uint64_t>(pInfo->iSsboe) * 1000000000ULL
                         + static_cast<uint64_t>(pInfo->iUsecs) * 1000ULL;
        evt.callback_monotonic_ns = steady_now_ns();
        evt.price = pInfo->dPrice;
        evt.size = static_cast<int32_t>(pInfo->llSize);
        evt.action = 'T';

        if (pInfo->sAggressorSide.iDataLen > 0) {
            evt.side = pInfo->sAggressorSide.pData[0];
        } else {
            evt.side = ' ';
        }

        // Resolve ticker to symbol_id; pInfo->sTicker is length-counted (NOT null-terminated).
        evt.symbol_id = adapter_->lookup_symbol_id(
            pInfo->sTicker.pData, pInfo->sTicker.iDataLen);

        if (!adapter_->mbo_queue_->push(evt)) {
            // Never block inside a Rithmic callback thread.  Count the drop and
            // flag the data gap so the consumer forces a book resync.
            adapter_->md_drops_.fetch_add(1, std::memory_order_relaxed);
            adapter_->md_data_gap_.store(true, std::memory_order_release);
        }

        *aiCode = API_OK;
        return OK;
    }

    int BestBidAskQuote(RApi::BidInfo* pBid, RApi::AskInfo* pAsk,
                        void* pContext, int* aiCode) override {
        (void)pContext;
        // Snapshot monotonic time once for both bid and ask to minimise overhead.
        const uint64_t cb_mono = steady_now_ns();

        if (pBid && pBid->bPriceFlag) {
            MarketDataEvent evt{};
            evt.timestamp_ns = static_cast<uint64_t>(pBid->iSsboe) * 1000000000ULL
                             + static_cast<uint64_t>(pBid->iUsecs) * 1000ULL;
            evt.callback_monotonic_ns = cb_mono;
            evt.price = pBid->dPrice;
            evt.size = static_cast<int32_t>(pBid->llSize);
            evt.action = 'M';
            evt.side = 'B';
            evt.order_id = 0;
            // Resolve ticker to symbol_id; pBid->sTicker is length-counted.
            evt.symbol_id = adapter_->lookup_symbol_id(
                pBid->sTicker.pData, pBid->sTicker.iDataLen);
            if (!adapter_->mbo_queue_->push(evt)) {
                adapter_->md_drops_.fetch_add(1, std::memory_order_relaxed);
                // Stale best-bid is now in the consumer's future; force a resync.
                adapter_->md_data_gap_.store(true, std::memory_order_release);
            }
        }

        if (pAsk && pAsk->bPriceFlag) {
            MarketDataEvent evt{};
            evt.timestamp_ns = static_cast<uint64_t>(pAsk->iSsboe) * 1000000000ULL
                             + static_cast<uint64_t>(pAsk->iUsecs) * 1000ULL;
            evt.callback_monotonic_ns = cb_mono;
            evt.price = pAsk->dPrice;
            evt.size = static_cast<int32_t>(pAsk->llSize);
            evt.action = 'M';
            evt.side = 'A';
            evt.order_id = 0;
            // Resolve ticker to symbol_id; pAsk->sTicker is length-counted.
            evt.symbol_id = adapter_->lookup_symbol_id(
                pAsk->sTicker.pData, pAsk->sTicker.iDataLen);
            if (!adapter_->mbo_queue_->push(evt)) {
                adapter_->md_drops_.fetch_add(1, std::memory_order_relaxed);
                adapter_->md_data_gap_.store(true, std::memory_order_release);
            }
        }

        *aiCode = API_OK;
        return OK;
    }

    int FillReport(RApi::OrderFillReport* pReport, void* pContext, int* aiCode) override {
        (void)pContext;
        auto* base = static_cast<RApi::OrderReport*>(pReport);
        OrderEvent evt = make_order_event(base, report_type_char(base));
        if (adapter_->order_queue_ && !adapter_->order_queue_->push(evt)) {
            adapter_->order_event_drops_.fetch_add(1, std::memory_order_relaxed);
            adapter_->order_halt_.store(true, std::memory_order_release);
        }
        *aiCode = API_OK;
        return OK;
    }

    int StatusReport(RApi::OrderStatusReport* pReport, void* pContext, int* aiCode) override {
        (void)pContext;
        auto* base = static_cast<RApi::OrderReport*>(pReport);
        OrderEvent evt = make_order_event(base, report_type_char(base));
        if (adapter_->order_queue_ && !adapter_->order_queue_->push(evt)) {
            adapter_->order_event_drops_.fetch_add(1, std::memory_order_relaxed);
            adapter_->order_halt_.store(true, std::memory_order_release);
        }
        *aiCode = API_OK;
        return OK;
    }

    int CancelReport(RApi::OrderCancelReport* pReport, void* pContext, int* aiCode) override {
        (void)pContext;
        auto* base = static_cast<RApi::OrderReport*>(pReport);
        OrderEvent evt = make_order_event(base, report_type_char(base));
        if (adapter_->order_queue_ && !adapter_->order_queue_->push(evt)) {
            adapter_->order_event_drops_.fetch_add(1, std::memory_order_relaxed);
            adapter_->order_halt_.store(true, std::memory_order_release);
        }
        *aiCode = API_OK;
        return OK;
    }

    int ModifyReport(RApi::OrderModifyReport* pReport, void* pContext, int* aiCode) override {
        (void)pContext;
        auto* base = static_cast<RApi::OrderReport*>(pReport);
        OrderEvent evt = make_order_event(base, report_type_char(base));
        if (adapter_->order_queue_ && !adapter_->order_queue_->push(evt)) {
            adapter_->order_event_drops_.fetch_add(1, std::memory_order_relaxed);
            adapter_->order_halt_.store(true, std::memory_order_release);
        }
        *aiCode = API_OK;
        return OK;
    }

    int RejectReport(RApi::OrderRejectReport* pReport, void* pContext, int* aiCode) override {
        (void)pContext;
        auto* base = static_cast<RApi::OrderReport*>(pReport);
        OrderEvent evt = make_order_event(base, report_type_char(base));
        if (adapter_->order_queue_ && !adapter_->order_queue_->push(evt)) {
            adapter_->order_event_drops_.fetch_add(1, std::memory_order_relaxed);
            adapter_->order_halt_.store(true, std::memory_order_release);
        }
        *aiCode = API_OK;
        return OK;
    }

    int FailureReport(RApi::OrderFailureReport* pReport, void* pContext, int* aiCode) override {
        (void)pContext;
        auto* base = static_cast<RApi::OrderReport*>(pReport);
        OrderEvent evt = make_order_event(base, report_type_char(base));
        if (adapter_->order_queue_ && !adapter_->order_queue_->push(evt)) {
            adapter_->order_event_drops_.fetch_add(1, std::memory_order_relaxed);
            adapter_->order_halt_.store(true, std::memory_order_release);
        }
        *aiCode = API_OK;
        return OK;
    }

    int AccountUpdate(RApi::AccountUpdateInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int Aggregator(RApi::AggregatorInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int AskQuote(RApi::AskInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int AssignedUserList(RApi::AssignedUserListInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int AutoLiquidate(RApi::AutoLiquidateInfo* pInfo, void* pContext, int* aiCode) override {
        (void)pContext;
        // Broker has force-flattened the position outside our control.  Position
        // state is unknown — set halt-level flags immediately, then push a synthetic
        // OrderEvent of type 'L' so the consumer's event loop sees it without polling.
        // The consumer must stop sending orders and reconcile before resuming.
        adapter_->auto_liquidate_halt_.store(true, std::memory_order_release);
        adapter_->position_desync_.store(true, std::memory_order_release);
        adapter_->order_halt_.store(true, std::memory_order_release);

        if (adapter_->order_queue_) {
            OrderEvent evt{};
            evt.callback_monotonic_ns = steady_now_ns();
            evt.callback_wall_ns = wall_now_ns();
            evt.event_type = 'L';  // auto-liquidate
            // RApi::AutoLiquidateInfo carries no event timestamp; use wall clock.
            evt.timestamp_ns = evt.callback_wall_ns;
            if (!adapter_->order_queue_->push(evt)) {
                // Flags are already set above; count the drop but do not double-halt.
                adapter_->order_event_drops_.fetch_add(1, std::memory_order_relaxed);
            }
        }
        *aiCode = API_OK;
        return OK;
    }
    int AuxRefData(RApi::AuxRefDataInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int Bar(RApi::BarInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int BarReplay(RApi::BarReplayInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int BestAskQuote(RApi::AskInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int BestBidQuote(RApi::BidInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int BidQuote(RApi::BidInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int BinaryContractList(RApi::BinaryContractListInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int BracketReplay(RApi::BracketReplayInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int BracketTierModify(RApi::BracketTierModifyInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int BracketUpdate(RApi::BracketInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int BustReport(RApi::OrderBustReport* pReport, void* pContext, int* aiCode) override {
        (void)pContext;
        // A bust reverses a fill: position has changed retroactively.  Emit an
        // event so the consumer can apply the reversal, and set the position-desync
        // flag so the gateway loop knows reconciliation is required even if the
        // queue is full.
        if (pReport) {
            auto* base = static_cast<RApi::OrderReport*>(pReport);
            OrderEvent evt = make_order_event(base, 'B');  // 'B' = bust
            if (adapter_->order_queue_ && !adapter_->order_queue_->push(evt)) {
                // Queue full — the consumer cannot see this bust; order state is now
                // unknown.  Set the hard-halt flag rather than silently dropping.
                adapter_->order_event_drops_.fetch_add(1, std::memory_order_relaxed);
                adapter_->order_halt_.store(true, std::memory_order_release);
            }
            // Always flag position desync regardless of queue success; the consumer
            // must reconcile position after processing the bust event.
            adapter_->position_desync_.store(true, std::memory_order_release);
        }
        *aiCode = API_OK;
        return OK;
    }
    int CloseMidPrice(RApi::CloseMidPriceInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int ClosePrice(RApi::ClosePriceInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int ClosingIndicator(RApi::ClosingIndicatorInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int Dbo(RApi::DboInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int DboBookRebuild(RApi::DboBookRebuildInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int EasyToBorrow(RApi::EasyToBorrowInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int EasyToBorrowList(RApi::EasyToBorrowListInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int EndQuote(RApi::EndQuoteInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int EquityOptionStrategyList(RApi::EquityOptionStrategyListInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int ExchangeList(RApi::ExchangeListInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int ExecutionReplay(RApi::ExecutionReplayInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int HighBidPrice(RApi::HighBidPriceInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int HighPrice(RApi::HighPriceInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int HighPriceLimit(RApi::HighPriceLimitInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int IbList(RApi::IbListInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int InstrumentByUnderlying(RApi::InstrumentByUnderlyingInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int InstrumentSearch(RApi::InstrumentSearchInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int LimitOrderBook(RApi::LimitOrderBookInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int LowAskPrice(RApi::LowAskPriceInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int LowPrice(RApi::LowPriceInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int LowPriceLimit(RApi::LowPriceLimitInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int MarketMode(RApi::MarketModeInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int MidPrice(RApi::MidPriceInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int NotCancelledReport(RApi::OrderNotCancelledReport* pReport, void* pContext, int* aiCode) override {
        (void)pContext;
        auto* base = static_cast<RApi::OrderReport*>(pReport);
        OrderEvent evt = make_order_event(base, 'X');
        if (adapter_->order_queue_ && !adapter_->order_queue_->push(evt)) {
            adapter_->order_event_drops_.fetch_add(1, std::memory_order_relaxed);
            adapter_->order_halt_.store(true, std::memory_order_release);
        }
        *aiCode = API_OK;
        return OK;
    }
    int NotModifiedReport(RApi::OrderNotModifiedReport* pReport, void* pContext, int* aiCode) override {
        (void)pContext;
        // The modify was rejected by the exchange; local order state (price/qty)
        // diverges from what the exchange has.  Emit an event so the consumer can
        // reconcile, and set the order-desync flag.
        if (pReport) {
            auto* base = static_cast<RApi::OrderReport*>(pReport);
            OrderEvent evt = make_order_event(base, 'N');  // 'N' = not-modified
            if (adapter_->order_queue_ && !adapter_->order_queue_->push(evt)) {
                adapter_->order_event_drops_.fetch_add(1, std::memory_order_relaxed);
                adapter_->order_halt_.store(true, std::memory_order_release);
            }
            // Flag order desync regardless; the consumer must re-query order state.
            adapter_->order_desync_.store(true, std::memory_order_release);
        }
        *aiCode = API_OK;
        return OK;
    }
    int OpenInterest(RApi::OpenInterestInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int OpenOrderReplay(RApi::OrderReplayInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int OpenPrice(RApi::OpenPriceInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int OpeningIndicator(RApi::OpeningIndicatorInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int OptionList(RApi::OptionListInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int OrderHistoryDates(RApi::OrderHistoryDatesInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int OrderReplay(RApi::OrderReplayInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int OtherReport(RApi::OrderReport*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int PasswordChange(RApi::PasswordChangeInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int Ping(RApi::PingInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int PnlReplay(RApi::PnlReplayInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int PnlUpdate(RApi::PnlInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int PositionExit(RApi::PositionExitInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int ProductRmsList(RApi::ProductRmsListInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int ProjectedSettlementPrice(RApi::ProjectedSettlementPriceInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int RefData(RApi::RefDataInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int SettlementPrice(RApi::SettlementPriceInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int SingleOrderReplay(RApi::SingleOrderReplayInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int SodUpdate(RApi::SodReport*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int Strategy(RApi::StrategyInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int StrategyList(RApi::StrategyListInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int TradeCondition(RApi::TradeInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int TradeCorrectReport(RApi::OrderTradeCorrectReport*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int TradeReplay(RApi::TradeReplayInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int TradeRoute(RApi::TradeRouteInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int TradeVolume(RApi::TradeVolumeInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int TriggerPulledReport(RApi::OrderTriggerPulledReport*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int TriggerReport(RApi::OrderTriggerReport*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int User(RApi::UserInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int UserDefinedSpreadCreate(RApi::UserDefinedSpreadCreateInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int UserList(RApi::UserListInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int UserProfile(RApi::UserProfileInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int VolumeAtPrice(RApi::VolumeAtPriceInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int Quote(RApi::QuoteReport*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int Quote(RApi::QuoteInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int QuoteReplay(RApi::QuoteReplayInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }

private:
    RithmicAdapter* adapter_;
};

static tsNCharcb make_ts(const char* s) {
    tsNCharcb cb;
    cb.pData = const_cast<char*>(s);
    cb.iDataLen = static_cast<int>(std::strlen(s));
    return cb;
}

static void copy_cstr(char* dst, size_t dst_len, const std::string& src) {
    if (!dst || dst_len == 0) return;
    size_t n = src.size();
    if (n >= dst_len) n = dst_len - 1;
    std::memcpy(dst, src.data(), n);
    dst[n] = '\0';
}

static void copy_cstr(char* dst, size_t dst_len, const char* src) {
    if (!dst || dst_len == 0) return;
    if (!src) {
        dst[0] = '\0';
        return;
    }
    size_t n = std::strlen(src);
    if (n >= dst_len) n = dst_len - 1;
    std::memcpy(dst, src, n);
    dst[n] = '\0';
}

struct PreparedLimitOrder {
    RApi::REngine* engine = nullptr;
    RApi::LimitOrderParams params{};
    RApi::AccountInfo account{};
    char exchange[32]{};
    char symbol[64]{};
    char trade_route[64]{};
    char account_id[64]{};
    char fcm_id[64]{};
    char ib_id[64]{};
    char buy[2]{"B"};
    char sell[2]{"S"};
    char duration[4]{"DAY"};
    char entry_type[2]{"M"};
    char tag[64]{};
    char user_msg[64]{};
};

RithmicAdapter::RithmicAdapter(const ConnectionConfig& config,
                               SPSCQueue<MarketDataEvent, 8192>* mbo_queue,
                               SPSCQueue<OrderEvent, 8192>* order_queue)
    : config_(config)
    , mbo_queue_(mbo_queue)
    , order_queue_(order_queue)
    , engine_(nullptr)
    , callbacks_(nullptr)
    , adm_callbacks_(nullptr)
{
}

RithmicAdapter::~RithmicAdapter() {
    disconnect();
}

void RithmicAdapter::build_envp() {
    cleanup_envp();
    env_storage_.reserve(config_.env_vars.size() + 1);
    for (auto& v : config_.env_vars) {
        env_storage_.push_back(v);
    }
    bool has_user = false;
    for (auto& v : env_storage_) {
        if (v.rfind("USER=", 0) == 0) { has_user = true; break; }
    }
    if (!has_user && !config_.username.empty()) {
        env_storage_.push_back("USER=" + config_.username);
    }
    env_strings_.reserve(env_storage_.size() + 1);
    for (auto& s : env_storage_) {
        env_strings_.push_back(const_cast<char*>(s.c_str()));
    }
    env_strings_.push_back(nullptr);
}

void RithmicAdapter::cleanup_envp() {
    env_strings_.clear();
    env_storage_.clear();
}

bool RithmicAdapter::initialize() {
    try {
        auto* adm = new MyAdmCallbacks(this);
        adm_callbacks_ = adm;
        return true;
    } catch (std::exception& ex) {
        std::cerr << "[RithmicAdapter] initialize error: " << ex.what() << std::endl;
        return false;
    }
}

bool RithmicAdapter::connect() {
    build_envp();

    auto* adm = static_cast<MyAdmCallbacks*>(adm_callbacks_);

    RApi::REngineParams params;
    params.sAppName = make_ts(config_.app_name.c_str());
    params.sAppVersion = make_ts(config_.app_version.c_str());
    params.envp = env_strings_.data();

    std::string ssl_path = config_.ssl_cert_path.empty()
        ? "rithmic_ssl_cert_auth_params"
        : config_.ssl_cert_path;
    for (auto& v : config_.env_vars) {
        if (v.find("MML_SSL_CLNT_AUTH_FILE=") == 0) {
            ssl_path = v.substr(strlen("MML_SSL_CLNT_AUTH_FILE="));
            break;
        }
    }

    params.pAdmCallbacks = adm;
    params.sLogFilePath = make_ts(config_.log_file_path.empty() ? "rithmic_gateway.log" : config_.log_file_path.c_str());

    RApi::REngine* pEngine = nullptr;
    try {
        pEngine = new RApi::REngine(&params);
    } catch (OmneException& ex) {
        const char* estr = ex.getErrorString();
        last_connect_error_ = "REngine creation error: " + std::to_string(ex.getErrorCode())
                            + " (" + (estr ? estr : "?") + ")";
        std::cerr << "[RithmicAdapter] " << last_connect_error_ << std::endl;
        return false;
    }
    engine_ = pEngine;

    auto* callbacks = new MyCallbacks(this);
    callbacks_ = callbacks;

    md_login_status_ = LOGIN_NOT_LOGGED_IN;
    ts_login_status_ = LOGIN_NOT_LOGGED_IN;
    rep_login_status_ = LOGIN_NOT_LOGGED_IN;

    // Step 1: Repository login — establishes the authenticated session
    if (!config_.rep_connect_point.empty()) {
        tsNCharcb envKey = make_ts("");
        tsNCharcb user = make_ts(config_.username.c_str());
        tsNCharcb pw = make_ts(config_.password.c_str());
        tsNCharcb cp = make_ts(config_.rep_connect_point.c_str());
        int iCode = 0;
        if (!pEngine->loginRepository(&envKey, &user, &pw, &cp, callbacks, &iCode)) {
            last_connect_error_ = "loginRepository error: " + std::to_string(iCode);
            std::cerr << "[RithmicAdapter] " << last_connect_error_ << std::endl;
            delete pEngine;
            delete callbacks;
            engine_ = nullptr;
            callbacks_ = nullptr;
            return false;
        }
        {
            std::unique_lock<std::mutex> lk(login_mutex_);
            login_cv_.wait_for(lk, std::chrono::seconds(30), [&] {
                return rep_login_status_ == LOGIN_COMPLETE || rep_login_status_ == LOGIN_FAILED;
            });
        }
        if (rep_login_status_ != LOGIN_COMPLETE) {
            last_connect_error_ = "repo login did not complete (status=" + std::to_string(rep_login_status_.load()) + ")";
            std::cerr << "[RithmicAdapter] " << last_connect_error_ << std::endl;
            delete pEngine;
            delete callbacks;
            engine_ = nullptr;
            callbacks_ = nullptr;
            return false;
        }

        // Step 2: Discover available environments and their variables
        discovered_env_key_.clear();
        env_list_ready_ = false;
        env_ready_ = false;
        int iCodeEnv = 0;
        if (!pEngine->listEnvironments(nullptr, &iCodeEnv)) {
            std::cerr << "[RithmicAdapter] listEnvironments error: " << iCodeEnv << std::endl;
        }
        {
            std::unique_lock<std::mutex> lk(env_mutex_);
            if (!env_list_cv_.wait_for(lk, std::chrono::seconds(10), [&] {
                    return env_list_ready_.load();
                })) {
                std::cerr << "[RithmicAdapter] listEnvironments timed out (no callback)" << std::endl;
            }
        }
        if (!discovered_env_key_.empty()) {
            tsNCharcb envKey = make_ts(discovered_env_key_.c_str());
            int iCodeGet = 0;
            if (!pEngine->getEnvironment(&envKey, nullptr, &iCodeGet)) {
                std::cerr << "[RithmicAdapter] getEnvironment error: " << iCodeGet << std::endl;
            }
            {
                std::unique_lock<std::mutex> lk(env_mutex_);
                if (!env_cv_.wait_for(lk, std::chrono::seconds(10), [&] {
                        return env_ready_.load();
                    })) {
                    std::cerr << "[RithmicAdapter] getEnvironment timed out" << std::endl;
                }
            }
        } else {
            std::cerr << "[RithmicAdapter] no environments discovered from repository" << std::endl;
        }

        // Step 2b: Discover agreements — list unaccepted agreements blocking service logins
        agreement_list_ready_.store(false);
        unaccepted_mandatory_agreements_.store(0);
        {
            std::lock_guard<std::mutex> lk(agreement_mutex_);
            last_agreement_list_text_.clear();
        }
        int iCodeAgr = 0;
        if (!pEngine->listAgreements(false, nullptr, &iCodeAgr)) {
            std::cerr << "[RithmicAdapter] listAgreements(unaccepted) submit error: "
                      << iCodeAgr << std::endl;
        } else {
            std::unique_lock<std::mutex> lk(agreement_mutex_);
            if (!agreement_cv_.wait_for(lk, std::chrono::seconds(5), [&] {
                    return agreement_list_ready_.load();
                })) {
                std::cerr << "[RithmicAdapter] listAgreements(unaccepted) timed out" << std::endl;
            }
        }
        if (unaccepted_mandatory_agreements_.load() > 0) {
            std::cerr << "[RithmicAdapter] WARNING: listAgreements(false) returned "
                      << unaccepted_mandatory_agreements_.load()
                      << " mandatory inactive agreements; continuing because "
                      << "Rithmic Trader Pro Web may already allow service login."
                      << std::endl;
        }
        int iCodeAgrAcc = 0;
        if (!pEngine->listAgreements(true, nullptr, &iCodeAgrAcc)) {
            std::cerr << "[RithmicAdapter] listAgreements(accepted) submit error: "
                      << iCodeAgrAcc << std::endl;
        } else {
            std::unique_lock<std::mutex> lk(agreement_mutex_);
            if (!agreement_cv_.wait_for(lk, std::chrono::seconds(5), [&] {
                    return agreement_list_ready_.load();
                })) {
                std::cerr << "[RithmicAdapter] listAgreements(accepted) timed out" << std::endl;
            }
        }
    } else {
        std::cerr << "[RithmicAdapter] no repo connect point, skipping repository login" << std::endl;
    }

    // Step 3: Login to individual service endpoints (MD, TS, IH, PnL)
    RApi::LoginParams login_params;
    tsNCharcb login_env_key = make_ts("");
    if (!discovered_env_key_.empty()) {
        login_env_key = make_ts(discovered_env_key_.c_str());
    }
    login_params.pCallbacks = callbacks;

    login_params.sMdEnvKey = login_env_key;
    login_params.sMdUser = make_ts(config_.username.c_str());
    login_params.sMdPassword = make_ts(config_.password.c_str());
    login_params.sMdCnnctPt = make_ts(config_.md_connect_point.c_str());

    login_params.sTsEnvKey = login_env_key;
    login_params.sTsUser = make_ts(config_.username.c_str());
    login_params.sTsPassword = make_ts(config_.password.c_str());
    login_params.sTsCnnctPt = make_ts(config_.ts_connect_point.c_str());
    login_params.sPnlCnnctPt = make_ts(config_.pnl_connect_point.c_str());
    login_params.sIhEnvKey = login_env_key;
    login_params.sIhUser = make_ts(config_.username.c_str());
    login_params.sIhPassword = make_ts(config_.password.c_str());
    login_params.sIhCnnctPt = make_ts(config_.ih_connect_point.c_str());

    int iCode = 0;
    if (!pEngine->login(&login_params, &iCode)) {
        last_connect_error_ = "login error: " + std::to_string(iCode);
        std::cerr << "[RithmicAdapter] " << last_connect_error_ << std::endl;
        delete pEngine;
        delete callbacks;
        engine_ = nullptr;
        callbacks_ = nullptr;
        return false;
    }

    {
        std::unique_lock<std::mutex> lk(login_mutex_);
        login_cv_.wait_for(lk, std::chrono::seconds(30), [&] {
            bool md_done = md_login_status_ == LOGIN_COMPLETE || md_login_status_ == LOGIN_FAILED;
            bool ts_done = ts_login_status_ == LOGIN_COMPLETE || ts_login_status_ == LOGIN_FAILED;
            return md_done && ts_done;
        });
    }

    bool md_ok = md_login_status_ == LOGIN_COMPLETE;
    bool ts_ok = ts_login_status_ == LOGIN_COMPLETE;
    if (!ts_ok) {
        last_connect_error_ = "TS login status=" + std::to_string(ts_login_status_.load())
                            + " MD status=" + std::to_string(md_login_status_.load());
        std::cerr << "[RithmicAdapter] " << last_connect_error_ << std::endl;
        delete pEngine;
        delete callbacks;
        engine_ = nullptr;
        callbacks_ = nullptr;
        return false;
    }
    if (!md_ok) {
        std::cerr << "[RithmicAdapter] MD login status=" << md_login_status_.load()
                  << " (TS OK)" << std::endl;
    }

    {
        std::unique_lock<std::mutex> lk(account_mutex_);
        account_cv_.wait_for(lk, std::chrono::seconds(30), [&] {
            return account_ready_.load();
        });
    }
    if (!account_ready_.load()) {
        last_connect_error_ = "No account received via AccountList";
        std::cerr << "[RithmicAdapter] " << last_connect_error_ << std::endl;
        delete pEngine;
        delete callbacks;
        engine_ = nullptr;
        callbacks_ = nullptr;
        return false;
    }

    int iCodeRt = 0;
    if (!pEngine->listTradeRoutes(nullptr, &iCodeRt)) {
        last_connect_error_ = "listTradeRoutes error: " + std::to_string(iCodeRt);
        std::cerr << "[RithmicAdapter] " << last_connect_error_ << std::endl;
        delete pEngine;
        delete callbacks;
        engine_ = nullptr;
        callbacks_ = nullptr;
        return false;
    }

    {
        std::unique_lock<std::mutex> lk(trade_route_mutex_);
        trade_route_cv_.wait_for(lk, std::chrono::seconds(30), [&] {
            return trade_route_ready_.load();
        });
    }
    if (!trade_route_ready_.load()) {
        last_connect_error_ = "No UP trade route available";
        std::cerr << "[RithmicAdapter] " << last_connect_error_ << std::endl;
        delete pEngine;
        delete callbacks;
        engine_ = nullptr;
        callbacks_ = nullptr;
        return false;
    }

    connected_ = true;
    logged_in_ = true;
    std::cout << "[RithmicAdapter] Connected to " << config_.environment
              << " (MD=" << config_.md_connect_point
              << ", TS=" << config_.ts_connect_point
              << ", account=" << account_id_
              << ", trade_route=" << trade_route_ << ")" << std::endl;
    return true;
}

const char* RithmicAdapter::cached_account_id() {
    std::lock_guard<std::mutex> lk(account_mutex_);
    return account_id_.c_str();
}

bool RithmicAdapter::list_agreements() {
    auto* engine = static_cast<RApi::REngine*>(engine_);
    if (!engine) {
        last_connect_error_ = "list_agreements: not connected";
        return false;
    }
    agreement_list_ready_.store(false);
    {
        std::lock_guard<std::mutex> lk(agreement_mutex_);
        last_agreement_list_text_.clear();
    }
    int iCode = -1;
    int rc = engine->listAgreements(false, nullptr, &iCode);
    if (rc != OK) {
        last_connect_error_ = "listAgreements submit error: rc=" + std::to_string(rc)
                            + " iCode=" + std::to_string(iCode);
        std::cerr << "[RithmicAdapter] " << last_connect_error_ << std::endl;
        return false;
    }
    {
        std::unique_lock<std::mutex> lk(agreement_mutex_);
        if (!agreement_cv_.wait_for(lk, std::chrono::seconds(5),
                                    [this]{ return agreement_list_ready_.load(); })) {
            last_connect_error_ = "listAgreements: timeout waiting for AgreementList callback";
            std::cerr << "[RithmicAdapter] " << last_connect_error_ << std::endl;
            return false;
        }
    }
    return true;
}

const char* RithmicAdapter::last_agreement_list_text() const {
    std::lock_guard<std::mutex> lk(agreement_mutex_);
    return last_agreement_list_text_.c_str();
}

const char* RithmicAdapter::cached_trade_route() {
    std::lock_guard<std::mutex> lk(trade_route_mutex_);
    return trade_route_.c_str();
}

void RithmicAdapter::disconnect() {
    if (engine_) {
        auto* pEngine = static_cast<RApi::REngine*>(engine_);
        if (logged_in_) {
            int iCode = 0;
            pEngine->logout(&iCode);
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
        }
        delete pEngine;
        engine_ = nullptr;
    }
    if (callbacks_) {
        delete static_cast<MyCallbacks*>(callbacks_);
        callbacks_ = nullptr;
    }
    if (adm_callbacks_) {
        delete static_cast<MyAdmCallbacks*>(adm_callbacks_);
        adm_callbacks_ = nullptr;
    }
    connected_ = false;
    logged_in_ = false;
    cleanup_envp();
}

bool RithmicAdapter::subscribe_mbo(const std::string& symbol, const std::string& exchange) {
    if (!connected_ || !engine_) return false;

    // Register the ticker in the symbol registry before subscribing so that
    // callbacks arriving immediately after subscribe() can resolve the id.
    if (symbol_count_ < kMaxSymbols) {
        bool already = false;
        for (int i = 0; i < symbol_count_; ++i) {
            if (static_cast<int>(symbol.size()) == symbol_registry_[i].len &&
                std::memcmp(symbol.c_str(), symbol_registry_[i].ticker,
                            static_cast<size_t>(symbol_registry_[i].len)) == 0) {
                already = true;
                break;
            }
        }
        if (!already) {
            SymbolEntry& e = symbol_registry_[symbol_count_];
            int len = static_cast<int>(
                symbol.size() < sizeof(e.ticker) - 1 ? symbol.size() : sizeof(e.ticker) - 1);
            std::memcpy(e.ticker, symbol.c_str(), static_cast<size_t>(len));
            e.ticker[len] = '\0';
            e.len = len;
            e.id  = static_cast<uint16_t>(symbol_count_);
            ++symbol_count_;
        }
    }

    auto* pEngine = static_cast<RApi::REngine*>(engine_);
    tsNCharcb sExchange = make_ts(exchange.c_str());
    tsNCharcb sTicker = make_ts(symbol.c_str());
    int iFlags = RApi::MD_PRINTS | RApi::MD_BEST;
    int iCode = 0;

    if (!pEngine->subscribe(&sExchange, &sTicker, iFlags, &iCode)) {
        std::cerr << "[RithmicAdapter] subscribe error: " << iCode << std::endl;
        return false;
    }
    return true;
}

uint16_t RithmicAdapter::lookup_symbol_id(const char* ticker, int len) const noexcept {
    if (!ticker || len <= 0) return 0xFFFF;
    // Linear scan over <=16 entries; length-first comparison avoids memcmp on mismatch.
    for (int i = 0; i < symbol_count_; ++i) {
        if (symbol_registry_[i].len == len &&
            std::memcmp(ticker, symbol_registry_[i].ticker, static_cast<size_t>(len)) == 0) {
            return symbol_registry_[i].id;
        }
    }
    return 0xFFFF;
}

bool RithmicAdapter::warm_price_increment(const std::string& symbol, const std::string& exchange) {
    if (!connected_ || !engine_) return false;

    const std::string key = exchange + "/" + symbol;
    {
        std::lock_guard<std::mutex> lk(price_incr_mutex_);
        if (price_incr_ready_keys_.find(key) != price_incr_ready_keys_.end()) {
            return true;
        }
    }

    auto* pEngine = static_cast<RApi::REngine*>(engine_);
    tsNCharcb sExchange = make_ts(exchange.c_str());
    tsNCharcb sTicker = make_ts(symbol.c_str());

    price_incr_ready_.store(false);
    int iPriceCode = 0;
    if (!pEngine->getPriceIncrInfo(&sExchange, &sTicker, &iPriceCode)) {
        std::cerr << "[RithmicAdapter] getPriceIncrInfo error: " << iPriceCode
                  << " for " << exchange << "/" << symbol << std::endl;
        return false;
    }
    {
        std::unique_lock<std::mutex> lk(price_incr_mutex_);
        if (!price_incr_cv_.wait_for(lk, std::chrono::seconds(10), [&] {
                return price_incr_ready_.load();
            })) {
            std::cerr << "[RithmicAdapter] getPriceIncrInfo timeout for "
                      << exchange << "/" << symbol << std::endl;
            return false;
        }
        price_incr_ready_keys_.insert(key);
    }
    return true;
}

PreparedLimitOrder* RithmicAdapter::prepare_limit_order(const std::string& symbol,
                                                        const std::string& exchange) {
    if (!connected_ || !engine_) return nullptr;
    if (!account_ready_.load() || !trade_route_ready_.load()) {
        std::cerr << "[RithmicAdapter] prepare_limit_order: account or trade route not ready" << std::endl;
        return nullptr;
    }
    if (!warm_price_increment(symbol, exchange)) {
        return nullptr;
    }

    auto* prepared = new PreparedLimitOrder();
    prepared->engine = static_cast<RApi::REngine*>(engine_);
    copy_cstr(prepared->exchange, sizeof(prepared->exchange), exchange);
    copy_cstr(prepared->symbol, sizeof(prepared->symbol), symbol);
    copy_cstr(prepared->trade_route, sizeof(prepared->trade_route), trade_route_);
    copy_cstr(prepared->account_id, sizeof(prepared->account_id), account_id_);
    copy_cstr(prepared->fcm_id, sizeof(prepared->fcm_id), fcm_id_);
    copy_cstr(prepared->ib_id, sizeof(prepared->ib_id), ib_id_);

    prepared->account.sAccountId = make_ts(prepared->account_id);
    prepared->account.sFcmId = make_ts(prepared->fcm_id);
    prepared->account.sIbId = make_ts(prepared->ib_id);

    prepared->params.sExchange = make_ts(prepared->exchange);
    prepared->params.sTicker = make_ts(prepared->symbol);
    prepared->params.sDuration = make_ts(prepared->duration);
    prepared->params.sEntryType = make_ts(prepared->entry_type);
    prepared->params.sTradeRoute = make_ts(prepared->trade_route);
    prepared->params.pAccount = &prepared->account;
    return prepared;
}

bool RithmicAdapter::set_prepared_limit_order_tag(PreparedLimitOrder* prepared,
                                                  const char* user_msg) {
    if (!prepared) return false;
    copy_cstr(prepared->tag, sizeof(prepared->tag), user_msg);
    copy_cstr(prepared->user_msg, sizeof(prepared->user_msg), user_msg);
    prepared->params.sTag = make_ts(prepared->tag);
    prepared->params.sUserMsg = make_ts(prepared->user_msg);
    return true;
}

bool RithmicAdapter::send_prepared_limit_order(PreparedLimitOrder* prepared, char side,
                                               int32_t qty, double price,
                                               const char* user_msg) {
    if (!connected_ || !engine_ || !prepared || !prepared->engine) return false;
    prepared->params.sBuySellType = make_ts(side == 'B' ? prepared->buy : prepared->sell);
    prepared->params.llQty = static_cast<long long>(qty);
    prepared->params.dPrice = price;
    if (user_msg) {
        set_prepared_limit_order_tag(prepared, user_msg);
    }

    int iCode = 0;
    if (!prepared->engine->sendOrder(&prepared->params, &iCode)) {
        std::cerr << "[RithmicAdapter] sendOrder prepared error: " << iCode << std::endl;
        return false;
    }
    return true;
}

void RithmicAdapter::destroy_prepared_limit_order(PreparedLimitOrder* prepared) {
    delete prepared;
}

bool RithmicAdapter::send_order(const std::string& symbol, char side, int32_t qty, double price,
                                const std::string& user_msg) {
    if (!connected_ || !engine_) return false;

    auto* pEngine = static_cast<RApi::REngine*>(engine_);

    if (!account_ready_.load() || !trade_route_ready_.load()) {
        std::cerr << "[RithmicAdapter] send_order: account or trade route not ready" << std::endl;
        return false;
    }

    RApi::LimitOrderParams params;
    tsNCharcb sExchange = make_ts("CME");
    tsNCharcb sTicker = make_ts(symbol.c_str());
    tsNCharcb sTradeRoute = make_ts(trade_route_.c_str());

    if (!warm_price_increment(symbol, "CME")) {
        return false;
    }

    params.sExchange = sExchange;
    params.sTicker = sTicker;
    params.sBuySellType = make_ts(side == 'B' ? "B" : "S");
    params.sDuration = make_ts("DAY");
    params.sEntryType = make_ts("M");
    params.llQty = static_cast<long long>(qty);
    params.dPrice = price;
    params.sTradeRoute = sTradeRoute;
    if (!user_msg.empty()) {
        params.sTag = make_ts(user_msg.c_str());
        params.sUserMsg = make_ts(user_msg.c_str());
    }

    RApi::AccountInfo dummy_acct{};
    tsNCharcb sAccount = make_ts(account_id_.c_str());
    dummy_acct.sAccountId = sAccount;
    tsNCharcb sFcm = make_ts(fcm_id_.c_str());
    dummy_acct.sFcmId = sFcm;
    tsNCharcb sIb = make_ts(ib_id_.c_str());
    dummy_acct.sIbId = sIb;
    params.pAccount = &dummy_acct;

    int iCode = 0;
    if (!pEngine->sendOrder(&params, &iCode)) {
        std::cerr << "[RithmicAdapter] sendOrder error: " << iCode << std::endl;
        return false;
    }
    return true;
}

bool RithmicAdapter::cancel_order(const std::string& order_id) {
    if (!connected_ || !engine_) return false;

    auto* pEngine = static_cast<RApi::REngine*>(engine_);
    tsNCharcb sOrderNum = make_ts(order_id.c_str());
    tsNCharcb sEntryType = make_ts("M");
    tsNCharcb sTradingAlgorithm = make_ts("");
    tsNCharcb sUserMsg = make_ts("");
    tsNCharcb sWindowName = make_ts("");

    RApi::AccountInfo dummy_acct{};
    tsNCharcb sAccount = make_ts(account_id_.c_str());
    dummy_acct.sAccountId = sAccount;
    tsNCharcb sFcm = make_ts(fcm_id_.c_str());
    dummy_acct.sFcmId = sFcm;
    tsNCharcb sIb = make_ts(ib_id_.c_str());
    dummy_acct.sIbId = sIb;

    int iCode = 0;
    if (!pEngine->cancelOrder(&dummy_acct, &sOrderNum, &sEntryType,
                               &sTradingAlgorithm, &sUserMsg,
                               nullptr, &sWindowName, &iCode)) {
        std::cerr << "[RithmicAdapter] cancelOrder error: " << iCode << std::endl;
        return false;
    }
    return true;
}

} // namespace hft
