#include "rithmic_adapter.hpp"

#include <iostream>
#include <cstring>
#include <chrono>

#include "RApiPlus.h"

namespace hft {

class MyAdmCallbacks : public RApi::AdmCallbacks {
public:
    MyAdmCallbacks() = default;
    ~MyAdmCallbacks() override = default;

    int Alert(RApi::AlertInfo* pInfo, void* pContext, int* aiCode) override {
        (void)pContext;
        int ignored;
        pInfo->dump(&ignored);
        *aiCode = API_OK;
        return OK;
    }
};

class MyCallbacks : public RApi::RCallbacks {
public:
    explicit MyCallbacks(RithmicAdapter* adapter)
        : adapter_(adapter) {}
    ~MyCallbacks() override = default;

    int Alert(RApi::AlertInfo* pInfo, void* pContext, int* aiCode) override {
        (void)pContext;
        int ignored;
        pInfo->dump(&ignored);

        if (pInfo->iConnectionId == RApi::REPOSITORY_CONNECTION_ID) {
            if (pInfo->iAlertType == RApi::ALERT_LOGIN_COMPLETE) {
                adapter_->rep_login_status_ = RithmicAdapter::LOGIN_COMPLETE;
            } else if (pInfo->iAlertType == RApi::ALERT_LOGIN_FAILED) {
                adapter_->rep_login_status_ = RithmicAdapter::LOGIN_FAILED;
            }
        }

        if (pInfo->iConnectionId == RApi::MARKET_DATA_CONNECTION_ID) {
            if (pInfo->iAlertType == RApi::ALERT_LOGIN_COMPLETE) {
                adapter_->md_login_status_ = RithmicAdapter::LOGIN_COMPLETE;
            } else if (pInfo->iAlertType == RApi::ALERT_LOGIN_FAILED) {
                adapter_->md_login_status_ = RithmicAdapter::LOGIN_FAILED;
            }
        }

        adapter_->login_cv_.notify_all();
        *aiCode = API_OK;
        return OK;
    }

    int AgreementList(RApi::AgreementListInfo* pInfo, void* pContext, int* aiCode) override {
        (void)pContext;
        if (!pInfo->bAccepted) {
            for (int i = 0; i < pInfo->iArrayLen; i++) {
                RApi::AgreementInfo ag = pInfo->asAgreementInfoArray[i];
                tsNCharcb active = {const_cast<char*>("active"), 6};
                bool is_active = (ag.sStatus.iDataLen == active.iDataLen &&
                    memcmp(ag.sStatus.pData, active.pData, ag.sStatus.iDataLen) == 0);
                if (ag.bMandatory && is_active) {
                    adapter_->unaccepted_mandatory_agreements_++;
                }
            }
            adapter_->agreements_received_ = true;
        }
        *aiCode = API_OK;
        return OK;
    }

    int TradePrint(RApi::TradeInfo* pInfo, void* pContext, int* aiCode) override {
        (void)pContext;
        MarketDataEvent evt{};
        evt.timestamp_ns = static_cast<uint64_t>(pInfo->iSsboe) * 1000000000ULL
                         + static_cast<uint64_t>(pInfo->iUsecs) * 1000ULL;
        evt.price = pInfo->dPrice;
        evt.size = static_cast<int32_t>(pInfo->llSize);
        evt.action = 'T';

        if (pInfo->sAggressorSide.iDataLen > 0) {
            evt.side = pInfo->sAggressorSide.pData[0];
        } else {
            evt.side = ' ';
        }

        if (!adapter_->mbo_queue_->push(evt)) {
            std::cerr << "[CRITICAL] MBO Queue overrun on TradePrint!" << std::endl;
        }

        *aiCode = API_OK;
        return OK;
    }

    int BestBidAskQuote(RApi::BidInfo* pBid, RApi::AskInfo* pAsk,
                        void* pContext, int* aiCode) override {
        (void)pContext;

        if (pBid && pBid->bPriceFlag) {
            MarketDataEvent evt{};
            evt.timestamp_ns = static_cast<uint64_t>(pBid->iSsboe) * 1000000000ULL
                             + static_cast<uint64_t>(pBid->iUsecs) * 1000ULL;
            evt.price = pBid->dPrice;
            evt.size = static_cast<int32_t>(pBid->llSize);
            evt.action = 'M';
            evt.side = 'B';
            evt.order_id = 0;
            adapter_->mbo_queue_->push(evt);
        }

        if (pAsk && pAsk->bPriceFlag) {
            MarketDataEvent evt{};
            evt.timestamp_ns = static_cast<uint64_t>(pAsk->iSsboe) * 1000000000ULL
                             + static_cast<uint64_t>(pAsk->iUsecs) * 1000ULL;
            evt.price = pAsk->dPrice;
            evt.size = static_cast<int32_t>(pAsk->llSize);
            evt.action = 'M';
            evt.side = 'A';
            evt.order_id = 0;
            adapter_->mbo_queue_->push(evt);
        }

        *aiCode = API_OK;
        return OK;
    }

    int LineUpdate(RApi::LineInfo* pInfo, void* pContext, int* aiCode) override {
        (void)pContext;
        int ignored;
        pInfo->dump(&ignored);
        *aiCode = API_OK;
        return OK;
    }

    int FillReport(RApi::OrderFillReport* pReport, void* pContext, int* aiCode) override {
        (void)pContext;
        int ignored;
        pReport->dump(&ignored);
        *aiCode = API_OK;
        return OK;
    }

    int StatusReport(RApi::OrderStatusReport* pReport, void* pContext, int* aiCode) override {
        (void)pContext;
        int ignored;
        pReport->dump(&ignored);
        *aiCode = API_OK;
        return OK;
    }

    int CancelReport(RApi::OrderCancelReport* pReport, void* pContext, int* aiCode) override {
        (void)pContext;
        int ignored;
        pReport->dump(&ignored);
        *aiCode = API_OK;
        return OK;
    }

    int ModifyReport(RApi::OrderModifyReport* pReport, void* pContext, int* aiCode) override {
        (void)pContext;
        int ignored;
        pReport->dump(&ignored);
        *aiCode = API_OK;
        return OK;
    }

    int RejectReport(RApi::OrderRejectReport* pReport, void* pContext, int* aiCode) override {
        (void)pContext;
        int ignored;
        pReport->dump(&ignored);
        *aiCode = API_OK;
        return OK;
    }

    int FailureReport(RApi::OrderFailureReport* pReport, void* pContext, int* aiCode) override {
        (void)pContext;
        int ignored;
        pReport->dump(&ignored);
        *aiCode = API_OK;
        return OK;
    }

    int AccountList(RApi::AccountListInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int AccountUpdate(RApi::AccountUpdateInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int Aggregator(RApi::AggregatorInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int AskQuote(RApi::AskInfo* pInfo, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int AssignedUserList(RApi::AssignedUserListInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int AutoLiquidate(RApi::AutoLiquidateInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
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
    int BustReport(RApi::OrderBustReport*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
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
    int NotCancelledReport(RApi::OrderNotCancelledReport*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int NotModifiedReport(RApi::OrderNotModifiedReport*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
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
    int PriceIncrUpdate(RApi::PriceIncrInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
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
    int TradeRouteList(RApi::TradeRouteListInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
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

RithmicAdapter::RithmicAdapter(const ConnectionConfig& config,
                               SPSCQueue<MarketDataEvent, 8192>* mbo_queue)
    : config_(config)
    , mbo_queue_(mbo_queue)
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
    env_strings_.reserve(config_.env_vars.size() + 1);
    for (auto& v : config_.env_vars) {
        env_strings_.push_back(const_cast<char*>(v.c_str()));
    }
    env_strings_.push_back(nullptr);
}

void RithmicAdapter::cleanup_envp() {
    env_strings_.clear();
}

bool RithmicAdapter::initialize() {
    try {
        auto* adm = new MyAdmCallbacks();
        adm_callbacks_ = adm;
        return true;
    } catch (OmneException& ex) {
        std::cerr << "[RithmicAdapter] AdmCallbacks creation error: " << ex.getErrorCode() << std::endl;
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
        std::cerr << "[RithmicAdapter] REngine creation error: " << ex.getErrorCode() << std::endl;
        return false;
    }
    engine_ = pEngine;

    auto* callbacks = new MyCallbacks(this);
    callbacks_ = callbacks;

    rep_login_status_ = LOGIN_NOT_LOGGED_IN;
    md_login_status_ = LOGIN_NOT_LOGGED_IN;
    agreements_received_ = false;
    unaccepted_mandatory_agreements_ = 0;

    tsNCharcb rep_env_key = make_ts("system");
    tsNCharcb rep_user = make_ts(config_.username.c_str());
    tsNCharcb rep_password = make_ts(config_.password.c_str());
    tsNCharcb rep_cnnct_pt = make_ts(config_.rep_connect_point.c_str());

    int iCode = 0;
    if (!pEngine->loginRepository(&rep_env_key, &rep_user, &rep_password,
                                   &rep_cnnct_pt, callbacks, &iCode)) {
        std::cerr << "[RithmicAdapter] loginRepository error: " << iCode << std::endl;
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

    if (rep_login_status_ == LOGIN_FAILED) {
        std::cerr << "[RithmicAdapter] Repository login failed" << std::endl;
        delete static_cast<RApi::REngine*>(engine_);
        delete static_cast<MyCallbacks*>(callbacks_);
        engine_ = nullptr;
        callbacks_ = nullptr;
        return false;
    }

    if (!pEngine->listAgreements(false, nullptr, &iCode)) {
        std::cerr << "[RithmicAdapter] listAgreements error: " << iCode << std::endl;
        int ignored;
        pEngine->logoutRepository(&ignored);
        delete pEngine;
        delete callbacks;
        engine_ = nullptr;
        callbacks_ = nullptr;
        return false;
    }

    {
        auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
        std::unique_lock<std::mutex> lk(login_mutex_);
        login_cv_.wait_until(lk, deadline, [&] { return agreements_received_.load(); });
    }

    if (unaccepted_mandatory_agreements_ > 0) {
        std::cerr << "[RithmicAdapter] " << unaccepted_mandatory_agreements_.load()
                  << " unaccepted mandatory agreements. Log in via R|Trader to accept." << std::endl;
        int ignored;
        pEngine->logoutRepository(&ignored);
        delete pEngine;
        delete callbacks;
        engine_ = nullptr;
        callbacks_ = nullptr;
        return false;
    }

    if (!pEngine->logoutRepository(&iCode)) {
        std::cerr << "[RithmicAdapter] logoutRepository error: " << iCode << std::endl;
    }

    RApi::LoginParams login_params;
    login_params.pCallbacks = callbacks;

    login_params.sMdUser = make_ts(config_.username.c_str());
    login_params.sMdPassword = make_ts(config_.password.c_str());
    login_params.sMdCnnctPt = make_ts(config_.md_connect_point.c_str());

    login_params.sTsUser = make_ts(config_.username.c_str());
    login_params.sTsPassword = make_ts(config_.password.c_str());
    login_params.sTsCnnctPt = make_ts(config_.ts_connect_point.c_str());

    md_login_status_ = LOGIN_NOT_LOGGED_IN;

    if (!pEngine->login(&login_params, &iCode)) {
        std::cerr << "[RithmicAdapter] login error: " << iCode << std::endl;
        delete pEngine;
        delete callbacks;
        engine_ = nullptr;
        callbacks_ = nullptr;
        return false;
    }

    {
        std::unique_lock<std::mutex> lk(login_mutex_);
        login_cv_.wait_for(lk, std::chrono::seconds(30), [&] {
            return md_login_status_ == LOGIN_COMPLETE || md_login_status_ == LOGIN_FAILED;
        });
    }

    if (md_login_status_ == LOGIN_FAILED) {
        std::cerr << "[RithmicAdapter] MD/TS login failed" << std::endl;
        delete pEngine;
        delete callbacks;
        engine_ = nullptr;
        callbacks_ = nullptr;
        return false;
    }

    connected_ = true;
    logged_in_ = true;
    std::cout << "[RithmicAdapter] Connected to " << config_.environment << " as " << config_.username << std::endl;
    return true;
}

void RithmicAdapter::disconnect() {
    if (engine_) {
        auto* pEngine = static_cast<RApi::REngine*>(engine_);
        if (logged_in_) {
            int iCode = 0;
            pEngine->logout(&iCode);
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

bool RithmicAdapter::send_order(const std::string& symbol, char side, int32_t qty, double price) {
    if (!connected_ || !engine_) return false;

    auto* pEngine = static_cast<RApi::REngine*>(engine_);

    RApi::LimitOrderParams params;
    params.pAccount = nullptr;
    params.sExchange = make_ts("CME");
    params.sTicker = make_ts(symbol.c_str());
    params.sBuySellType = make_ts(side == 'B' ? "Buy" : "Sell");
    params.sDuration = make_ts("Day");
    params.sEntryType = make_ts("Limit");
    params.sTradingAlgorithm = make_ts("System");
    params.llQty = static_cast<long long>(qty);
    params.dPrice = price;
    params.sTradeRoute = make_ts("");
    params.sRoutingInstructions = make_ts("");
    params.sTag = make_ts("");
    params.sUserMsg = make_ts("");
    params.pContext = nullptr;

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
    tsNCharcb sEntryType = make_ts("Limit");
    tsNCharcb sTradingAlgorithm = make_ts("System");
    tsNCharcb sUserMsg = make_ts("");
    tsNCharcb sWindowName = make_ts("");

    int iCode = 0;
    if (!pEngine->cancelOrder(nullptr, &sOrderNum, &sEntryType,
                               &sTradingAlgorithm, &sUserMsg,
                               nullptr, &sWindowName, &iCode)) {
        std::cerr << "[RithmicAdapter] cancelOrder error: " << iCode << std::endl;
        return false;
    }
    return true;
}

} // namespace hft