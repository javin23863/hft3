#include "RApiPlus.h"
#include <stdio.h>
#include <string.h>
#include <unistd.h>

using namespace RApi;

bool g_bTsLoginComplete = false;
int g_aiCode = 0;
RApi::REngine* g_pEngine = 0;

class MyCallbacks2 : public RCallbacks {
public:
    int Alert(AlertInfo* pInfo, void*, int* aiCode) override {
        printf("[cb] Alert: type=%d connId=%d code=%d\n",
               pInfo->iAlertType, pInfo->iConnectionId, pInfo->iCode);
        if (pInfo->iAlertType == ALERT_LOGIN_COMPLETE &&
            pInfo->iConnectionId == TRADING_SYSTEM_CONNECTION_ID) {
            g_bTsLoginComplete = true;
        }
        *aiCode = API_OK; return OK;
    }
    int LoginComplete(LoginInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int ConnectionOpened(ConnectionInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int ConnectionClosed(ConnectionInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int ConnectionBroken(ConnectionInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int ServiceError(ServiceErrorInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int ForcedLogout(ForcedLogoutInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int QuietHeartbeat(QuietHeartbeatInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int ShutdownSignal(ShutdownSignalInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int ResetMd(int, int, int* aiCode) override { *aiCode = API_OK; return OK; }
    int LoginFailed(int, int, int* aiCode) override { *aiCode = API_OK; return OK; }
    int LineUpdate(LineInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int BestBidAsk(BestBidAskInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int TradePrint(TradeInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int StatusReport(OrderStatusReport*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int FillReport(OrderFillReport*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int ModifyReport(OrderModifyReport*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int CancelReport(OrderCancelReport*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int RejectReport(OrderRejectReport*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int FailureReport(OrderFailureReport*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int OtherReport(OrderReport*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int AccountList(AccountListInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int TradeRouteList(TradeRouteListInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int PriceIncrUpdate(PriceIncrInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int TradeRoute(TradeRouteInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int User(UserInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int UserList(UserListInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
    int UserProfile(UserProfileInfo*, void*, int* aiCode) override { *aiCode = API_OK; return OK; }
};

int main(int argc, char** argv, char** envp) {
    const char* user = argc > 1 ? argv[1] : getenv("USER");
    const char* pass = argc > 2 ? argv[2] : "";

    REngineParams eparams;
    eparams.sAppName = {(char*)"Test",4};
    eparams.sAppVersion = {(char*)"1.0",3};
    eparams.envp = envp;
    eparams.sLogFilePath = {(char*)"/tmp/test_login.log",19};

    MyCallbacks2 adm;
    eparams.pAdmCallbacks = &adm;

    REngine* pEngine = new REngine(&eparams);
    g_pEngine = pEngine;
    printf("[main] REngine created\n");

    MyCallbacks2 callbacks;

    LoginParams lparams;
    lparams.pCallbacks = &callbacks;
    lparams.sMdUser = {(char*)user, (int)strlen(user)};
    lparams.sMdPassword = {(char*)pass, (int)strlen(pass)};
    lparams.sMdCnnctPt = {(char*)"login_agent_tpc", 16};
    lparams.sTsUser = {(char*)user, (int)strlen(user)};
    lparams.sTsPassword = {(char*)pass, (int)strlen(pass)};
    lparams.sTsCnnctPt = {(char*)"login_agent_opc", 16};

    int iCode = 0;
    bool ok = pEngine->login(&lparams, &iCode);
    printf("[main] login() returned %s, iCode=%d\n", ok ? "true" : "false", iCode);

    if (ok) {
        int wait = 0;
        while (!g_bTsLoginComplete && wait < 100) {
            usleep(100000);
            wait++;
        }
        printf("[main] g_bTsLoginComplete=%s after %d00ms\n",
               g_bTsLoginComplete ? "true" : "false", wait);
        pEngine->logout(&iCode);
    }

    delete pEngine;
    printf("[main] done\n");
    return 0;
}
