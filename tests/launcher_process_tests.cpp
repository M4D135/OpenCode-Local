// Real Windows process fixtures. No model, installation, or security setting is used.
// The production source is included unchanged. This test-only API wrapper lets
// the observer stop a helper at the exact return boundary of CreateProcessW.
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
#include <windows.h>
#include <iostream>

namespace fixture {
BOOL WINAPI CreateProcessObserved(LPCWSTR, LPWSTR, LPSECURITY_ATTRIBUTES,
    LPSECURITY_ATTRIBUTES, BOOL, DWORD, LPVOID, LPCWSTR, LPSTARTUPINFOW,
    LPPROCESS_INFORMATION);
}

#define CreateProcessW fixture::CreateProcessObserved
#include "../src/launcher.cpp"
#undef CreateProcessW

namespace fixture {

enum class Hook { None, CancelAfterCreate, CancelOnCreateFailure, StopAfterCreate };
Hook hook = Hook::None;
unsigned createCalls = 0;
Handle observedChild;
HANDLE childCreated = nullptr, releaseCreate = nullptr;
struct SharedChild { volatile LONG pid; };
SharedChild* sharedChild = nullptr;

BOOL WINAPI CreateProcessObserved(LPCWSTR application, LPWSTR command,
    LPSECURITY_ATTRIBUTES processSecurity, LPSECURITY_ATTRIBUTES threadSecurity,
    BOOL inherit, DWORD flags, LPVOID environment, LPCWSTR directory,
    LPSTARTUPINFOW startup, LPPROCESS_INFORMATION information) {
    ++createCalls;
    BOOL created = ::CreateProcessW(application, command, processSecurity,
        threadSecurity, inherit, flags, environment, directory, startup, information);
    DWORD error = GetLastError();
    if (!created && hook == Hook::CancelOnCreateFailure) g.stopRequested = true;
    if (created && hook == Hook::CancelAfterCreate) {
        HANDLE copy = nullptr;
        if (DuplicateHandle(GetCurrentProcess(), information->hProcess,
            GetCurrentProcess(), &copy, 0, FALSE, DUPLICATE_SAME_ACCESS))
            observedChild.reset(copy);
        g.stopRequested = true;
    } else if (created && hook == Hook::StopAfterCreate) {
        InterlockedExchange(&sharedChild->pid, static_cast<LONG>(information->dwProcessId));
        SetEvent(childCreated);
        // Only the fixture waits here. The observer kills this helper while
        // the child is still suspended, before production StartProcess resumes.
        WaitForSingleObject(releaseCreate, 30000);
    }
    SetLastError(error);
    return created;
}

struct MappedView {
    void* value = nullptr;
    explicit MappedView(HANDLE mapping, DWORD access)
        : value(MapViewOfFile(mapping, access, 0, 0, sizeof(SharedChild))) {
        if (!value) Fail(L"MapViewOfFile fixture");
    }
    ~MappedView() { if (value) UnmapViewOfFile(value); }
    MappedView(const MappedView&) = delete;
    MappedView& operator=(const MappedView&) = delete;
};

void Require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

void WaitEnded(HANDLE process, DWORD milliseconds = 10000) {
    Require(WaitForSingleObject(process, milliseconds) == WAIT_OBJECT_0,
            "A fixture process did not terminate within the deadline");
}

DWORD ExitCode(HANDLE process) {
    DWORD code = 0;
    Require(GetExitCodeProcess(process, &code) != FALSE, "GetExitCodeProcess failed");
    return code;
}

DWORD ActiveProcesses(HANDLE job) {
    JOBOBJECT_BASIC_ACCOUNTING_INFORMATION info{};
    Require(QueryInformationJobObject(job, JobObjectBasicAccountingInformation,
        &info, sizeof(info), nullptr) != FALSE, "Cannot inspect the fixture job");
    return info.ActiveProcesses;
}

std::vector<std::wstring> Arguments() {
    int count = 0;
    LPWSTR* values = CommandLineToArgvW(GetCommandLineW(), &count);
    if (!values) Fail(L"CommandLineToArgvW fixture");
    std::vector<std::wstring> arguments(values, values + count);
    LocalFree(values);
    return arguments;
}

constexpr const wchar_t* kQuotedArgument = L"argument avec espaces et \"guillemets\" \\fin\\";

int ChildMode(const std::vector<std::wstring>& args) {
    if (args[1] == L"--child-wait") { Sleep(INFINITE); return 91; }
    if (args[1] != L"--child-streams" || args.size() != 4) return 92;
    HANDLE extra = reinterpret_cast<HANDLE>(static_cast<uintptr_t>(std::stoull(args[2])));
    // Only the observer's event state identifies accidental inheritance. A
    // coincidentally reused numeric handle in this child cannot fool that check.
    SetEvent(extra);
    if (args[3] != kQuotedArgument) return 93;
    HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
    if (GetFileType(input) != FILE_TYPE_CHAR) return 94;
    char byte = 0;
    DWORD read = 99;
    if (!ReadFile(input, &byte, 1, &read, nullptr) || read != 0) return 95;
    const char output[] = "stdout-ok\n", error[] = "stderr-ok\n";
    DWORD written = 0;
    if (!WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), output, sizeof(output) - 1, &written, nullptr) ||
        written != sizeof(output) - 1) return 96;
    if (!WriteFile(GetStdHandle(STD_ERROR_HANDLE), error, sizeof(error) - 1, &written, nullptr) ||
        written != sizeof(error) - 1) return 97;
    return 0;
}

int SupervisorMode(const std::vector<std::wstring>& args) {
    Require(args.size() == 6, "Invalid supervisor arguments");
    Handle mapping(OpenFileMappingW(FILE_MAP_READ | FILE_MAP_WRITE, FALSE, args[3].c_str()));
    Handle ready(OpenEventW(EVENT_MODIFY_STATE, FALSE, args[4].c_str()));
    Handle release(OpenEventW(SYNCHRONIZE, FALSE, args[5].c_str()));
    Require(mapping.valid() && ready.valid() && release.valid(), "Cannot open observer fixtures");
    MappedView view(mapping.value, FILE_MAP_READ | FILE_MAP_WRITE);
    sharedChild = static_cast<SharedChild*>(view.value);
    childCreated = ready.value;
    releaseCreate = release.value;
    hook = Hook::StopAfterCreate;
    Handle ownJob = CreateOwnedJob();
    fs::path executable = ExePath();
    if (args[2] == L"legacy") {
        // Negative control: the previous two-call design, confined to this
        // helper. Its observer owns an outer job and cleans up the orphan.
        std::wstring command = CommandLine(executable, {L"--child-wait"});
        std::vector<wchar_t> writable(command.begin(), command.end());
        writable.push_back(L'\0');
        STARTUPINFOW startup{};
        startup.cb = sizeof(startup);
        PROCESS_INFORMATION information{};
        BOOL created = CreateProcessObserved(executable.c_str(), writable.data(),
            nullptr, nullptr, FALSE, CREATE_SUSPENDED | CREATE_NO_WINDOW,
            nullptr, executable.parent_path().c_str(), &startup, &information);
        Require(created != FALSE, "Legacy negative-control creation failed");
        Handle process(information.hProcess), thread(information.hThread);
        // Reached only if the observer failed to stop us at the boundary.
        TerminateProcess(process.value, 90);
        WaitEnded(process.value);
    } else {
        auto child = StartProcess(executable, {L"--child-wait"}, executable.parent_path(),
            CurrentEnvironment(), ownJob.value, false);
        TerminateJobObject(ownJob.value, 90);
        WaitEnded(child.handle.value);
    }
    return 90;
}

struct Row { std::string name, detail; bool passed = false; };
std::vector<Row> rows;

void Check(const char* name, const std::function<void()>& test) {
    Row row{name, "", false};
    hook = Hook::None;
    createCalls = 0;
    g.stopRequested = false;
    try { test(); row.passed = true; }
    catch (const std::exception& error) { row.detail = error.what(); }
    catch (...) { row.detail = "Unexpected non-standard exception"; }
    SetJobForUi(nullptr);
    g.stopRequested = false;
    hook = Hook::None;
    observedChild.reset();
    rows.push_back(std::move(row));
}

std::string JsonString(const std::string& value) {
    std::string result = "\"";
    constexpr char hex[] = "0123456789abcdef";
    for (unsigned char c : value) {
        if (c == '"' || c == '\\') { result += '\\'; result += static_cast<char>(c); }
        else if (c < 32) { result += "\\u00"; result += hex[c >> 4]; result += hex[c & 15]; }
        else result += static_cast<char>(c);
    }
    return result + "\"";
}

void StopSupervisorAtCreation(const fs::path& work, bool legacy) {
    const std::wstring name = L"Local\\OpenCodeLocal-process-fixture-" +
        std::to_wstring(GetCurrentProcessId()) + L"-" + Wide(RandomToken());
    const std::wstring mapName = name + L"-map", readyName = name + L"-ready", releaseName = name + L"-release";
    Handle mapping(CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE,
                                     0, sizeof(SharedChild), mapName.c_str()));
    Handle ready(CreateEventW(nullptr, TRUE, FALSE, readyName.c_str()));
    Handle release(CreateEventW(nullptr, TRUE, FALSE, releaseName.c_str()));
    Require(mapping.valid() && ready.valid() && release.valid(), "Cannot create observer objects");
    MappedView view(mapping.value, FILE_MAP_READ | FILE_MAP_WRITE);
    auto* shared = static_cast<SharedChild*>(view.value);
    InterlockedExchange(&shared->pid, 0);
    // This outer job guarantees cleanup even if a test assertion fails. It is
    // held by the observer, not the supervisor whose lifetime we exercise.
    Handle outerJob = CreateOwnedJob();
    auto supervisor = StartProcess(ExePath(), {L"--supervisor", legacy ? L"legacy" : L"atomic",
        mapName, readyName, releaseName}, work, CurrentEnvironment(), outerJob.value, false);
    HANDLE outcomes[] = {ready.value, supervisor.handle.value};
    Require(WaitForMultipleObjects(2, outcomes, FALSE, 15000) == WAIT_OBJECT_0,
            "Supervisor did not reach the CreateProcessW boundary");
    DWORD childId = static_cast<DWORD>(InterlockedCompareExchange(&shared->pid, 0, 0));
    Require(childId != 0, "Supervisor did not record its child's PID");
    Handle child(OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE,
                             FALSE, childId));
    Require(child.valid(), "Cannot inspect the suspended child");
    Require(TerminateProcess(supervisor.handle.value, 77) != FALSE, "Cannot stop the fixture supervisor");
    WaitEnded(supervisor.handle.value);
    if (legacy) {
        Require(WaitForSingleObject(child.value, 300) == WAIT_TIMEOUT,
                "Negative control did not reproduce the former orphan gap");
        Require(TerminateProcess(child.value, 78) != FALSE, "Cannot clean up the negative-control child");
        WaitEnded(child.value);
    } else {
        WaitEnded(child.value);
    }
    Require(ActiveProcesses(outerJob.value) == 0, "A fixture descendant remains active");
}

int Suite() {
    fs::path work = fs::temp_directory_path() /
        (L"OpenCodeLocal-process-fixtures-" + std::to_wstring(GetCurrentProcessId()) + L"-" + Wide(RandomToken()));
    fs::create_directories(work);
    g.paths.logs = work / L"logs";
    Check("only_stdin_and_log_handles_are_inherited", [&] {
        fs::path directory = work / L"projet été 旅";
        fs::create_directories(directory);
        SECURITY_ATTRIBUTES security{sizeof(security), nullptr, TRUE};
        Handle sentinel(CreateEventW(&security, TRUE, FALSE, nullptr));
        Require(sentinel.valid(), "Cannot create the inheritable sentinel");
        Handle job = CreateOwnedJob();
        fs::path output = directory / L"output.log";
        auto child = StartProcess(ExePath(), {L"--child-streams",
            std::to_wstring(reinterpret_cast<uintptr_t>(sentinel.value)), kQuotedArgument},
            directory, CurrentEnvironment(), job.value, false, output);
        WaitEnded(child.handle.value);
        Require(ExitCode(child.handle.value) == 0, "The child rejected stdin, arguments, or log output");
        Require(ReadSmallFile(output) == "stdout-ok\nstderr-ok\n", "Both streams did not reach the log");
        Require(WaitForSingleObject(sentinel.value, 0) == WAIT_TIMEOUT,
                "An unrelated inheritable event reached the child");
    });
    Check("child_is_in_job_and_closing_job_terminates_it", [&] {
        Handle job = CreateOwnedJob();
        auto child = StartProcess(ExePath(), {L"--child-wait"}, work,
                                  CurrentEnvironment(), job.value, true);
        BOOL member = FALSE;
        Require(IsProcessInJob(child.handle.value, job.value, &member) && member,
                "The launched child is outside its session job");
        Require(ActiveProcesses(job.value) == 1, "The session job does not own exactly one child");
        job.reset();
        WaitEnded(child.handle.value);
    });
    Check("request_stop_terminates_the_owned_child", [&] {
        Handle job = CreateOwnedJob();
        auto child = StartProcess(ExePath(), {L"--child-wait"}, work,
                                  CurrentEnvironment(), job.value, false);
        SetJobForUi(job.value);
        RequestStop();
        SetJobForUi(nullptr);
        WaitEnded(child.handle.value);
        Require(g.stopRequested.load(), "Stop request was not retained");
    });
    Check("cancellation_before_creation_starts_no_process", [&] {
        Handle job = CreateOwnedJob();
        g.stopRequested = true;
        bool cancelled = false;
        try { StartProcess(ExePath(), {L"--child-wait"}, work, CurrentEnvironment(), job.value, false); }
        catch (const Cancelled&) { cancelled = true; }
        Require(cancelled && createCalls == 0 && ActiveProcesses(job.value) == 0,
                "Pre-launch cancellation created a process or was not reported");
    });
    Check("cancellation_after_creation_terminates_suspended_child", [&] {
        Handle job = CreateOwnedJob();
        hook = Hook::CancelAfterCreate;
        bool cancelled = false;
        try { StartProcess(ExePath(), {L"--child-wait"}, work, CurrentEnvironment(), job.value, false); }
        catch (const Cancelled&) { cancelled = true; }
        Require(cancelled && observedChild.valid(), "Post-creation cancellation was not observed");
        WaitEnded(observedChild.value);
        Require(ActiveProcesses(job.value) == 0, "Cancelled child remains in the job");
    });
    Check("missing_executable_fails_without_a_child", [&] {
        Handle job = CreateOwnedJob();
        bool failed = false;
        try { StartProcess(work / L"absent.exe", {}, work, CurrentEnvironment(), job.value, false); }
        catch (const std::exception&) { failed = true; }
        Require(failed && createCalls == 1 && ActiveProcesses(job.value) == 0,
                "Missing executable was not rejected cleanly");
    });
    Check("missing_working_directory_fails_without_a_child", [&] {
        Handle job = CreateOwnedJob();
        bool failed = false;
        try { StartProcess(ExePath(), {L"--child-wait"}, work / L"absent", CurrentEnvironment(), job.value, false); }
        catch (const std::exception&) { failed = true; }
        Require(failed && createCalls == 1 && ActiveProcesses(job.value) == 0,
                "Missing working directory was not rejected cleanly");
    });
    Check("creation_failure_during_stop_is_reported_as_cancelled", [&] {
        Handle job = CreateOwnedJob();
        hook = Hook::CancelOnCreateFailure;
        bool cancelled = false;
        try { StartProcess(work / L"absent.exe", {}, work, CurrentEnvironment(), job.value, false); }
        catch (const Cancelled&) { cancelled = true; }
        Require(cancelled && createCalls == 1 && ActiveProcesses(job.value) == 0,
                "A concurrent stop was reported as a launch error");
    });
    Check("invalid_job_fails_before_creation", [&] {
        bool failed = false;
        try { StartProcess(ExePath(), {L"--child-wait"}, work, CurrentEnvironment(), nullptr, false); }
        catch (const std::exception&) { failed = true; }
        Require(failed && createCalls == 0, "An unowned child was allowed");
    });
    Check("negative_control_former_gap_leaves_a_suspended_orphan", [&] {
        StopSupervisorAtCreation(work, true);
    });
    Check("parent_exit_at_creation_leaves_no_orphan", [&] {
        StopSupervisorAtCreation(work, false);
    });
    Check("fixture_cleanup", [&] { fs::remove_all(work); });
    bool passed = std::all_of(rows.begin(), rows.end(), [](const Row& row) { return row.passed; });
    std::cout << "{\"passed\":" << (passed ? "true" : "false") << ",\"checks\":[";
    for (size_t i = 0; i < rows.size(); ++i) {
        if (i) std::cout << ',';
        const auto& row = rows[i];
        std::cout << "{\"name\":" << JsonString(row.name) << ",\"passed\":" <<
            (row.passed ? "true" : "false") << ",\"detail\":" << JsonString(row.detail) << '}';
    }
    std::cout << "]}\n";
    return passed ? 0 : 1;
}

} // namespace fixture

int main() {
    try {
        auto args = fixture::Arguments();
        if (args.size() > 1 && args[1].rfind(L"--child-", 0) == 0) return fixture::ChildMode(args);
        if (args.size() > 1 && args[1] == L"--supervisor") return fixture::SupervisorMode(args);
        return fixture::Suite();
    } catch (const std::exception& error) {
        std::cout << "{\"passed\":false,\"checks\":[{\"name\":\"fixture_setup\",\"passed\":false,\"detail\":"
            << fixture::JsonString(error.what()) << "}]}\n";
        return 1;
    }
}
