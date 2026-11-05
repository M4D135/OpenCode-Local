#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif

#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <commctrl.h>
#include <commdlg.h>
#include <shlobj.h>
#include <shobjidl.h>
#include <shellapi.h>
#include <winhttp.h>
#include <bcrypt.h>
#include <iphlpapi.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iterator>
#include <map>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "pack_config.h"

// Native Windows launcher. No scripts, elevation, downloaded runtime, or
// external HTTP endpoint is needed. Only the owned localhost server is used.
namespace fs = std::filesystem;

namespace {

constexpr const wchar_t* kProduct = PackConfig::DisplayName;
constexpr const wchar_t* kVersion = PackConfig::VersionW;
constexpr const wchar_t* kClass = PackConfig::ProfileName;
constexpr const wchar_t* kModelFile = PackConfig::ModelFileW;
constexpr const wchar_t* kAlias = PackConfig::ModelAliasW;
constexpr uint64_t kModelBytes = PackConfig::ModelBytes;
constexpr UINT WM_STATUS = WM_APP + 1;
constexpr UINT WM_FINISHED = WM_APP + 2;
constexpr int kDefaultContext = PackConfig::DefaultContext;
constexpr int kOutputTokens = PackConfig::OutputTokens;
constexpr size_t kContextCount = std::size(PackConfig::AllowedContexts);
static_assert(kContextCount > 0, "At least one context size is required");

constexpr bool IsAllowedContext(int context) {
    for (int allowed : PackConfig::AllowedContexts)
        if (context == allowed) return true;
    return false;
}
static_assert(IsAllowedContext(kDefaultContext), "Default context must be offered by this pack");
static_assert(PackConfig::MemoryReserveMB >= 0 && PackConfig::RuntimeOverheadMB >= 0 &&
              PackConfig::CacheMemoryPer4096MB > 0, "Memory estimates must be non-negative");
static_assert(PackConfig::MicroBatchSize > 0 && PackConfig::BatchSize >= PackConfig::MicroBatchSize &&
              PackConfig::MaxCpuThreads > 0, "Invalid CPU inference settings");

enum Control : int {
    ID_PROJECT = 101, ID_BROWSE, ID_START, ID_DIRECT, ID_STOP,
    ID_DIAGNOSTIC, ID_LOGS, ID_GUIDE, ID_THEME, ID_CONTEXT, ID_PROGRESS
};

struct Handle {
    HANDLE value = nullptr;
    Handle() = default;
    explicit Handle(HANDLE h) : value(h) {}
    ~Handle() { reset(); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
    Handle(Handle&& other) noexcept : value(other.value) { other.value = nullptr; }
    Handle& operator=(Handle&& other) noexcept {
        if (this != &other) { reset(); value = other.value; other.value = nullptr; }
        return *this;
    }
    bool valid() const { return value && value != INVALID_HANDLE_VALUE; }
    void reset(HANDLE h = nullptr) {
        if (valid()) CloseHandle(value);
        value = h;
    }
};

struct InternetHandle {
    HINTERNET value = nullptr;
    explicit InternetHandle(HINTERNET h = nullptr) : value(h) {}
    ~InternetHandle() { if (value) WinHttpCloseHandle(value); }
    InternetHandle(const InternetHandle&) = delete;
    InternetHandle& operator=(const InternetHandle&) = delete;
};

struct Cancelled : std::exception {
    const char* what() const noexcept override { return "Session arretee"; }
};

struct LayoutPaths {
    fs::path install, profile, runtime, config, cache, data, state, logs;
    fs::path engine, completion, model, opencode, tools, configTemplate, seed;
};

struct SessionResult {
    bool error = false;
    std::wstring message;
};

struct App {
    HINSTANCE instance = nullptr;
    HWND window = nullptr;
    HWND project = nullptr, browse = nullptr, start = nullptr, direct = nullptr;
    HWND stop = nullptr, diagnostic = nullptr, logs = nullptr, guide = nullptr;
    HWND theme = nullptr, context = nullptr, progress = nullptr;
    HFONT font = nullptr, titleFont = nullptr, smallFont = nullptr, boldFont = nullptr;
    HBRUSH backgroundBrush = nullptr, panelBrush = nullptr, editBrush = nullptr;
    LayoutPaths paths;
    fs::path projectPath;
    std::wstring status = L"Prêt. Choisissez votre dossier de projet.";
    std::wstring subtitle = std::wstring(PackConfig::ModelLabel) + L" — modèle et outils embarqués.";
    int dpi = 96;
    int selectedContext = kDefaultContext;
    bool dark = true, statusError = false, pendingClose = false;
    std::atomic<bool> busy{false}, stopRequested{false};
    std::atomic<unsigned short> activePort{0};
    std::mutex jobMutex;
    HANDLE job = nullptr; // Worker owns it; UI can request termination only.
    std::thread worker;
    Handle profileLock;
};

App g;

std::string Utf8(const std::wstring& s) {
    if (s.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, s.data(),
                               static_cast<int>(s.size()), nullptr, 0, nullptr, nullptr);
    if (!n) throw std::runtime_error("Texte Unicode invalide.");
    std::string out(static_cast<size_t>(n), '\0');
    WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, s.data(),
                        static_cast<int>(s.size()), out.data(), n, nullptr, nullptr);
    return out;
}

std::wstring Wide(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s.data(),
                              static_cast<int>(s.size()), nullptr, 0);
    if (!n) throw std::runtime_error("Un fichier texte n'est pas en UTF-8 valide.");
    std::wstring out(static_cast<size_t>(n), L'\0');
    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s.data(),
                       static_cast<int>(s.size()), out.data(), n);
    return out;
}

std::wstring WinError(DWORD code = GetLastError()) {
    LPWSTR buffer = nullptr;
    DWORD n = FormatMessageW(FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM |
                            FORMAT_MESSAGE_IGNORE_INSERTS, nullptr, code, 0,
                            reinterpret_cast<LPWSTR>(&buffer), 0, nullptr);
    std::wstring result = n && buffer ? std::wstring(buffer, n) : L"Erreur Windows";
    if (buffer) LocalFree(buffer);
    while (!result.empty() && (result.back() == L'\r' || result.back() == L'\n' || result.back() == L' '))
        result.pop_back();
    return result + L" (" + std::to_wstring(code) + L")";
}

[[noreturn]] void Fail(const std::wstring& context, DWORD code = GetLastError()) {
    throw std::runtime_error(Utf8(context + L" : " + WinError(code)));
}

fs::path ExePath() {
    std::vector<wchar_t> buffer(1024);
    for (;;) {
        DWORD n = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
        if (!n) Fail(L"Impossible de localiser l'application");
        if (n < buffer.size() - 1) return fs::path(std::wstring(buffer.data(), n));
        if (buffer.size() > 32768) throw std::runtime_error("Chemin de l'application trop long.");
        buffer.resize(buffer.size() * 2);
    }
}

fs::path KnownFolder(REFKNOWNFOLDERID id) {
    PWSTR value = nullptr;
    HRESULT hr = SHGetKnownFolderPath(id, KF_FLAG_DEFAULT, nullptr, &value);
    if (FAILED(hr)) throw std::runtime_error("Impossible de localiser le dossier utilisateur Windows.");
    fs::path p(value);
    CoTaskMemFree(value);
    return p;
}

void MakeDirectory(const fs::path& p) {
    std::error_code ec;
    fs::create_directories(p, ec);
    if (ec || !fs::is_directory(p))
        throw std::runtime_error(Utf8(L"Impossible de créer le dossier : " + p.wstring()));
}

LayoutPaths FindPaths() {
    LayoutPaths p;
    p.install = ExePath().parent_path();
    p.profile = KnownFolder(FOLDERID_LocalAppData) / PackConfig::ProfileName;
    p.runtime = p.profile / L"runtime" / kVersion;
    p.config = p.runtime / L"config";
    p.cache = p.runtime / L"cache";
    p.data = p.profile / L"data";
    p.state = p.profile / L"state";
    p.logs = p.profile / L"logs";
    p.engine = p.install / L"wad" / L"engine" / L"llama-server.exe";
    p.completion = p.install / L"wad" / L"engine" / L"llama-completion.exe";
    p.model = p.install / L"wad" / L"model" / kModelFile;
    p.opencode = p.install / L"wad" / L"opencode" / L"opencode.exe";
    p.tools = p.install / L"wad" / L"tools";
    p.configTemplate = p.install / L"wad" / L"config" / L"opencode.json";
    p.seed = p.install / L"wad" / L"offline-cache";
    return p;
}

std::string ReadSmallFile(const fs::path& p, size_t maxBytes = 2 * 1024 * 1024) {
    std::error_code ec;
    const auto size = fs::file_size(p, ec);
    if (ec || size > maxBytes)
        throw std::runtime_error(Utf8(L"Fichier absent, illisible ou trop volumineux : " + p.wstring()));
    std::ifstream in(p, std::ios::binary);
    if (!in) throw std::runtime_error(Utf8(L"Impossible de lire : " + p.wstring()));
    std::string text(static_cast<size_t>(size), '\0');
    if (size) in.read(text.data(), static_cast<std::streamsize>(size));
    if (!in && size) throw std::runtime_error(Utf8(L"Lecture incomplète : " + p.wstring()));
    return text;
}

void WriteAtomic(const fs::path& path, const std::string& text) {
    MakeDirectory(path.parent_path());
    fs::path temp = path;
    temp += L".tmp." + std::to_wstring(GetCurrentProcessId());
    Handle file(CreateFileW(temp.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
                            FILE_ATTRIBUTE_NORMAL, nullptr));
    if (!file.valid()) Fail(L"Impossible d'écrire " + temp.wstring());
    size_t offset = 0;
    while (offset < text.size()) {
        DWORD written = 0;
        DWORD wanted = static_cast<DWORD>(std::min<size_t>(text.size() - offset, 1024 * 1024));
        if (!WriteFile(file.value, text.data() + offset, wanted, &written, nullptr) || written != wanted) {
            DWORD error = GetLastError();
            file.reset(); DeleteFileW(temp.c_str());
            Fail(L"Écriture interrompue de " + path.wstring(), error);
        }
        offset += written;
    }
    if (!FlushFileBuffers(file.value)) {
        DWORD error = GetLastError(); file.reset(); DeleteFileW(temp.c_str());
        Fail(L"Écriture incomplète de " + path.wstring(), error);
    }
    file.reset();
    if (!MoveFileExW(temp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        DWORD error = GetLastError(); DeleteFileW(temp.c_str());
        Fail(L"Impossible de finaliser " + path.wstring(), error);
    }
}

std::wstring TimeStamp() {
    SYSTEMTIME st{}; GetLocalTime(&st);
    wchar_t out[64];
    swprintf(out, 64, L"%04u%02u%02u-%02u%02u%02u-%03u", st.wYear, st.wMonth,
             st.wDay, st.wHour, st.wMinute, st.wSecond, st.wMilliseconds);
    return out;
}

void Log(const std::wstring& message) {
    try {
        MakeDirectory(g.paths.logs);
        const std::string text = Utf8(TimeStamp() + L"  " + message + L"\r\n");
        Handle file(CreateFileW((g.paths.logs / L"launcher.log").c_str(), FILE_APPEND_DATA,
                               FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_ALWAYS,
                               FILE_ATTRIBUTE_NORMAL, nullptr));
        if (file.valid()) { DWORD written; WriteFile(file.value, text.data(), static_cast<DWORD>(text.size()), &written, nullptr); }
    } catch (...) { /* Logging must never turn successful cleanup into a failure. */ }
}

void Status(const std::wstring& text) {
    if (!g.window) { Log(text); return; }
    auto* value = new std::wstring(text);
    if (!PostMessageW(g.window, WM_STATUS, 0, reinterpret_cast<LPARAM>(value))) delete value;
}

void CheckStop() { if (g.stopRequested.load()) throw Cancelled(); }

std::wstring QuoteArgument(const std::wstring& input) {
    // CommandLineToArgvW / CRT-compatible quoting, including trailing slashes.
    std::wstring out = L"\"";
    size_t slashes = 0;
    for (wchar_t c : input) {
        if (c == L'\\') { ++slashes; continue; }
        if (c == L'\"') { out.append(slashes * 2 + 1, L'\\'); out.push_back(c); }
        else { out.append(slashes, L'\\'); out.push_back(c); }
        slashes = 0;
    }
    out.append(slashes * 2, L'\\');
    out.push_back(L'\"');
    return out;
}

std::wstring CommandLine(const fs::path& executable, const std::vector<std::wstring>& args) {
    std::wstring out = QuoteArgument(executable.wstring());
    for (const auto& arg : args) { out.push_back(L' '); out += QuoteArgument(arg); }
    return out;
}

struct EnvLess {
    bool operator()(const std::wstring& a, const std::wstring& b) const {
        return CompareStringOrdinal(a.c_str(), -1, b.c_str(), -1, TRUE) == CSTR_LESS_THAN;
    }
};
using Environment = std::map<std::wstring, std::wstring, EnvLess>;

Environment CurrentEnvironment() {
    Environment env;
    LPWCH strings = GetEnvironmentStringsW();
    if (!strings) Fail(L"Impossible de préparer l'environnement");
    for (const wchar_t* entry = strings; *entry; entry += wcslen(entry) + 1) {
        std::wstring value(entry);
        size_t split = value.find(L'=', value[0] == L'=' ? 1 : 0);
        if (split != std::wstring::npos) env[value.substr(0, split)] = value.substr(split + 1);
    }
    FreeEnvironmentStringsW(strings);
    return env;
}

std::vector<wchar_t> EnvironmentBlock(const Environment& env) {
    std::vector<wchar_t> block;
    for (const auto& entry : env) {
        std::wstring value = entry.first + L"=" + entry.second;
        block.insert(block.end(), value.begin(), value.end());
        block.push_back(L'\0');
    }
    block.push_back(L'\0');
    if (block.size() == 1) block.push_back(L'\0');
    return block;
}

std::string ReplaceAll(std::string value, const std::string& from, const std::string& to) {
    size_t pos = 0;
    while ((pos = value.find(from, pos)) != std::string::npos) {
        value.replace(pos, from.size(), to); pos += to.size();
    }
    return value;
}

std::string JsonEscape(const std::wstring& text) {
    std::string output;
    for (unsigned char c : Utf8(text)) {
        if (c == '\\') output += "\\\\";
        else if (c == '"') output += "\\\"";
        else if (c < 32) {
            const char hex[] = "0123456789abcdef";
            output += "\\u00"; output += hex[c >> 4]; output += hex[c & 15];
        } else output += static_cast<char>(c);
    }
    return output;
}

std::string RandomToken() {
    std::array<unsigned char, 32> bytes{};
    if (BCryptGenRandom(nullptr, bytes.data(), static_cast<ULONG>(bytes.size()), BCRYPT_USE_SYSTEM_PREFERRED_RNG) != 0)
        throw std::runtime_error("Impossible de créer la clé de la session locale.");
    constexpr char hex[] = "0123456789abcdef";
    std::string value;
    for (unsigned char c : bytes) { value.push_back(hex[c >> 4]); value.push_back(hex[c & 15]); }
    return value;
}

void ValidateFiles(bool directOnly = false) {
    if (Utf8(PackConfig::ModelFileW) != PackConfig::ModelFile ||
        Utf8(PackConfig::ModelAliasW) != PackConfig::ModelAlias)
        throw std::runtime_error("L’identité du modèle de ce pack est incohérente. Réinstallez le pack complet.");
    const std::vector<fs::path> files = directOnly
        ? std::vector<fs::path>{g.paths.completion, g.paths.model}
        : std::vector<fs::path>{g.paths.engine, g.paths.completion, g.paths.model,
                               g.paths.opencode, g.paths.tools / L"rg.exe", g.paths.configTemplate};
    for (const auto& file : files) {
        std::error_code ec;
        if (!fs::is_regular_file(file, ec) || fs::file_size(file, ec) == 0)
            throw std::runtime_error(Utf8(L"Fichier requis absent ou vide : " + file.wstring() +
                                         L". Relancez Setup.exe depuis le pack complet."));
    }
    std::error_code ec;
    if (fs::file_size(g.paths.model, ec) != kModelBytes)
        throw std::runtime_error("Le fichier du modèle est incomplet ou différent du modèle livré. Relancez Setup.exe.");
    std::ifstream model(g.paths.model, std::ios::binary);
    char header[8]{}; model.read(header, sizeof(header));
    const uint32_t ggufVersion = static_cast<unsigned char>(header[4]) |
        (static_cast<uint32_t>(static_cast<unsigned char>(header[5])) << 8) |
        (static_cast<uint32_t>(static_cast<unsigned char>(header[6])) << 16) |
        (static_cast<uint32_t>(static_cast<unsigned char>(header[7])) << 24);
    if (!model || std::string(header, header + 4) != "GGUF" ||
        (ggufVersion != 2 && ggufVersion != 3))
        throw std::runtime_error("Le modèle n'a pas un en-tête GGUF valide. Relancez Setup.exe.");
}

struct MemoryBudget {
    uint64_t installedMB = 0;
    uint64_t usableMB = 0;
    uint64_t availableMB = 0;
    bool physicalInventory = false;
};

MemoryBudget ReadMemoryBudget() {
    MEMORYSTATUSEX memory{};
    memory.dwLength = sizeof(memory);
    if (!GlobalMemoryStatusEx(&memory)) Fail(L"Impossible de vérifier la mémoire disponible");
    MemoryBudget budget;
    budget.usableMB = memory.ullTotalPhys / (1024ULL * 1024);
    budget.availableMB = memory.ullAvailPhys / (1024ULL * 1024);
    ULONGLONG installedKB = 0;
    budget.physicalInventory = GetPhysicallyInstalledSystemMemory(&installedKB) && installedKB > 0;
    // Firmware and integrated graphics can reserve part of the installed RAM.
    // Prefer installed RAM, falling back to usable RAM with the pack's margin.
    budget.installedMB = budget.physicalInventory ? installedKB / 1024 : budget.usableMB;
    return budget;
}

std::wstring MemoryGiB(uint64_t megabytes) {
    std::wostringstream value;
    value << std::fixed << std::setprecision(1) << static_cast<double>(megabytes) / 1024.0;
    std::wstring text = value.str();
    std::replace(text.begin(), text.end(), L'.', L',');
    return text;
}

uint64_t EstimatedSessionMemoryMB(int context) {
    if (!IsAllowedContext(context))
        throw std::runtime_error("La taille de contexte n’est pas proposée par ce pack.");
    // Count every expert's mapped weights, not just the experts active for a
    // token. The allowance also covers the engine, OpenCode and four recurrent
    // checkpoints. This is a startup estimate, not a Windows memory limit.
    const uint64_t weights = (kModelBytes + 1024ULL * 1024 - 1) / (1024ULL * 1024);
    const uint64_t contextBlocks = (static_cast<uint64_t>(context) + 4095) / 4096;
    return weights + static_cast<uint64_t>(PackConfig::RuntimeOverheadMB) +
           contextBlocks * static_cast<uint64_t>(PackConfig::CacheMemoryPer4096MB);
}

uint64_t RequiredAvailableMemoryMB(int context) {
    return EstimatedSessionMemoryMB(context) + static_cast<uint64_t>(PackConfig::MemoryReserveMB);
}

void CheckMemoryBudget(int context) {
    const MemoryBudget memory = ReadMemoryBudget();
    const uint64_t estimatedMB = EstimatedSessionMemoryMB(context);
    const uint64_t requiredMB = RequiredAvailableMemoryMB(context);
    if (memory.installedMB < static_cast<uint64_t>(PackConfig::MinInstalledMemoryMB))
        throw std::runtime_error(Utf8(std::wstring(L"Mémoire insuffisante pour ") + kProduct +
            L" : " + MemoryGiB(memory.installedMB) + L" Go détectés, " +
            MemoryGiB(PackConfig::MinInstalledMemoryMB) + L" Go installés nécessaires."));
    if (memory.availableMB < requiredMB)
        throw std::runtime_error(Utf8(L"RAM disponible : " + MemoryGiB(memory.availableMB) +
            L" Go ; " + MemoryGiB(requiredMB) + L" Go nécessaires à " + std::to_wstring(context) +
            L" tokens, dont " + MemoryGiB(PackConfig::MemoryReserveMB) +
            L" Go de marge pour vos autres logiciels. Fermez des applications puis réessayez."));
    Log(L"Budget RAM au démarrage : disponible=" + MemoryGiB(memory.availableMB) +
        L" Go, session estimée=" + MemoryGiB(estimatedMB) + L" Go, réserve=" +
        MemoryGiB(PackConfig::MemoryReserveMB) + L" Go. Estimation, sans plafond système imposé.");
}

struct CpuBudget {
    unsigned logical = 1;
    unsigned physical = 0;
    int threads = 1;
};

int InferenceThreadCount(unsigned physical, unsigned logical) {
    const unsigned available = std::max(1U, physical ? physical : logical);
    const unsigned headroom = available >= 4 ? 1U : 0U;
    return static_cast<int>(std::min(available - headroom,
                                    static_cast<unsigned>(PackConfig::MaxCpuThreads)));
}

CpuBudget ReadCpuBudget() {
    CpuBudget budget;
    SYSTEM_INFO system{};
    GetNativeSystemInfo(&system);
    budget.logical = std::max(1U, static_cast<unsigned>(system.dwNumberOfProcessors));
    DWORD size = 0;
    GetLogicalProcessorInformationEx(RelationProcessorCore, nullptr, &size);
    if (size >= 2 * sizeof(DWORD) && size <= 4 * 1024 * 1024) {
        std::vector<unsigned char> data(size);
        auto first = reinterpret_cast<PSYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX>(data.data());
        if (GetLogicalProcessorInformationEx(RelationProcessorCore, first, &size)) {
            size_t offset = 0;
            unsigned cores = 0;
            while (offset + 2 * sizeof(DWORD) <= size) {
                auto entry = reinterpret_cast<PSYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX>(data.data() + offset);
                if (entry->Size < 2 * sizeof(DWORD) || entry->Size > size - offset) break;
                if (entry->Relationship == RelationProcessorCore) ++cores;
                offset += entry->Size;
            }
            if (offset == size) budget.physical = cores;
        }
    }
    budget.threads = InferenceThreadCount(budget.physical, budget.logical);
    return budget;
}

void PrepareProfile() {
    for (const auto& p : {g.paths.profile, g.paths.runtime, g.paths.config, g.paths.cache,
                          g.paths.data, g.paths.state, g.paths.logs}) MakeDirectory(p);
    fs::path ready = g.paths.runtime / L"offline-seed.ready";
    const fs::path pluginPackage = g.paths.config / L"opencode" / L"node_modules" /
                                  L"@opencode-ai" / L"plugin" / L"package.json";
    bool needsSeed = !fs::exists(ready) || !fs::is_regular_file(pluginPackage);
    const fs::path dataSeed = g.paths.seed / L"data";
    if (!needsSeed && fs::is_directory(dataSeed)) {
        for (const auto& entry : fs::recursive_directory_iterator(dataSeed)) {
            if (entry.is_regular_file() &&
                !fs::is_regular_file(g.paths.data / entry.path().lexically_relative(dataSeed))) {
                needsSeed = true; break;
            }
        }
    }
    if (needsSeed) {
        if (!fs::is_directory(g.paths.seed))
            throw std::runtime_error("Le cache de démarrage hors ligne est absent. Relancez Setup.exe depuis le pack complet.");
        Status(L"Préparation des composants hors ligne du premier lancement…");
        size_t count = 0;
        for (const auto& entry : fs::recursive_directory_iterator(g.paths.seed)) {
            CheckStop();
            DWORD attr = GetFileAttributesW(entry.path().c_str());
            if (attr == INVALID_FILE_ATTRIBUTES) Fail(L"Impossible de lire un composant du pack");
            if (attr & FILE_ATTRIBUTE_REPARSE_POINT)
                throw std::runtime_error("Un lien de fichier inattendu a été trouvé dans le pack.");
            const auto relative = entry.path().lexically_relative(g.paths.seed);
            // OpenTUI's parser cache follows XDG_DATA_HOME. OpenCode sessions
            // stay in that stable data root; versioned config/cache stay in
            // runtime. Only the files explicitly present in the seed are copied.
            const bool stableData = relative.begin() != relative.end() && *relative.begin() == fs::path(L"data");
            const auto target = (stableData ? g.paths.profile : g.paths.runtime) / relative;
            if (entry.is_directory()) MakeDirectory(target);
            else if (entry.is_regular_file()) {
                MakeDirectory(target.parent_path());
                // Only packaged runtime dependencies live in this seed. Copy
                // atomically so an interrupted first launch is repairable.
                std::error_code sizeError;
                bool existingComplete = fs::is_regular_file(target) &&
                    fs::file_size(target, sizeError) == entry.file_size() && !sizeError;
                if (!existingComplete) {
                    fs::path temporary = target;
                    temporary += L".seedtmp." + std::to_wstring(GetCurrentProcessId());
                    if (!CopyFileW(entry.path().c_str(), temporary.c_str(), FALSE)) {
                        DWORD error = GetLastError(); DeleteFileW(temporary.c_str());
                        Fail(L"Impossible de préparer " + target.wstring(), error);
                    }
                    if (!MoveFileExW(temporary.c_str(), target.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
                        DWORD error = GetLastError(); DeleteFileW(temporary.c_str());
                        Fail(L"Impossible de finaliser " + target.wstring(), error);
                    }
                }
                if (++count % 150 == 0) Status(L"Préparation hors ligne : " + std::to_wstring(count) + L" fichiers…");
            }
        }
        WriteAtomic(ready, Utf8(std::wstring(kProduct) + L" " + kVersion) + "\n");
    }
}

std::string WriteConfig(unsigned short port, const std::string& token, int context) {
    if (!IsAllowedContext(context))
        throw std::runtime_error("La taille de contexte n’est pas proposée par ce pack.");
    auto config = ReadSmallFile(g.paths.configTemplate);
    const std::string base = "http://127.0.0.1:" + std::to_string(port) + "/v1";
    config = ReplaceAll(std::move(config), "{{BASE_URL}}", base);
    config = ReplaceAll(std::move(config), "{{API_KEY}}", token);
    config = ReplaceAll(std::move(config), "{{CONTEXT}}", std::to_string(context));
    // A short context must still leave room for the prompt and tools.
    const int outputTokens = std::min(kOutputTokens, context / 2);
    config = ReplaceAll(std::move(config), "{{OUTPUT}}", std::to_string(outputTokens));
    config = ReplaceAll(std::move(config), "{{COMPACTION_RESERVED}}",
                        std::to_string(std::min(1024, context / 8)));
    config = ReplaceAll(std::move(config), "{{COMPACTION_PRESERVE}}",
                        std::to_string(std::min(2048, context / 4)));
    // Resolve the Windows shell canonically so a project's own powershell.exe
    // cannot shadow the system executable through the working directory.
    wchar_t systemDirectory[MAX_PATH + 1]{};
    UINT systemLength = GetSystemDirectoryW(systemDirectory, MAX_PATH);
    if (!systemLength || systemLength >= MAX_PATH) Fail(L"Impossible de localiser PowerShell");
    fs::path shell = fs::path(systemDirectory) / L"WindowsPowerShell" / L"v1.0" / L"powershell.exe";
    if (!fs::is_regular_file(shell))
        throw std::runtime_error("Windows PowerShell est absent. Les outils de commande d'OpenCode en ont besoin.");
    config = ReplaceAll(std::move(config), "\"powershell.exe\"", "\"" + JsonEscape(shell.wstring()) + "\"");
    if (config.find("{{") != std::string::npos)
        throw std::runtime_error("La configuration du pack contient un paramètre non renseigné.");
    // Reject accidental remote endpoints in the provider template itself. URLs
    // used as JSON schema metadata are harmless but cannot become a provider.
    if (config.find(base) == std::string::npos || config.find(token) == std::string::npos)
        throw std::runtime_error("La configuration locale ne contient pas l'adresse et la clé de cette session.");
    WriteAtomic(g.paths.config / L"opencode" / L"opencode.json", config);
    return config;
}

Environment ChildEnvironment(const std::string& config = {}) {
    Environment env = CurrentEnvironment();
    // Ambient overrides from another OpenCode / llama.cpp installation must
    // not add remote backends, weaken permissions, or redirect this profile.
    // Only child processes receive this isolated environment.
    const auto startsWith = [](const std::wstring& value, const wchar_t* prefix) {
        int count = static_cast<int>(wcslen(prefix));
        return value.size() >= static_cast<size_t>(count) &&
            CompareStringOrdinal(value.data(), count, prefix, count, TRUE) == CSTR_EQUAL;
    };
    for (auto entry = env.begin(); entry != env.end();) {
        if (startsWith(entry->first, L"OPENCODE_") || startsWith(entry->first, L"LLAMA_ARG_"))
            entry = env.erase(entry);
        else ++entry;
    }
    env[L"XDG_CONFIG_HOME"] = g.paths.config.wstring();
    env[L"XDG_CACHE_HOME"] = g.paths.cache.wstring();
    env[L"XDG_DATA_HOME"] = g.paths.data.wstring();
    env[L"XDG_STATE_HOME"] = g.paths.state.wstring();
    env[L"PATH"] = g.paths.tools.wstring() + L";" +
                   (g.paths.install / L"wad" / L"opencode").wstring() + L";" +
                   (g.paths.install / L"wad" / L"engine").wstring() + L";" + env[L"PATH"];
    // These settings are scoped to child processes and never change Windows.
    env.erase(L"HTTP_PROXY"); env.erase(L"HTTPS_PROXY"); env.erase(L"ALL_PROXY");
    env[L"NO_PROXY"] = L"127.0.0.1,localhost,::1";
    env[L"npm_config_offline"] = L"true";
    env[L"npm_config_audit"] = L"false";
    env[L"npm_config_fund"] = L"false";
    env[L"OPENCODE_DISABLE_AUTOUPDATE"] = L"1";
    env[L"OPENCODE_DISABLE_MODELS_FETCH"] = L"1";
    env[L"OPENCODE_DISABLE_DEFAULT_PLUGINS"] = L"1";
    env[L"OPENCODE_DISABLE_LSP_DOWNLOAD"] = L"1";
    env[L"OPENCODE_DISABLE_EXTERNAL_SKILLS"] = L"1";
    env[L"OPENCODE_DISABLE_CLAUDE_CODE"] = L"1";
    env[L"OPENCODE_DISABLE_PROJECT_CONFIG"] = L"1";
    env[L"OPENCODE_PURE"] = L"1";
    env.erase(L"OTEL_EXPORTER_OTLP_ENDPOINT");
    env.erase(L"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT");
    env.erase(L"OTEL_EXPORTER_OTLP_METRICS_ENDPOINT");
    env.erase(L"OTEL_EXPORTER_OTLP_LOGS_ENDPOINT");
    env[L"OTEL_SDK_DISABLED"] = L"true";
    env.erase(L"NO_COLOR");
    if (!config.empty()) {
        env[L"OPENCODE_CONFIG"] = (g.paths.config / L"opencode" / L"opencode.json").wstring();
        env[L"OPENCODE_CONFIG_CONTENT"] = Wide(config);
    }
    return env;
}

Handle CreateOwnedJob() {
    Handle job(CreateJobObjectW(nullptr, nullptr));
    if (!job.valid()) Fail(L"Impossible de créer la session de processus");
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!SetInformationJobObject(job.value, JobObjectExtendedLimitInformation, &limits, sizeof(limits)))
        Fail(L"Impossible de sécuriser l'arrêt de la session");
    return job;
}

struct Process {
    Handle handle;
    DWORD id = 0;
};

Process StartProcess(const fs::path& executable, const std::vector<std::wstring>& args,
                     const fs::path& cwd, const Environment& environment, HANDLE job,
                     bool newConsole, const fs::path& outputLog = {}) {
    CheckStop();
    std::wstring command = CommandLine(executable, args);
    std::vector<wchar_t> mutableCommand(command.begin(), command.end());
    mutableCommand.push_back(L'\0');
    auto env = EnvironmentBlock(environment);
    STARTUPINFOEXW startup{};
    startup.StartupInfo.cb = sizeof(STARTUPINFOW);
    DWORD flags = CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT |
                  (newConsole ? CREATE_NEW_CONSOLE : CREATE_NO_WINDOW);
    Handle logHandle, inputHandle;
    std::vector<unsigned char> attributes;
    bool initializedAttributes = false;
    BOOL inherit = FALSE;
    if (!outputLog.empty()) {
        SECURITY_ATTRIBUTES sa{sizeof(sa), nullptr, TRUE};
        logHandle.reset(CreateFileW(outputLog.c_str(), GENERIC_WRITE, FILE_SHARE_READ,
                                     &sa, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr));
        inputHandle.reset(CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                      &sa, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr));
        if (!logHandle.valid() || !inputHandle.valid()) Fail(L"Impossible d'ouvrir le journal du moteur");
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        startup.StartupInfo.hStdInput = inputHandle.value;
        startup.StartupInfo.hStdOutput = logHandle.value;
        startup.StartupInfo.hStdError = logHandle.value;
        SIZE_T required = 0;
        InitializeProcThreadAttributeList(nullptr, 1, 0, &required);
        attributes.resize(required);
        startup.lpAttributeList = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attributes.data());
        if (!InitializeProcThreadAttributeList(startup.lpAttributeList, 1, 0, &required))
            Fail(L"Impossible de préparer les handles du moteur");
        initializedAttributes = true;
        HANDLE inheritedHandles[] = {inputHandle.value, logHandle.value};
        if (!UpdateProcThreadAttribute(startup.lpAttributeList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                       inheritedHandles, sizeof(inheritedHandles), nullptr, nullptr)) {
            DWORD error = GetLastError(); DeleteProcThreadAttributeList(startup.lpAttributeList);
            Fail(L"Impossible de limiter les handles transmis", error);
        }
        startup.StartupInfo.cb = sizeof(startup);
        flags |= EXTENDED_STARTUPINFO_PRESENT;
        inherit = TRUE;
    }
    PROCESS_INFORMATION process{};
    BOOL created = CreateProcessW(executable.c_str(), mutableCommand.data(), nullptr, nullptr, inherit,
                                  flags, env.data(), cwd.c_str(), &startup.StartupInfo, &process);
    DWORD creationError = GetLastError();
    if (initializedAttributes) DeleteProcThreadAttributeList(startup.lpAttributeList);
    if (!created) Fail(L"Impossible de lancer " + executable.filename().wstring(), creationError);
    Handle processHandle(process.hProcess), threadHandle(process.hThread);
    if (!AssignProcessToJobObject(job, processHandle.value)) {
        DWORD error = GetLastError(); TerminateProcess(processHandle.value, 1);
        WaitForSingleObject(processHandle.value, 5000);
        Fail(L"Windows empêche la gestion de cette session", error);
    }
    if (g.stopRequested.load()) { TerminateProcess(processHandle.value, 1); throw Cancelled(); }
    if (ResumeThread(threadHandle.value) == static_cast<DWORD>(-1)) {
        DWORD error = GetLastError(); TerminateProcess(processHandle.value, 1);
        Fail(L"Impossible de démarrer le processus", error);
    }
    return Process{std::move(processHandle), process.dwProcessId};
}

unsigned short ChoosePort() {
    for (unsigned short port = 18741; port < 18781; ++port) {
        SOCKET socketHandle = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (socketHandle == INVALID_SOCKET) throw std::runtime_error("Impossible de préparer une liaison locale sur ce PC.");
        BOOL exclusive = TRUE;
        setsockopt(socketHandle, SOL_SOCKET, SO_EXCLUSIVEADDRUSE,
                   reinterpret_cast<const char*>(&exclusive), sizeof(exclusive));
        sockaddr_in address{}; address.sin_family = AF_INET;
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK); address.sin_port = htons(port);
        int result = bind(socketHandle, reinterpret_cast<sockaddr*>(&address), sizeof(address));
        closesocket(socketHandle);
        if (result == 0) return port;
    }
    throw std::runtime_error("Aucun port local disponible entre 18741 et 18780. Le mode Discussion directe reste utilisable sans serveur.");
}

bool ListenerOwnedBy(unsigned short port, DWORD processId) {
    ULONG bytes = 0;
    DWORD result = GetExtendedTcpTable(nullptr, &bytes, FALSE, AF_INET, TCP_TABLE_OWNER_PID_LISTENER, 0);
    if (result != ERROR_INSUFFICIENT_BUFFER || !bytes) return false;
    std::vector<unsigned char> table(bytes);
    result = GetExtendedTcpTable(table.data(), &bytes, FALSE, AF_INET, TCP_TABLE_OWNER_PID_LISTENER, 0);
    if (result != NO_ERROR) return false;
    auto* entries = reinterpret_cast<MIB_TCPTABLE_OWNER_PID*>(table.data());
    for (DWORD i = 0; i < entries->dwNumEntries; ++i) {
        const auto& entry = entries->table[i];
        if (entry.dwOwningPid == processId && ntohs(static_cast<unsigned short>(entry.dwLocalPort)) == port &&
            entry.dwLocalAddr == htonl(INADDR_LOOPBACK)) return true;
    }
    return false;
}

struct HttpResponse { DWORD status = 0; std::string body; };

HttpResponse LocalHttp(unsigned short port, const std::string& token, const wchar_t* method,
                       const wchar_t* path, const std::string& body = {}, int timeoutMs = 1500) {
    const std::wstring userAgent = std::wstring(PackConfig::ProfileName) + L"/" + kVersion;
    InternetHandle session(WinHttpOpen(userAgent.c_str(), WINHTTP_ACCESS_TYPE_NO_PROXY,
                                       WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0));
    if (!session.value) return {};
    WinHttpSetTimeouts(session.value, timeoutMs, timeoutMs, timeoutMs, timeoutMs);
    InternetHandle connection(WinHttpConnect(session.value, L"127.0.0.1", port, 0));
    if (!connection.value) return {};
    InternetHandle request(WinHttpOpenRequest(connection.value, method, path, nullptr,
                                              WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0));
    if (!request.value) return {};
    DWORD redirectPolicy = WINHTTP_OPTION_REDIRECT_POLICY_NEVER;
    WinHttpSetOption(request.value, WINHTTP_OPTION_REDIRECT_POLICY, &redirectPolicy, sizeof(redirectPolicy));
    DWORD features = WINHTTP_DISABLE_COOKIES | WINHTTP_DISABLE_AUTHENTICATION;
    WinHttpSetOption(request.value, WINHTTP_OPTION_DISABLE_FEATURE, &features, sizeof(features));
    std::wstring headers = L"Authorization: Bearer " + Wide(token) + L"\r\nContent-Type: application/json\r\n";
    if (!WinHttpSendRequest(request.value, headers.c_str(), static_cast<DWORD>(headers.size()),
                            body.empty() ? WINHTTP_NO_REQUEST_DATA : const_cast<char*>(body.data()),
                            static_cast<DWORD>(body.size()), static_cast<DWORD>(body.size()), 0) ||
        !WinHttpReceiveResponse(request.value, nullptr)) return {};
    HttpResponse response;
    DWORD statusBytes = sizeof(response.status);
    WinHttpQueryHeaders(request.value, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                         WINHTTP_HEADER_NAME_BY_INDEX, &response.status, &statusBytes, WINHTTP_NO_HEADER_INDEX);
    for (;;) {
        DWORD available = 0;
        if (!WinHttpQueryDataAvailable(request.value, &available) || !available) break;
        if (response.body.size() + available > 1024 * 1024) return {};
        size_t old = response.body.size(); response.body.resize(old + available);
        DWORD received = 0;
        if (!WinHttpReadData(request.value, response.body.data() + old, available, &received)) return {};
        response.body.resize(old + received);
        if (!received) break;
    }
    return response;
}

void WaitForEngine(const Process& engine, unsigned short port, const std::string& token) {
    auto start = std::chrono::steady_clock::now();
    int lastSeconds = -1;
    for (;;) {
        CheckStop();
        DWORD exitCode = 0;
        if (!GetExitCodeProcess(engine.handle.value, &exitCode) || exitCode != STILL_ACTIVE)
            throw std::runtime_error("Le moteur s'est arrêté pendant son lancement. Consultez le journal moteur dans Journaux.");
        int seconds = static_cast<int>(std::chrono::duration_cast<std::chrono::seconds>(
                          std::chrono::steady_clock::now() - start).count());
        if (seconds >= PackConfig::LoadTimeoutSeconds)
            throw std::runtime_error("Le modèle n’est pas prêt après " +
                std::to_string(PackConfig::LoadTimeoutSeconds) +
                " secondes. Consultez le journal moteur. Si les ports locaux sont bloqués, utilisez Discussion directe.");
        if (seconds != lastSeconds) {
            Status(std::wstring(L"Chargement de ") + PackConfig::ModelLabel + L"… " + std::to_wstring(seconds) + L" s");
            lastSeconds = seconds;
        }
        // Never adopt an unrelated service which happens to use the same port.
        if (ListenerOwnedBy(port, engine.id)) {
            auto health = LocalHttp(port, token, L"GET", L"/health");
            if (health.status == 200) {
                auto models = LocalHttp(port, token, L"GET", L"/v1/models");
                if (models.status == 200 && models.body.find(PackConfig::ModelAlias) != std::string::npos) return;
            }
        }
        for (int i = 0; i < 5; ++i) { CheckStop(); Sleep(100); }
    }
}

std::vector<std::wstring> InferenceArguments(int context, int threads) {
    // b12089 uses --load-mode mmap, not the historical --mmap switch.
    // Both entry points share the same bounded context, CPU work and KV type.
    // Keep mapped weights without a second, repacked copy of every expert.
    return {
        L"--model", g.paths.model.wstring(),
        L"--ctx-size", std::to_wstring(context), L"--n-gpu-layers", L"0",
        L"--load-mode", L"mmap", L"--no-repack",
        L"--threads", std::to_wstring(threads), L"--threads-batch", std::to_wstring(threads),
        L"--batch-size", std::to_wstring(PackConfig::BatchSize),
        L"--ubatch-size", std::to_wstring(PackConfig::MicroBatchSize),
        L"--cache-type-k", PackConfig::CacheTypeK, L"--cache-type-v", PackConfig::CacheTypeV,
        L"--flash-attn", PackConfig::FlashAttention ? L"on" : L"off",
        L"--parallel", L"1", L"--n-predict", std::to_wstring(std::min(kOutputTokens, context / 2)),
        L"--jinja", L"--reasoning", L"off", L"--reasoning-budget", L"0", L"--offline"
    };
}

std::wstring InferenceSummary(int context, const CpuBudget& cpu) {
    return L"contexte=" + std::to_wstring(context) + L", threads=" + std::to_wstring(cpu.threads) +
        L", batch=" + std::to_wstring(PackConfig::BatchSize) +
        L", ubatch=" + std::to_wstring(PackConfig::MicroBatchSize) +
        L", cache KV=" + PackConfig::CacheTypeK + L"/" + PackConfig::CacheTypeV +
        L", flash attention=" + (PackConfig::FlashAttention ? std::wstring(L"oui") : std::wstring(L"non")) +
        L", poids mappés sans repacking, une seule séquence";
}

Process StartEngine(HANDLE job, unsigned short port, const std::string& token, int context) {
    // First-launch profile preparation may take time; check available RAM again
    // immediately before starting the model. Other applications can still grow
    // later, so this check does not claim to reserve memory from Windows.
    CheckMemoryBudget(context);
    const CpuBudget cpu = ReadCpuBudget();
    auto args = InferenceArguments(context, cpu.threads);
    args.insert(args.end(), {
        L"--alias", kAlias, L"--no-webui",
        // The upstream 8 GiB prompt-cache default is separate from the KV
        // buffers. Disable it; four small recurrent checkpoints preserve useful
        // message boundaries without retaining the upstream 32-state default.
        L"--cache-ram", L"0", L"--ctx-checkpoints", L"4",
        L"--host", L"127.0.0.1", L"--port", std::to_wstring(port),
        L"--api-key", Wide(token)
    });
    fs::path output = g.paths.logs / (L"moteur-" + TimeStamp() + L".log");
    Log(L"Démarrage du moteur CPU : " + InferenceSummary(context, cpu) +
        L", cache de prompts RAM=0 Mo, checkpoints=4" +
        L", adresse=127.0.0.1:" + std::to_wstring(port) + L", journal=" + output.filename().wstring());
    auto process = StartProcess(g.paths.engine, args, g.paths.install, ChildEnvironment(), job, false, output);
    WaitForEngine(process, port, token);
    return process;
}

void SetJobForUi(HANDLE job) { std::lock_guard<std::mutex> lock(g.jobMutex); g.job = job; }

void RequestStop() {
    g.stopRequested = true;
    std::lock_guard<std::mutex> lock(g.jobMutex);
    if (g.job) TerminateJobObject(g.job, 0);
}

void RunSession(bool direct, fs::path project, int context) {
    auto result = std::make_unique<SessionResult>();
    Handle job;
    try {
        CheckStop();
        ValidateFiles(direct);
        CheckMemoryBudget(context);
        if (!IsAllowedContext(context))
            throw std::runtime_error("La taille de contexte n’est pas proposée par ce pack.");
        if (!fs::is_directory(project)) throw std::runtime_error("Le dossier de projet n'existe plus.");
        MakeDirectory(g.paths.logs);
        job = CreateOwnedJob();
        SetJobForUi(job.value);
        if (direct) {
            Status(L"Ouverture de la discussion directe, sans serveur réseau…");
            const CpuBudget cpu = ReadCpuBudget();
            auto args = InferenceArguments(context, cpu.threads);
            args.emplace_back(L"--conversation");
            Log(L"Discussion directe CPU : " + InferenceSummary(context, cpu) + L", sans cache de prompts serveur.");
            auto chat = StartProcess(g.paths.completion, args, project, ChildEnvironment(), job.value, true);
            Status(L"Discussion ouverte dans sa console. Fermez-la pour terminer.");
            while (WaitForSingleObject(chat.handle.value, 250) == WAIT_TIMEOUT) CheckStop();
            CheckStop();
            DWORD exit = 0; GetExitCodeProcess(chat.handle.value, &exit);
            if (exit != 0 && exit != 130 && exit != 0xC000013A)
                throw std::runtime_error("La discussion s'est arrêtée avec le code " + std::to_string(exit) + ". Consultez le message affiché dans sa console.");
            result->message = L"Discussion terminée. Le modèle a été déchargé.";
        } else {
            PrepareProfile();
            unsigned short port = ChoosePort();
            const std::string token = RandomToken();
            const std::string config = WriteConfig(port, token, context);
            auto engine = StartEngine(job.value, port, token, context);
            g.activePort = port;
            CheckStop();
            Status(L"Modèle prêt. Ouverture d'OpenCode…");
            auto opencode = StartProcess(g.paths.opencode, {}, project, ChildEnvironment(config), job.value, true);
            Status(L"OpenCode est ouvert. Travaillez dans sa console ; ce lanceur gère le moteur local.");
            Log(L"OpenCode lancé dans le dossier de projet sélectionné.");
            for (;;) {
                CheckStop();
                DWORD wait = WaitForSingleObject(opencode.handle.value, 250);
                if (wait == WAIT_OBJECT_0) break;
                if (wait == WAIT_FAILED) Fail(L"Impossible de suivre OpenCode");
                if (WaitForSingleObject(engine.handle.value, 0) == WAIT_OBJECT_0)
                    throw std::runtime_error("Le moteur local s'est arrêté pendant la session. Consultez son journal.");
            }
            CheckStop();
            DWORD exit = 0; GetExitCodeProcess(opencode.handle.value, &exit);
            if (exit != 0 && exit != 130 && exit != 0xC000013A)
                throw std::runtime_error("OpenCode s'est arrêté avec le code " + std::to_string(exit) + ". Consultez Journaux et le diagnostic.");
            result->message = L"Session terminée. Vos conversations sont conservées sur ce PC.";
        }
    } catch (const Cancelled&) {
        result->message = L"Session arrêtée. Le moteur local a été fermé.";
    } catch (const std::exception& ex) {
        result->error = true;
        try { result->message = Wide(ex.what()); } catch (...) { result->message = L"Erreur inattendue. Consultez Journaux."; }
        Log(L"ERREUR : " + result->message);
    }
    SetJobForUi(nullptr);
    if (job.valid()) TerminateJobObject(job.value, 0);
    job.reset();
    g.activePort = 0;
    if (!PostMessageW(g.window, WM_FINISHED, 0, reinterpret_cast<LPARAM>(result.get()))) return;
    result.release();
}

std::wstring DiagnosticReport(bool* valid = nullptr) {
    std::wostringstream report;
    report << kProduct << L" " << kVersion << L" — DIAGNOSTIC\r\n\r\n";
    report << L"Date : " << TimeStamp() << L"\r\n";
    report << L"Application : " << g.paths.install.wstring() << L"\r\n";
    report << L"Données utilisateur : " << g.paths.profile.wstring() << L"\r\n";
    report << L"Journaux OpenCode : " << (g.paths.data / L"opencode" / L"log").wstring() << L"\r\n";
    report << L"Projet : " << g.projectPath.wstring() << L"\r\n\r\n";
    const MemoryBudget memory = ReadMemoryBudget();
    report << L"RAM installée : " << memory.installedMB << L" Mo"
           << (memory.physicalInventory ? L" (inventaire matériel)" : L" (mémoire utilisable, valeur de repli)") << L"\r\n";
    report << L"RAM utilisable : " << memory.usableMB << L" Mo\r\n";
    report << L"RAM disponible : " << memory.availableMB << L" Mo\r\n";
    report << L"Session estimée (tous les experts, contexte, moteur et OpenCode) : "
           << EstimatedSessionMemoryMB(g.selectedContext) << L" Mo\r\n";
    report << L"Marge visée pour les autres logiciels : " << PackConfig::MemoryReserveMB << L" Mo\r\n";
    report << L"RAM disponible demandée au démarrage, marge incluse : "
           << RequiredAvailableMemoryMB(g.selectedContext) << L" Mo\r\n";
    report << L"Ce budget est une estimation ; la consommation dépend de la session et des autres applications.\r\n";
    report << L"Modèle : " << PackConfig::ModelLabel << L"\r\n";
    report << L"Fichier du modèle : " << kModelFile << L"\r\n";
    report << L"SHA-256 attendu du modèle : " << Wide(PackConfig::ModelSha256) << L"\r\n";
    SYSTEM_INFO system{}; GetNativeSystemInfo(&system);
    const CpuBudget cpu = ReadCpuBudget();
    report << L"Processeurs logiques : " << cpu.logical << L"\r\n";
    if (cpu.physical) report << L"Cœurs physiques : " << cpu.physical << L"\r\n";
    else report << L"Cœurs physiques : inventaire indisponible, repli sur les processeurs logiques\r\n";
    report << L"Threads de calcul / préremplissage : " << cpu.threads << L" / " << cpu.threads << L"\r\n";
    report << L"Architecture du système : " <<
        (system.wProcessorArchitecture == PROCESSOR_ARCHITECTURE_AMD64 ? L"x64" :
         system.wProcessorArchitecture == PROCESSOR_ARCHITECTURE_ARM64 ? L"ARM64 (x64 émulé)" : L"autre") << L"\r\n";
    report << L"Contexte OpenCode : " << g.selectedContext << L" tokens\r\n";
    report << L"Réponse maximale par tour : " << std::min(kOutputTokens, g.selectedContext / 2) << L" tokens\r\n";
    report << L"Poids : mémoire mappée ; repacking désactivé\r\n";
    report << L"Cache KV K/V : " << PackConfig::CacheTypeK << L" / " << PackConfig::CacheTypeV << L"\r\n";
    report << L"Flash Attention : " << (PackConfig::FlashAttention ? L"activée" : L"désactivée") << L"\r\n";
    report << L"Batch / micro-batch : " << PackConfig::BatchSize << L" / " << PackConfig::MicroBatchSize << L"\r\n";
    report << L"Serveur : une séquence ; cache de prompts RAM désactivé ; quatre checkpoints récurrents\r\n";
    report << L"Discussion directe : une séquence, sans cache de prompts serveur\r\n\r\n";
    bool allValid = true;
    const std::vector<std::pair<std::wstring, fs::path>> files = {
        {L"Moteur API", g.paths.engine}, {L"Discussion directe", g.paths.completion},
        {L"OpenCode", g.paths.opencode}, {L"Recherche de fichiers", g.paths.tools / L"rg.exe"},
        {PackConfig::ModelLabel, g.paths.model}, {L"Configuration", g.paths.configTemplate}
    };
    for (const auto& entry : files) {
        std::error_code ec; auto bytes = fs::file_size(entry.second, ec);
        bool ok = !ec && bytes > 0;
        if (entry.second == g.paths.model) ok = ok && bytes == kModelBytes;
        allValid = allValid && ok;
        report << (ok ? L"[OK] " : L"[ERREUR] ") << entry.first;
        if (!ec) report << L" : " << bytes << L" octets";
        report << L"\r\n";
    }
    try { ValidateFiles(); } catch (const std::exception& ex) { allValid = false; report << L"\r\n" << Wide(ex.what()) << L"\r\n"; }
    try { CheckMemoryBudget(g.selectedContext); } catch (const std::exception& ex) { allValid = false; report << L"\r\n" << Wide(ex.what()) << L"\r\n"; }
    report << L"\r\nPort de cette session : ";
    if (g.activePort.load()) report << L"127.0.0.1:" << g.activePort.load(); else report << L"aucun moteur API actif";
    report << L"\r\n\r\nLe mode OpenCode utilise uniquement un serveur local pour le modèle.\r\n"
              L"Le mode Discussion directe ne lance aucun serveur HTTP.\r\n"
              L"Aucune règle de pare-feu, aucun service Windows et aucun réglage système\r\n"
              L"ne sont modifiés par le lanceur.\r\n\r\n"
              L"Ce contrôle vérifie la présence et le format des fichiers ; l'installateur\r\n"
              L"réalise la vérification SHA-256 du contenu livré.\r\n";
    if (valid) *valid = allValid;
    return report.str();
}

void SaveSettings() {
    try {
        WriteAtomic(g.paths.profile / L"launcher.ini",
            "version=1\nproject=" + Utf8(g.projectPath.wstring()) + "\ncontext=" +
            std::to_string(g.selectedContext) + "\ndark=" + (g.dark ? "1" : "0") + "\n");
    } catch (const std::exception& ex) { try { Log(Wide(ex.what())); } catch (...) {} }
}

void LoadSettings() {
    try {
        g.projectPath = KnownFolder(FOLDERID_Documents) / PackConfig::ProfileName;
        MakeDirectory(g.projectPath);
    } catch (...) {
        g.projectPath = g.paths.profile / L"Projets";
        MakeDirectory(g.projectPath);
    }
    try {
        const fs::path settings = g.paths.profile / L"launcher.ini";
        if (!fs::exists(settings)) return;
        std::istringstream input(ReadSmallFile(settings, 65536));
        std::string line;
        while (std::getline(input, line)) {
            if (!line.empty() && line.back() == '\r') line.pop_back();
            if (line.rfind("project=", 0) == 0) {
                fs::path candidate(Wide(line.substr(8)));
                if (candidate.is_absolute() && fs::is_directory(candidate)) g.projectPath = candidate;
            } else if (line.rfind("context=", 0) == 0) {
                for (int context : PackConfig::AllowedContexts)
                    if (line == "context=" + std::to_string(context)) g.selectedContext = context;
            }
            else if (line == "dark=0") g.dark = false;
            else if (line == "dark=1") g.dark = true;
        }
    } catch (...) { /* A broken preference file must not block the application. */ }
}

int S(int value) { return MulDiv(value, g.dpi, 96); }
COLORREF Background() { return g.dark ? RGB(16, 21, 32) : RGB(242, 245, 250); }
COLORREF Panel() { return g.dark ? RGB(25, 33, 48) : RGB(255, 255, 255); }
COLORREF Input() { return g.dark ? RGB(16, 23, 36) : RGB(245, 247, 251); }
COLORREF Foreground() { return g.dark ? RGB(235, 240, 250) : RGB(25, 35, 52); }
COLORREF Muted() { return g.dark ? RGB(160, 173, 194) : RGB(88, 105, 126); }
COLORREF Border() { return g.dark ? RGB(45, 57, 76) : RGB(215, 224, 237); }
COLORREF Accent() { return RGB(48, 112, 235); }

template<typename T> T OptionalFunction(HMODULE module, const char* name) {
    FARPROC raw = GetProcAddress(module, name);
    T function = nullptr;
    static_assert(sizeof(function) == sizeof(raw), "Windows function-pointer size mismatch");
    std::memcpy(&function, &raw, sizeof(function));
    return function;
}

void SetDarkTitle(HWND window, bool dark) {
    HMODULE dwm = LoadLibraryExW(L"dwmapi.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!dwm) return;
    auto fn = OptionalFunction<HRESULT(WINAPI*)(HWND, DWORD, LPCVOID, DWORD)>(dwm, "DwmSetWindowAttribute");
    BOOL value = dark;
    if (fn) fn(window, 20, &value, sizeof(value));
    FreeLibrary(dwm);
}

void UpdateFontsAndBrushes() {
    for (HFONT font : {g.font, g.titleFont, g.smallFont, g.boldFont}) if (font) DeleteObject(font);
    for (HBRUSH brush : {g.backgroundBrush, g.panelBrush, g.editBrush}) if (brush) DeleteObject(brush);
    auto makeFont = [](int size, int weight) {
        return CreateFontW(-S(size), 0, 0, 0, weight, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                            OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                            DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
    };
    g.font = makeFont(14, FW_NORMAL); g.titleFont = makeFont(29, FW_SEMIBOLD);
    g.smallFont = makeFont(12, FW_NORMAL); g.boldFont = makeFont(14, FW_SEMIBOLD);
    g.backgroundBrush = CreateSolidBrush(Background());
    g.panelBrush = CreateSolidBrush(Panel()); g.editBrush = CreateSolidBrush(Input());
    for (HWND control : {g.project, g.browse, g.start, g.direct, g.stop, g.diagnostic,
                         g.logs, g.guide, g.theme, g.context}) {
        if (control) SendMessageW(control, WM_SETFONT, reinterpret_cast<WPARAM>(g.font), TRUE);
    }
    if (g.window) { SetDarkTitle(g.window, g.dark); InvalidateRect(g.window, nullptr, TRUE); }
}

void Place(HWND window, int x, int y, int width, int height) {
    MoveWindow(window, S(x), S(y), S(width), S(height), TRUE);
}

void Layout() {
    RECT r{}; GetClientRect(g.window, &r);
    int width = MulDiv(r.right, 96, g.dpi);
    int height = MulDiv(r.bottom, 96, g.dpi);
    Place(g.project, 53, 174, std::max(240, width - 226), 35);
    Place(g.browse, width - 158, 172, 104, 39);
    Place(g.context, 198, 232, 174, 200);
    Place(g.start, 53, 293, 237, 46);
    Place(g.direct, 304, 293, 211, 46);
    Place(g.stop, width - 186, 293, 132, 46);
    Place(g.progress, 54, 457, width - 108, 8);
    Place(g.diagnostic, 32, height - 100, 142, 37);
    Place(g.logs, 186, height - 100, 119, 37);
    Place(g.guide, 317, height - 100, 119, 37);
    Place(g.theme, width - 164, height - 100, 132, 37);
}

void DrawLabel(HDC dc, const std::wstring& text, int x, int y, int width, int height,
               HFONT font, COLORREF color, UINT flags = DT_LEFT | DT_VCENTER | DT_SINGLELINE) {
    RECT r{S(x), S(y), S(x + width), S(y + height)};
    auto old = SelectObject(dc, font); SetBkMode(dc, TRANSPARENT); SetTextColor(dc, color);
    DrawTextW(dc, text.c_str(), static_cast<int>(text.size()), &r, flags);
    SelectObject(dc, old);
}

void RoundedPanel(HDC dc, int x, int y, int width, int height, COLORREF fill, COLORREF border) {
    HPEN pen = CreatePen(PS_SOLID, 1, border); HBRUSH brush = CreateSolidBrush(fill);
    HGDIOBJ oldPen = SelectObject(dc, pen), oldBrush = SelectObject(dc, brush);
    RoundRect(dc, S(x), S(y), S(x + width), S(y + height), S(18), S(18));
    SelectObject(dc, oldPen); SelectObject(dc, oldBrush); DeleteObject(pen); DeleteObject(brush);
}

void PaintMain() {
    PAINTSTRUCT ps{}; HDC dc = BeginPaint(g.window, &ps);
    RECT client{}; GetClientRect(g.window, &client); FillRect(dc, &client, g.backgroundBrush);
    const int width = MulDiv(client.right, 96, g.dpi), height = MulDiv(client.bottom, 96, g.dpi);
    DrawLabel(dc, kProduct, 32, 23, width - 64, 43, g.titleFont, Foreground());
    DrawLabel(dc, g.subtitle, 33, 70, width - 66, 25, g.font, Muted());
    RoundedPanel(dc, 32, 120, width - 64, 250, Panel(), Border());
    DrawLabel(dc, L"DOSSIER DE PROJET", 53, 140, width - 106, 23, g.smallFont, Muted());
    DrawLabel(dc, L"Contexte du modèle", 53, 228, 143, 34, g.font, Foreground());
    DrawLabel(dc, std::to_wstring(kDefaultContext) + L" tokens conseillés pour ce pack", 391, 228,
              std::max(200, width - 446), 34, g.smallFont, Muted());
    RoundedPanel(dc, 32, 391, width - 64, 93, Panel(), Border());
    DrawLabel(dc, g.status, 53, 402, width - 106, 47, g.font,
              g.statusError ? RGB(235, 116, 104) : Foreground(), DT_LEFT | DT_WORDBREAK | DT_NOPREFIX);
    DrawLabel(dc, std::wstring(PackConfig::ModelLabel) + L" · CPU  |  Modèle et outils embarqués", 33, height - 48,
              width - 66, 25, g.smallFont, Muted());
    EndPaint(g.window, &ps);
}

void DrawButton(const DRAWITEMSTRUCT& item) {
    const bool primary = item.CtlID == ID_START;
    const bool enabled = !(item.itemState & ODS_DISABLED);
    const bool pressed = (item.itemState & ODS_SELECTED) != 0;
    COLORREF fill = primary && enabled ? (pressed ? RGB(36, 88, 196) : Accent()) : Panel();
    COLORREF line = primary && enabled ? fill : Border();
    if (!enabled) fill = g.dark ? RGB(27, 34, 47) : RGB(235, 239, 245);
    HPEN pen = CreatePen(PS_SOLID, 1, line); HBRUSH brush = CreateSolidBrush(fill);
    auto oldPen = SelectObject(item.hDC, pen), oldBrush = SelectObject(item.hDC, brush);
    RoundRect(item.hDC, item.rcItem.left, item.rcItem.top, item.rcItem.right, item.rcItem.bottom, S(11), S(11));
    SelectObject(item.hDC, oldPen); SelectObject(item.hDC, oldBrush); DeleteObject(pen); DeleteObject(brush);
    wchar_t text[128]{}; GetWindowTextW(item.hwndItem, text, 128);
    RECT r = item.rcItem; SetBkMode(item.hDC, TRANSPARENT);
    SetTextColor(item.hDC, !enabled ? Muted() : primary ? RGB(255, 255, 255) : Foreground());
    auto oldFont = SelectObject(item.hDC, primary ? g.boldFont : g.font);
    DrawTextW(item.hDC, text, -1, &r, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
    SelectObject(item.hDC, oldFont);
    if ((item.itemState & ODS_FOCUS) && enabled) { InflateRect(&r, -S(4), -S(4)); DrawFocusRect(item.hDC, &r); }
}

void SetBusyUi(bool busy) {
    for (HWND control : {g.project, g.browse, g.start, g.direct, g.context}) EnableWindow(control, !busy);
    EnableWindow(g.stop, busy);
    SendMessageW(g.progress, PBM_SETMARQUEE, busy ? TRUE : FALSE, 30);
    ShowWindow(g.progress, busy ? SW_SHOW : SW_HIDE);
    InvalidateRect(g.window, nullptr, TRUE);
}

void BrowseProject() {
    IFileOpenDialog* dialog = nullptr;
    HRESULT hr = CoCreateInstance(CLSID_FileOpenDialog, nullptr, CLSCTX_INPROC_SERVER,
                                   IID_IFileOpenDialog, reinterpret_cast<void**>(&dialog));
    if (FAILED(hr)) { MessageBoxW(g.window, L"Impossible d'ouvrir le sélecteur de dossier.", kProduct, MB_ICONERROR); return; }
    FILEOPENDIALOGOPTIONS options{}; dialog->GetOptions(&options);
    dialog->SetOptions(options | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST);
    dialog->SetTitle(L"Choisir le dossier de votre projet");
    dialog->SetOkButtonLabel(L"Utiliser ce dossier");
    IShellItem* initial = nullptr;
    if (SUCCEEDED(SHCreateItemFromParsingName(g.projectPath.c_str(), nullptr, IID_IShellItem,
                                             reinterpret_cast<void**>(&initial)))) {
        dialog->SetFolder(initial); initial->Release();
    }
    if (SUCCEEDED(dialog->Show(g.window))) {
        IShellItem* item = nullptr;
        if (SUCCEEDED(dialog->GetResult(&item))) {
            PWSTR path = nullptr;
            if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &path))) {
                g.projectPath = path; SetWindowTextW(g.project, path); SaveSettings(); CoTaskMemFree(path);
            }
            item->Release();
        }
    }
    dialog->Release();
}

void OpenLogs() {
    MakeDirectory(g.paths.logs);
    ShellExecuteW(g.window, L"open", g.paths.logs.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
}

void ShowDiagnostic() {
    try {
        fs::path file = g.paths.logs / L"diagnostic.txt";
        WriteAtomic(file, "\xEF\xBB\xBF" + Utf8(DiagnosticReport()));
        ShellExecuteW(g.window, L"open", file.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
    } catch (const std::exception& ex) { MessageBoxW(g.window, Wide(ex.what()).c_str(), kProduct, MB_ICONERROR); }
}

void StartFromUi(bool direct) {
    if (g.busy.load()) return;
    int length = GetWindowTextLengthW(g.project);
    if (length <= 0 || length > 30000) { g.status = L"Choisissez un dossier de projet existant."; g.statusError = true; InvalidateRect(g.window, nullptr, TRUE); return; }
    std::wstring path(static_cast<size_t>(length) + 1, L'\0'); GetWindowTextW(g.project, path.data(), length + 1); path.resize(length);
    g.projectPath = fs::path(path);
    std::error_code ec;
    if (!g.projectPath.is_absolute() || !fs::is_directory(g.projectPath, ec)) {
        g.status = L"Le dossier de projet doit être un dossier existant, avec un chemin complet.";
        g.statusError = true; InvalidateRect(g.window, nullptr, TRUE); return;
    }
    LRESULT contextIndex = SendMessageW(g.context, CB_GETCURSEL, 0, 0);
    g.selectedContext = contextIndex >= 0 && static_cast<size_t>(contextIndex) < kContextCount
        ? PackConfig::AllowedContexts[contextIndex] : kDefaultContext;
    SaveSettings();
    if (g.worker.joinable()) g.worker.join();
    g.stopRequested = false; g.busy = true; g.statusError = false;
    g.status = L"Vérification des composants…"; SetBusyUi(true);
    g.worker = std::thread(RunSession, direct, g.projectPath, g.selectedContext);
}

HWND MakeButton(const wchar_t* caption, int id) {
    return CreateWindowExW(0, L"BUTTON", caption, WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_OWNERDRAW,
                          0, 0, 1, 1, g.window, reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)), g.instance, nullptr);
}

LRESULT CALLBACK WindowProcedure(HWND window, UINT message, WPARAM wp, LPARAM lp) {
    switch (message) {
    case WM_CREATE: {
        g.window = window;
        g.project = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", g.projectPath.c_str(),
            WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_AUTOHSCROLL, 0, 0, 1, 1,
            window, reinterpret_cast<HMENU>(ID_PROJECT), g.instance, nullptr);
        SendMessageW(g.project, EM_SETLIMITTEXT, 30000, 0);
        g.browse = MakeButton(L"Parcourir…", ID_BROWSE);
        g.start = MakeButton(L"Ouvrir OpenCode", ID_START);
        g.direct = MakeButton(L"Discussion directe", ID_DIRECT);
        g.stop = MakeButton(L"Arrêter", ID_STOP);
        g.diagnostic = MakeButton(L"Diagnostic", ID_DIAGNOSTIC);
        g.logs = MakeButton(L"Journaux", ID_LOGS);
        g.guide = MakeButton(L"Guide", ID_GUIDE);
        g.theme = MakeButton(g.dark ? L"Mode clair" : L"Mode sombre", ID_THEME);
        g.context = CreateWindowExW(0, L"COMBOBOX", L"", WS_CHILD | WS_VISIBLE | WS_TABSTOP |
            CBS_DROPDOWNLIST | CBS_OWNERDRAWFIXED | CBS_HASSTRINGS, 0, 0, 1, 1,
            window, reinterpret_cast<HMENU>(ID_CONTEXT), g.instance, nullptr);
        size_t selectedIndex = 0;
        for (size_t index = 0; index < kContextCount; ++index) {
            const std::wstring label = std::to_wstring(PackConfig::AllowedContexts[index]) + L" tokens";
            SendMessageW(g.context, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(label.c_str()));
            if (PackConfig::AllowedContexts[index] == g.selectedContext) selectedIndex = index;
        }
        SendMessageW(g.context, CB_SETCURSEL, selectedIndex, 0);
        SendMessageW(g.context, CB_SETITEMHEIGHT, static_cast<WPARAM>(-1), S(28));
        SendMessageW(g.context, CB_SETITEMHEIGHT, 0, S(30));
        g.progress = CreateWindowExW(0, PROGRESS_CLASSW, L"", WS_CHILD | PBS_MARQUEE,
            0, 0, 1, 1, window, reinterpret_cast<HMENU>(ID_PROGRESS), g.instance, nullptr);
        UpdateFontsAndBrushes(); Layout(); SetBusyUi(false);
        return 0;
    }
    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case ID_BROWSE: if (HIWORD(wp) == BN_CLICKED && !g.busy) BrowseProject(); break;
        case ID_START: if (HIWORD(wp) == BN_CLICKED) StartFromUi(false); break;
        case ID_DIRECT: if (HIWORD(wp) == BN_CLICKED) StartFromUi(true); break;
        case ID_STOP:
            if (HIWORD(wp) == BN_CLICKED && g.busy) { RequestStop(); g.status = L"Arrêt de la session…"; EnableWindow(g.stop, FALSE); InvalidateRect(window, nullptr, TRUE); }
            break;
        case ID_DIAGNOSTIC: if (HIWORD(wp) == BN_CLICKED) ShowDiagnostic(); break;
        case ID_LOGS: if (HIWORD(wp) == BN_CLICKED) OpenLogs(); break;
        case ID_GUIDE:
            if (HIWORD(wp) == BN_CLICKED) {
                fs::path guide = g.paths.install / L"wad" / L"docs" / L"LISEZ-MOI.txt";
                if (fs::exists(guide)) ShellExecuteW(window, L"open", guide.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
                else ShowDiagnostic();
            }
            break;
        case ID_THEME:
            if (HIWORD(wp) == BN_CLICKED) { g.dark = !g.dark; SetWindowTextW(g.theme, g.dark ? L"Mode clair" : L"Mode sombre"); UpdateFontsAndBrushes(); SaveSettings(); }
            break;
        }
        return 0;
    case WM_STATUS: {
        std::unique_ptr<std::wstring> status(reinterpret_cast<std::wstring*>(lp));
        if (status && !(g.stopRequested.load() && g.busy.load())) {
            g.status = *status; g.statusError = false; InvalidateRect(window, nullptr, TRUE);
        }
        return 0;
    }
    case WM_FINISHED: {
        std::unique_ptr<SessionResult> result(reinterpret_cast<SessionResult*>(lp));
        if (g.worker.joinable()) g.worker.join();
        g.busy = false;
        if (result) { g.status = result->message; g.statusError = result->error; }
        SetBusyUi(false);
        if (g.pendingClose) DestroyWindow(window);
        else if (result && result->error) MessageBoxW(window, result->message.c_str(), L"OpenCode Local — démarrage interrompu", MB_OK | MB_ICONERROR);
        return 0;
    }
    case WM_PAINT: PaintMain(); return 0;
    case WM_ERASEBKGND: return 1;
    case WM_SIZE: if (g.project) Layout(); return 0;
    case WM_GETMINMAXINFO: {
        auto* info = reinterpret_cast<MINMAXINFO*>(lp);
        info->ptMinTrackSize.x = S(850); info->ptMinTrackSize.y = S(650);
        return 0;
    }
    case WM_DPICHANGED: {
        g.dpi = HIWORD(wp); auto* rect = reinterpret_cast<RECT*>(lp);
        UpdateFontsAndBrushes();
        SendMessageW(g.context, CB_SETITEMHEIGHT, static_cast<WPARAM>(-1), S(28));
        SendMessageW(g.context, CB_SETITEMHEIGHT, 0, S(30));
        SetWindowPos(window, nullptr, rect->left, rect->top, rect->right - rect->left,
                      rect->bottom - rect->top, SWP_NOZORDER | SWP_NOACTIVATE);
        Layout(); return 0;
    }
    case WM_MEASUREITEM: {
        auto* item = reinterpret_cast<MEASUREITEMSTRUCT*>(lp);
        if (item->CtlID == ID_CONTEXT) { item->itemHeight = S(30); return TRUE; }
        break;
    }
    case WM_DRAWITEM: {
        const auto* item = reinterpret_cast<DRAWITEMSTRUCT*>(lp);
        if (item->CtlType == ODT_BUTTON) { DrawButton(*item); return TRUE; }
        if (item->CtlType == ODT_COMBOBOX && item->itemID != static_cast<UINT>(-1)) {
            HBRUSH brush = CreateSolidBrush((item->itemState & ODS_SELECTED) ? Accent() : Input());
            FillRect(item->hDC, &item->rcItem, brush); DeleteObject(brush);
            wchar_t text[128]{}; SendMessageW(item->hwndItem, CB_GETLBTEXT, item->itemID, reinterpret_cast<LPARAM>(text));
            RECT r = item->rcItem; r.left += S(10); SetBkMode(item->hDC, TRANSPARENT);
            SetTextColor(item->hDC, item->itemState & ODS_SELECTED ? RGB(255,255,255) : Foreground());
            auto oldFont = SelectObject(item->hDC, g.font); DrawTextW(item->hDC, text, -1, &r, DT_LEFT | DT_VCENTER | DT_SINGLELINE); SelectObject(item->hDC, oldFont);
            return TRUE;
        }
        break;
    }
    case WM_CTLCOLOREDIT:
    case WM_CTLCOLORLISTBOX:
        SetTextColor(reinterpret_cast<HDC>(wp), Foreground()); SetBkColor(reinterpret_cast<HDC>(wp), Input());
        return reinterpret_cast<LRESULT>(g.editBrush);
    case WM_CTLCOLORSTATIC:
        SetTextColor(reinterpret_cast<HDC>(wp), Foreground()); SetBkColor(reinterpret_cast<HDC>(wp), Panel());
        return reinterpret_cast<LRESULT>(g.panelBrush);
    case WM_CLOSE:
        if (g.busy.load()) {
            if (MessageBoxW(window, L"Fermer le lanceur arrêtera OpenCode, le moteur et leurs tâches en cours.\n\nVoulez-vous arrêter la session ?",
                            kProduct, MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2) != IDYES) return 0;
            // The session may have ended while the modal question processed
            // WM_FINISHED. Do not wait for a completion already delivered.
            if (!g.busy.load()) { SaveSettings(); DestroyWindow(window); return 0; }
            g.pendingClose = true; RequestStop(); EnableWindow(g.stop, FALSE);
            g.status = L"Arrêt de la session avant fermeture…"; InvalidateRect(window, nullptr, TRUE); return 0;
        }
        SaveSettings(); DestroyWindow(window); return 0;
    case WM_DESTROY: PostQuitMessage(0); return 0;
    }
    return DefWindowProcW(window, message, wp, lp);
}

int SmokeTest() {
    Handle job;
    try {
        const int context = *std::min_element(std::begin(PackConfig::AllowedContexts), std::end(PackConfig::AllowedContexts));
        // The validation entry point must not load a second model beside a
        // running interactive session for this profile.
        g.profileLock.reset(CreateFileW((g.paths.profile / L"launcher.lock").c_str(), GENERIC_READ | GENERIC_WRITE,
                                         0, nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr));
        if (!g.profileLock.valid())
            throw std::runtime_error("Une session est déjà ouverte, ou le dossier du profil est inaccessible. Fermez la session avant le test.");
        ValidateFiles(); CheckMemoryBudget(context); PrepareProfile();
        unsigned short port = ChoosePort(); std::string token = RandomToken();
        WriteConfig(port, token, context);
        job = CreateOwnedJob(); SetJobForUi(job.value);
        auto engine = StartEngine(job.value, port, token, context);
        auto response = LocalHttp(port, token, L"POST", L"/v1/chat/completions",
            "{\"model\":\"" + JsonEscape(PackConfig::ModelAliasW) +
            "\",\"messages\":[{\"role\":\"user\",\"content\":\"Respond only with the word OK.\"}],\"max_tokens\":16,\"temperature\":0,\"stream\":false}", 60000);
        SetJobForUi(nullptr); TerminateJobObject(job.value, 0);
        if (response.status != 200 || response.body.find("choices") == std::string::npos)
            throw std::runtime_error("Le test de génération local n'a pas retourné une réponse valide.");
        WriteAtomic(g.paths.logs / L"smoke-test.txt", "PASS: moteur Windows, identite du port et generation locale.\n" + response.body + "\n");
        return 0;
    } catch (const std::exception& ex) {
        SetJobForUi(nullptr); if (job.valid()) TerminateJobObject(job.value, 1);
        try { WriteAtomic(g.paths.logs / L"smoke-test.txt", "FAIL: " + std::string(ex.what()) + "\n"); } catch (...) {}
        return 2;
    }
}

} // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int show) {
    g.instance = instance;
    auto setDpi = OptionalFunction<BOOL(WINAPI*)(HANDLE)>(GetModuleHandleW(L"user32.dll"), "SetProcessDpiAwarenessContext");
    if (setDpi) setDpi(reinterpret_cast<HANDLE>(-4));
    HRESULT com = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    WSADATA sockets{};
    bool socketsReady = WSAStartup(MAKEWORD(2, 2), &sockets) == 0;
    int result = 1;
    try {
        g.paths = FindPaths(); MakeDirectory(g.paths.profile); MakeDirectory(g.paths.logs); LoadSettings();
        int count = 0; LPWSTR* args = CommandLineToArgvW(GetCommandLineW(), &count);
        std::wstring mode = count > 1 ? args[1] : L"";
        if (args) LocalFree(args);
        if (mode == L"/diagnostic" || mode == L"/self-test") {
            bool valid = false;
            WriteAtomic(g.paths.logs / L"diagnostic.txt", "\xEF\xBB\xBF" + Utf8(DiagnosticReport(&valid)));
            result = valid ? 0 : 2;
        } else if (mode == L"/smoke-test") {
            if (!socketsReady) throw std::runtime_error("Winsock n'est pas disponible.");
            result = SmokeTest();
        } else {
            g.profileLock.reset(CreateFileW((g.paths.profile / L"launcher.lock").c_str(), GENERIC_READ | GENERIC_WRITE,
                                             0, nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr));
            if (!g.profileLock.valid()) {
                HWND existing = FindWindowW(kClass, nullptr);
                if (existing) { ShowWindow(existing, SW_RESTORE); SetForegroundWindow(existing); }
                else MessageBoxW(nullptr, L"OpenCode Local est déjà ouvert pour ce profil, ou son dossier est inaccessible.", kProduct, MB_OK | MB_ICONINFORMATION);
                result = 0;
            } else {
                HDC screen = GetDC(nullptr); g.dpi = GetDeviceCaps(screen, LOGPIXELSX); ReleaseDC(nullptr, screen);
                INITCOMMONCONTROLSEX controls{sizeof(controls), ICC_PROGRESS_CLASS | ICC_STANDARD_CLASSES}; InitCommonControlsEx(&controls);
                WNDCLASSEXW windowClass{}; windowClass.cbSize = sizeof(windowClass);
                windowClass.style = CS_HREDRAW | CS_VREDRAW;
                windowClass.lpfnWndProc = WindowProcedure; windowClass.hInstance = instance;
                windowClass.hCursor = LoadCursorW(nullptr, IDC_ARROW);
                windowClass.hIcon = LoadIconW(instance, MAKEINTRESOURCEW(101));
                if (!windowClass.hIcon) windowClass.hIcon = LoadIconW(nullptr, IDI_APPLICATION);
                windowClass.hIconSm = windowClass.hIcon; windowClass.lpszClassName = kClass;
                if (!RegisterClassExW(&windowClass)) Fail(L"Impossible de préparer l'interface");
                RECT rect{0, 0, S(900), S(625)};
                AdjustWindowRectEx(&rect, WS_OVERLAPPEDWINDOW, FALSE, 0);
                HWND window = CreateWindowExW(0, kClass, kProduct, WS_OVERLAPPEDWINDOW,
                    CW_USEDEFAULT, CW_USEDEFAULT, rect.right - rect.left, rect.bottom - rect.top,
                    nullptr, nullptr, instance, nullptr);
                if (!window) Fail(L"Impossible d'ouvrir l'interface");
                if (!socketsReady) { g.status = L"Les liaisons locales ne sont pas disponibles. Utilisez Discussion directe."; g.statusError = true; EnableWindow(g.start, FALSE); }
                ShowWindow(window, show); UpdateWindow(window);
                MSG message{};
                while (GetMessageW(&message, nullptr, 0, 0) > 0) {
                    if (!IsDialogMessageW(window, &message)) { TranslateMessage(&message); DispatchMessageW(&message); }
                }
                if (g.worker.joinable()) { RequestStop(); g.worker.join(); }
                result = static_cast<int>(message.wParam);
            }
        }
    } catch (const std::exception& ex) {
        std::wstring error;
        try { error = Wide(ex.what()); } catch (...) { error = L"Erreur de démarrage."; }
        Log(L"ERREUR : " + error);
        MessageBoxW(nullptr, error.c_str(), kProduct, MB_OK | MB_ICONERROR);
        result = 1;
    }
    for (HFONT font : {g.font, g.titleFont, g.smallFont, g.boldFont}) if (font) DeleteObject(font);
    for (HBRUSH brush : {g.backgroundBrush, g.panelBrush, g.editBrush}) if (brush) DeleteObject(brush);
    if (socketsReady) WSACleanup();
    if (SUCCEEDED(com)) CoUninitialize();
    return result;
}

