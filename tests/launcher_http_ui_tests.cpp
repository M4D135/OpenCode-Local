// Exercise the production HTTP reader against real loopback sockets, and the
// production logs action with a filesystem error. No model or security product
// is started or configured. The message box is captured only in this fixture.
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif
#include <winsock2.h>
#include <windows.h>
static int WINAPI CaptureMessageBoxW(HWND, LPCWSTR, LPCWSTR, UINT);
#define MessageBoxW CaptureMessageBoxW
#include "../src/launcher.cpp"
#undef MessageBoxW
#include <iostream>

static int capturedMessages = 0;
static int WINAPI CaptureMessageBoxW(HWND, LPCWSTR text, LPCWSTR, UINT) {
    if (text && *text) ++capturedMessages;
    return IDOK;
}

struct OneShotServer {
    SOCKET listener = INVALID_SOCKET;
    unsigned short port = 0;
    std::thread worker;
    explicit OneShotServer(std::string response) {
        listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (listener == INVALID_SOCKET) throw std::runtime_error("fixture socket failed");
        sockaddr_in address{};
        address.sin_family = AF_INET;
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        int bytes = sizeof(address);
        if (bind(listener, reinterpret_cast<sockaddr*>(&address), sizeof(address)) ||
            getsockname(listener, reinterpret_cast<sockaddr*>(&address), &bytes) ||
            listen(listener, 1)) {
            closesocket(listener); listener = INVALID_SOCKET;
            throw std::runtime_error("fixture listener failed");
        }
        port = ntohs(address.sin_port);
        worker = std::thread([this, response = std::move(response)] {
            fd_set readers;
            FD_ZERO(&readers); FD_SET(listener, &readers);
            timeval deadline{5, 0};
            if (select(0, &readers, nullptr, nullptr, &deadline) != 1) return;
            SOCKET client = accept(listener, nullptr, nullptr);
            if (client == INVALID_SOCKET) return;
            const DWORD timeout = 2000;
            setsockopt(client, SOL_SOCKET, SO_RCVTIMEO,
                       reinterpret_cast<const char*>(&timeout), sizeof(timeout));
            char block[2048];
            std::string request;
            while (request.find("\r\n\r\n") == std::string::npos && request.size() < 16384) {
                int count = recv(client, block, sizeof(block), 0);
                if (count <= 0) break;
                request.append(block, static_cast<size_t>(count));
            }
            size_t sent = 0;
            while (sent < response.size()) {
                int count = send(client, response.data() + sent,
                                 static_cast<int>(response.size() - sent), 0);
                if (count <= 0) break;
                sent += static_cast<size_t>(count);
            }
            shutdown(client, SD_SEND);
            closesocket(client);
        });
    }
    ~OneShotServer() {
        if (worker.joinable()) worker.join();
        if (listener != INVALID_SOCKET) closesocket(listener);
    }
};

int main() {
    WSADATA sockets{};
    if (WSAStartup(MAKEWORD(2, 2), &sockets)) {
        std::cout << "{\"passed\":false,\"checks\":[{\"name\":\"winsock\",\"passed\":false}]}\n";
        return 1;
    }
    std::vector<std::pair<std::string, bool>> checks;
    auto test = [&](const char* name, const std::function<bool()>& body) {
        bool passed = false;
        try { passed = body(); } catch (...) { }
        checks.emplace_back(name, passed);
    };
    test("complete_http_body", [] {
        OneShotServer server("HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}");
        auto result = LocalHttp(server.port, "test-session-key", L"GET", L"/health", {}, 1000);
        return result.status == 200 && result.body == "{}";
    });
    test("truncated_http_body_rejected", [] {
        OneShotServer server("HTTP/1.1 200 OK\r\nContent-Length: 512\r\nConnection: close\r\n\r\nchoices");
        auto result = LocalHttp(server.port, "test-session-key", L"GET", L"/v1/models", {}, 1000);
        return result.status == 0 && result.body.empty();
    });
    test("oversized_declared_body_rejected", [] {
        OneShotServer server("HTTP/1.1 200 OK\r\nContent-Length: 1048577\r\nConnection: close\r\n\r\n{}");
        auto result = LocalHttp(server.port, "test-session-key", L"GET", L"/health", {}, 1000);
        return result.status == 0 && result.body.empty();
    });
    test("body_without_content_length", [] {
        OneShotServer server("HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n{}");
        auto result = LocalHttp(server.port, "test-session-key", L"GET", L"/health", {}, 1000);
        return result.status == 200 && result.body == "{}";
    });
    test("redirect_not_followed", [] {
        OneShotServer server("HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:9/other\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
        auto result = LocalHttp(server.port, "test-session-key", L"GET", L"/health", {}, 1000);
        return result.status == 302 && result.body.empty();
    });
    fs::path fixture;
    bool created = false;
    test("logs_error_does_not_escape_window_action", [&] {
        wchar_t temporary[MAX_PATH + 1]{};
        const auto length = GetTempPathW(MAX_PATH, temporary);
        if (!length || length >= MAX_PATH) return false;
        fixture = fs::path(temporary) / (L"OpenCodeLocal-http-ui-" +
            std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(GetTickCount64()));
        created = fs::create_directory(fixture);
        if (!created) return false;
        g.paths.profile = fixture;
        g.paths.logs = fixture / L"logs";
        { std::ofstream file(g.paths.logs, std::ios::binary); file << "keep"; }
        OpenLogs();
        return capturedMessages == 1 && ReadSmallFile(g.paths.logs) == "keep";
    });
    if (created) {
        std::error_code error;
        fs::remove_all(fixture, error);
        checks.emplace_back("fixture_cleanup", !error && !fs::exists(fixture));
    }
    WSACleanup();
    const bool passed = std::all_of(checks.begin(), checks.end(), [](const auto& check) { return check.second; });
    std::cout << "{\"passed\":" << (passed ? "true" : "false") << ",\"checks\":[";
    for (size_t i = 0; i < checks.size(); ++i) {
        if (i) std::cout << ',';
        std::cout << "{\"name\":\"" << checks[i].first << "\",\"passed\":"
                  << (checks[i].second ? "true" : "false") << '}';
    }
    std::cout << "]}\n";
    return passed ? 0 : 1;
}
