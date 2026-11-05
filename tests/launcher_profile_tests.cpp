#include <iostream>
#include <cstddef>

// Exercise the production implementation, not a separate copy of its logic.
#include "../src/launcher.cpp"
#include <winioctl.h>

namespace {

void Require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

void Put(const fs::path& path, const std::string& content) {
    fs::create_directories(path.parent_path());
    std::ofstream file(path, std::ios::binary | std::ios::trunc);
    file.write(content.data(), static_cast<std::streamsize>(content.size()));
    if (!file) throw std::runtime_error("Cannot create fixture file");
}

std::string Read(const fs::path& path) {
    return ReadSmallFile(path, 1024 * 1024);
}

void Junction(const fs::path& link, const fs::path& target) {
    // Directory junctions work without the symbolic-link privilege. Both ends
    // stay inside this test's private temporary directory.
    fs::create_directories(link.parent_path());
    if (!CreateDirectoryW(link.c_str(), nullptr)) Fail(L"Cannot create fixture junction directory");
    const std::wstring print = fs::absolute(target).wstring();
    const std::wstring substitute = L"\\??\\" + print;
    struct MountPoint {
        DWORD tag;
        WORD dataLength, reserved;
        WORD substituteOffset, substituteLength, printOffset, printLength;
        wchar_t path[1];
    };
    const size_t bytes = offsetof(MountPoint, path) +
        (substitute.size() + print.size() + 2) * sizeof(wchar_t);
    std::vector<unsigned char> data(bytes, 0);
    auto* point = reinterpret_cast<MountPoint*>(data.data());
    point->tag = IO_REPARSE_TAG_MOUNT_POINT;
    point->dataLength = static_cast<WORD>(bytes - 8);
    point->substituteLength = static_cast<WORD>(substitute.size() * sizeof(wchar_t));
    point->printOffset = static_cast<WORD>((substitute.size() + 1) * sizeof(wchar_t));
    point->printLength = static_cast<WORD>(print.size() * sizeof(wchar_t));
    std::memcpy(point->path, substitute.c_str(), (substitute.size() + 1) * sizeof(wchar_t));
    std::memcpy(reinterpret_cast<unsigned char*>(point->path) + point->printOffset,
                print.c_str(), (print.size() + 1) * sizeof(wchar_t));
    Handle handle(CreateFileW(link.c_str(), GENERIC_WRITE, 0, nullptr, OPEN_EXISTING,
                               FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, nullptr));
    if (!handle.valid()) Fail(L"Cannot open fixture junction");
    DWORD returned = 0;
    if (!DeviceIoControl(handle.value, FSCTL_SET_REPARSE_POINT, data.data(), static_cast<DWORD>(bytes),
                         nullptr, 0, &returned, nullptr)) Fail(L"Cannot create fixture junction");
}

struct Fixture {
    fs::path root;
    std::vector<fs::path> junctions;
    const fs::path script = fs::path(L"config") / L"opencode" / L"node_modules" /
                            L"@opencode-ai" / L"plugin" / L"dist" / L"index.js";
    std::string scriptContents = "export const fixture = '" + std::string(131089, 'a') + "';\n";

    void SetProfile(const fs::path& profile) {
        g.paths.profile = profile;
        g.paths.runtime = g.paths.profile / L"runtime" / L"fixture-version";
        g.paths.config = g.paths.runtime / L"config";
        g.paths.cache = g.paths.runtime / L"cache";
        g.paths.data = g.paths.profile / L"data";
        g.paths.state = g.paths.profile / L"state";
        g.paths.logs = g.paths.profile / L"logs";
    }

    Fixture() {
        std::vector<wchar_t> temporary(32768);
        DWORD length = GetTempPathW(static_cast<DWORD>(temporary.size()), temporary.data());
        if (!length || length >= temporary.size()) Fail(L"Cannot find fixture temporary directory");
        root = fs::path(std::wstring(temporary.data(), length)) /
            (L"OpenCodeLocal profile test " + std::to_wstring(GetCurrentProcessId()) + L"-" + Wide(RandomToken().substr(0, 12)));
        Require(fs::create_directory(root), "Fixture directory already exists");
        g.paths = {};
        g.paths.install = root / L"install";
        SetProfile(root / L"profile");
        g.paths.seed = root / L"seed";
        g.paths.tools = g.paths.install / L"wad" / L"tools";
        g.window = nullptr;
        g.stopRequested = false;
        Put(g.paths.seed / script, scriptContents);
        Put(g.paths.seed / L"config/opencode/node_modules/@opencode-ai/plugin/package.json", "{\"version\":\"fixture\"}\n");
        Put(g.paths.seed / L"data/opentui/tree-sitter/languages/fixture.wasm", "fixture wasm bytes\n");
    }

    ~Fixture() {
        g.stopRequested = false;
        for (auto it = junctions.rbegin(); it != junctions.rend(); ++it) RemoveDirectoryW(it->c_str());
        std::error_code ignored;
        fs::remove_all(root, ignored);
    }

    fs::path Ready() const { return g.paths.runtime / L"offline-seed.ready"; }
    fs::path CachedScript() const { return g.paths.runtime / script; }

    void Link(const fs::path& link, const fs::path& target) {
        junctions.push_back(link);
        Junction(link, target);
    }
};

void ExpectJunctionRejection() {
    bool rejected = false;
    try { PrepareProfile(); }
    catch (const std::exception& error) { rejected = std::string(error.what()).find("lien") != std::string::npos; }
    Require(rejected, "A junction was not rejected with a path-validation error");
}

struct ScopedEnvironment {
    Environment saved = CurrentEnvironment();
    std::vector<std::wstring> names;
    void Set(const wchar_t* key, const wchar_t* value) {
        names.emplace_back(key);
        if (!SetEnvironmentVariableW(key, value)) Fail(L"Cannot configure fixture environment");
    }
    ~ScopedEnvironment() {
        for (const auto& key : names) {
            auto found = saved.find(key);
            SetEnvironmentVariableW(key.c_str(), found == saved.end() ? nullptr : found->second.c_str());
        }
    }
};

} // namespace

int main() {
    const std::vector<std::pair<std::string, std::function<void()>>> tests = {
        {"initial_seed", [] {
            Fixture fixture;
            PrepareProfile();
            Require(Read(fixture.CachedScript()) == fixture.scriptContents, "Initial JS copy differs from seed");
            Require(Read(g.paths.data / L"opentui/tree-sitter/languages/fixture.wasm") == "fixture wasm bytes\n", "Stable parser data was not seeded");
            Require(fs::is_regular_file(fixture.Ready()), "Ready marker is missing after successful preparation");
        }},
        {"long_source_and_destination_paths", [] {
            Fixture fixture;
            // Keep this explicit case as well as the naturally long temporary
            // paths above. Fixtures embed the unchanged production manifest.
            const auto source = fixture.root / (L"long seed " + std::wstring(160, L's'));
            fs::rename(g.paths.seed, source);
            g.paths.seed = source;
            fixture.SetProfile(fixture.root / (L"long profile " + std::wstring(160, L'p')));
            Require((g.paths.seed / fixture.script).wstring().size() > MAX_PATH, "Source path did not exceed MAX_PATH");
            Require(fixture.CachedScript().wstring().size() > MAX_PATH, "Destination path did not exceed MAX_PATH");
            PrepareProfile();
            Require(Read(fixture.CachedScript()) == fixture.scriptContents, "Long-path seed copy differs");
            Require(fs::is_regular_file(fixture.Ready()), "Long-path preparation did not complete");
        }},
        {"missing_dependency_after_ready", [] {
            Fixture fixture;
            PrepareProfile();
            fs::remove(fixture.CachedScript());
            PrepareProfile();
            Require(Read(fixture.CachedScript()) == fixture.scriptContents, "A missing JS dependency was not repaired");
        }},
        {"same_size_corruption_after_ready", [] {
            Fixture fixture;
            PrepareProfile();
            auto corrupted = fixture.scriptContents;
            corrupted.at(100000) = 'z';
            Put(fixture.CachedScript(), corrupted);
            Require(fs::file_size(fixture.CachedScript()) == fixture.scriptContents.size(), "Corruption fixture changed length");
            PrepareProfile();
            Require(Read(fixture.CachedScript()) == fixture.scriptContents, "Equal-size corruption after the first block was not repaired");
        }},
        {"preserve_unlisted_user_data", [] {
            Fixture fixture;
            PrepareProfile();
            const auto personal = g.paths.data / L"opencode" / L"session-fixture.db";
            const auto extra = g.paths.config / L"unlisted-fixture.txt";
            Put(personal, "personal session proof");
            Put(extra, "unlisted file proof");
            fs::remove(fixture.CachedScript());
            PrepareProfile();
            Require(Read(personal) == "personal session proof", "Session data changed during seed repair");
            Require(Read(extra) == "unlisted file proof", "An unlisted file changed during seed repair");
        }},
        {"reject_destination_junction", [] {
            Fixture fixture;
            PrepareProfile();
            const auto outside = fixture.root / L"outside";
            Put(outside / L"proof.txt", "outside unchanged");
            const auto link = g.paths.config / L"opencode";
            fs::remove_all(link);
            fixture.Link(link, outside);
            ExpectJunctionRejection();
            Require(Read(outside / L"proof.txt") == "outside unchanged", "Outside proof changed");
            Require(!fs::exists(outside / L"node_modules"), "Seed files escaped through a destination junction");
            Require(!fs::exists(fixture.Ready()), "Failed preparation left a ready marker");
        }},
        {"reject_private_profile_root_junction", [] {
            Fixture fixture;
            const auto outside = fixture.root / L"outside";
            Put(outside / L"proof.txt", "outside unchanged");
            fixture.Link(g.paths.profile, outside);
            ExpectJunctionRejection();
            Require(Read(outside / L"proof.txt") == "outside unchanged", "Outside proof changed");
            Require(!fs::exists(outside / L"runtime"), "Preparation escaped through the profile root junction");
        }},
        {"reject_seed_root_junction", [] {
            Fixture fixture;
            const auto actualSeed = fixture.root / L"actual-seed";
            fs::rename(g.paths.seed, actualSeed);
            fixture.Link(g.paths.seed, actualSeed);
            ExpectJunctionRejection();
            Require(!fs::exists(fixture.CachedScript()), "A seed-root junction was traversed");
            Require(Read(actualSeed / fixture.script) == fixture.scriptContents, "Seed source changed");
            Require(!fs::exists(fixture.Ready()), "Rejected seed left a ready marker");
        }},
        {"allow_redirected_ancestor_above_private_profile", [] {
            Fixture fixture;
            const auto actualFolder = fixture.root / L"actual-known-folder";
            fs::create_directory(actualFolder);
            const auto redirectedFolder = fixture.root / L"redirected-known-folder";
            fixture.Link(redirectedFolder, actualFolder);
            fixture.SetProfile(redirectedFolder / L"private-profile");
            PrepareProfile();
            Require(Read(fixture.CachedScript()) == fixture.scriptContents, "A legitimate redirected ancestor was rejected");
            Require(fs::is_regular_file(fixture.Ready()), "Redirected known folder did not complete preparation");
        }},
        {"cancellation_invalidates_ready", [] {
            Fixture fixture;
            PrepareProfile();
            g.stopRequested = true;
            bool cancelled = false;
            try { PrepareProfile(); } catch (const Cancelled&) { cancelled = true; }
            g.stopRequested = false;
            Require(cancelled, "Preparation ignored cancellation");
            Require(!fs::exists(fixture.Ready()), "Cancelled preparation left a ready marker");
            Require(Read(fixture.CachedScript()) == fixture.scriptContents, "Cancellation damaged an existing dependency");
        }},
        {"isolated_runtime_overrides", [] {
            Fixture fixture;
            ScopedEnvironment scope;
            scope.Set(L"gGmL_bAcKeNd_PaTh", L"fixture-backend.dll");
            scope.Set(L"BUN_OPTIONS", L"--preload ./fixture.js");
            scope.Set(L"BUN_BE_BUN", L"1");
            scope.Set(L"BUN_INSPECT_PRELOAD", L"./fixture.js");
            scope.Set(L"OPENCODE_CONFIG", L"unrelated-config.json");
            scope.Set(L"LLAMA_ARG_MODEL", L"unrelated-model.gguf");
            scope.Set(L"OPENCODE_LOCAL_FIXTURE_KEEP", L"discarded namespace override");
            scope.Set(L"PROJECT_FIXTURE_SETTING", L"preserved build setting");
            const auto child = ChildEnvironment();
            for (const auto* key : {L"GGML_BACKEND_PATH", L"BUN_OPTIONS", L"BUN_BE_BUN", L"BUN_INSPECT_PRELOAD", L"OPENCODE_CONFIG", L"LLAMA_ARG_MODEL", L"OPENCODE_LOCAL_FIXTURE_KEEP"})
                Require(child.find(key) == child.end(), "A runtime or provider override leaked into the child");
            Require(child.at(L"PROJECT_FIXTURE_SETTING") == L"preserved build setting", "An unrelated build setting was removed");
            Require(CurrentEnvironment().at(L"BUN_BE_BUN") == L"1", "The parent process environment was modified");
        }}
    };
    bool success = true;
    std::vector<std::string> records;
    for (const auto& test : tests) {
        try {
            test.second();
            records.push_back("{\"name\":\"" + test.first + "\",\"passed\":true}");
        } catch (const std::exception& error) {
            success = false;
            records.push_back("{\"name\":\"" + test.first + "\",\"passed\":false,\"detail\":\"" + JsonEscape(Wide(error.what())) + "\"}");
        }
    }
    std::cout << "{\"passed\":" << (success ? "true" : "false") << ",\"checks\":[";
    for (size_t index = 0; index < records.size(); ++index) {
        if (index) std::cout << ',';
        std::cout << records[index];
    }
    std::cout << "]}\n";
    return success ? 0 : 1;
}
