#!/usr/bin/env python3
"""Run the pinned MoE with the Linux counterpart of the shipped Windows engine.

This is a bounded functional/memory smoke test, not a quality benchmark or a
Windows RAM qualification. The build workflow must verify the model's pinned
SHA-256 before calling this script; the multi-GB hash is not repeated here.
With --opencode, the real harness must read an unknown proof from a temporary
file and reject a separate write request without interactive permission.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

from pack_profiles import ROOT, configure_opencode, load_pack


GIB = 1024**3
MAX_RSS_BYTES = 14 * GIB


class DeadlineExpired(TimeoutError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_report(path: Path, report: dict, secret: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False, indent=2).replace(secret, "<redacted>") + "\n"
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def small_tail(path: Path, maximum: int = 12000) -> str:
    if not path.is_file():
        return ""
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - maximum))
        return stream.read(maximum).decode("utf-8", "replace")


def process_status(pid: int) -> dict[str, int]:
    result = {}
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            fields = line.split()
            if fields and fields[0] in {"VmRSS:", "VmHWM:", "VmSwap:"}:
                result[fields[0][:-1]] = int(fields[1]) * 1024
    except (OSError, ValueError):
        pass
    return result


def host_memory() -> dict[str, int]:
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        fields = line.split()
        if fields[0] in {"MemTotal:", "MemAvailable:", "SwapTotal:", "SwapFree:"}:
            values[fields[0][:-1]] = int(fields[1]) * 1024
    return values


def model_mapping_rss(pid: int, model: Path) -> tuple[int, int]:
    """Return total smaps RSS and this model's RSS, from one consistent scan."""
    total = mapped = 0
    current_model = False
    with Path(f"/proc/{pid}/smaps").open() as stream:
        for line in stream:
            if re.match(r"^[0-9a-f]+-[0-9a-f]+\s", line):
                fields = line.rstrip().split(maxsplit=5)
                filename = fields[5] if len(fields) == 6 else ""
                filename = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), filename)
                current_model = filename == str(model)
            elif line.startswith("Rss:"):
                count = int(line.split()[1]) * 1024
                total += count
                if current_model:
                    mapped += count
    return total, mapped


class MemoryMonitor:
    """Sample our process groups only; do not count unrelated runner processes."""

    def __init__(self, model: Path, model_bytes: int):
        require(process_status(os.getpid()).get("VmRSS", 0) > 0,
                "Linux /proc does not expose this process namespace; RSS measurement is unavailable")
        self.model = model
        self.model_bytes = model_bytes
        self.groups: dict[int, str] = {}
        self.engine_pid: int | None = None
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.result = {
            "measurement": "Linux /proc, sampled every 0.5 seconds; RSS sums may count shared pages twice",
            "sample_interval_seconds": 0.5,
            "samples": 0,
            "peak_engine_rss_bytes": 0,
            "peak_engine_hwm_bytes": 0,
            "peak_opencode_rss_bytes": 0,
            "peak_combined_rss_bytes": 0,
            "peak_validator_rss_bytes": 0,
            "peak_including_validator_rss_bytes": 0,
            "peak_model_mapped_rss_bytes": 0,
            "peak_full_residency_estimate_bytes": 0,
            "full_residency_estimate": "All GGUF bytes + sampled engine non-model RSS + OpenCode process-tree RSS; estimate, not a measured Windows peak",
            "rss_budget_bytes": MAX_RSS_BYTES,
            "host_initial": host_memory(),
            "minimum_host_available_bytes": host_memory()["MemAvailable"],
            "peak_tracked_swap_bytes": 0,
            "errors": [],
        }
        self.cgroup = Path("/sys/fs/cgroup")
        self.result["cgroup"] = self._cgroup()

    def _cgroup(self) -> dict:
        result = {}
        for key, filename in (("limit_bytes", "memory.max"), ("current_bytes", "memory.current"),
                              ("recorded_peak_bytes", "memory.peak")):
            try:
                value = (self.cgroup / filename).read_text().strip()
                result[key] = int(value) if value.isdigit() else value
            except OSError:
                pass
        result["scope"] = "Entire cgroup, including other runner processes and file cache"
        return result

    def add(self, process: subprocess.Popen, label: str) -> None:
        with self.lock:
            self.groups[process.pid] = label
            if label == "engine":
                self.engine_pid = process.pid

    def remove(self, process: subprocess.Popen) -> None:
        with self.lock:
            self.groups.pop(process.pid, None)
            if self.engine_pid == process.pid:
                self.engine_pid = None

    def start(self) -> None:
        self.thread.start()

    def sample(self) -> None:
        with self.lock:
            groups = dict(self.groups)
            engine_pid = self.engine_pid
        totals = {"engine": 0, "opencode": 0}
        engine_root_rss = 0
        high_water = swap = 0
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text()
                fields = stat[stat.rfind(")") + 2:].split()
                label = groups.get(int(fields[2]))  # process-group ID
                if label is None:
                    continue
                status = process_status(int(entry.name))
                rss = status.get("VmRSS", 0)
                totals[label] += rss
                swap += status.get("VmSwap", 0)
                if int(entry.name) == engine_pid:
                    engine_root_rss = rss
                    high_water = status.get("VmHWM", 0)
            except (OSError, ValueError, IndexError):
                continue
        model_rss = full_estimate = 0
        if engine_pid is not None:
            try:
                engine_smaps_rss, model_rss = model_mapping_rss(engine_pid, self.model)
                full_estimate = (self.model_bytes + max(0, engine_smaps_rss - model_rss)
                                 + max(0, totals["engine"] - engine_root_rss) + totals["opencode"])
            except FileNotFoundError:
                pass  # normal process exit between samples
        validator_rss = process_status(os.getpid()).get("VmRSS", 0)
        combined = totals["engine"] + totals["opencode"]
        current_host = host_memory()
        with self.lock:
            r = self.result
            r["samples"] += 1
            for name, value in (
                ("peak_engine_rss_bytes", totals["engine"]),
                ("peak_engine_hwm_bytes", high_water),
                ("peak_opencode_rss_bytes", totals["opencode"]),
                ("peak_combined_rss_bytes", combined),
                ("peak_validator_rss_bytes", validator_rss),
                ("peak_including_validator_rss_bytes", combined + validator_rss),
                ("peak_model_mapped_rss_bytes", model_rss),
                ("peak_full_residency_estimate_bytes", full_estimate),
                ("peak_tracked_swap_bytes", swap),
            ):
                r[name] = max(r[name], value)
            r["minimum_host_available_bytes"] = min(r["minimum_host_available_bytes"], current_host["MemAvailable"])

    def _loop(self) -> None:
        while not self.stopped.is_set():
            try:
                self.sample()
            except Exception as error:
                with self.lock:
                    detail = str(error)
                    if detail not in self.result["errors"]:
                        self.result["errors"].append(detail)
            self.stopped.wait(0.5)

    def snapshot(self) -> dict:
        with self.lock:
            result = json.loads(json.dumps(self.result))
        result["cgroup_final"] = self._cgroup()
        return result

    def stop(self) -> None:
        self.stopped.set()
        if self.thread.is_alive():
            self.thread.join(timeout=3)


class Runtime:
    def __init__(self, deadline: float, monitor: MemoryMonitor):
        self.deadline = deadline
        self.monitor = monitor
        self.processes: list[subprocess.Popen] = []

    def remaining(self, maximum: float = 1200) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise DeadlineExpired("Global runtime validation deadline reached")
        return min(maximum, remaining)

    def launch(self, command: list[str], *, label: str, cwd: Path, env: dict,
               stdout, stderr) -> subprocess.Popen:
        self.remaining()
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, start_new_session=True)
        self.processes.append(process)
        self.monitor.add(process, label)
        return process

    def terminate(self, process: subprocess.Popen) -> None:
        # Every managed process gets its own group at launch. Never terminate
        # by executable name, port, or a PID obtained outside this test.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        # The leader may already have exited while its children are still
        # running. Kill the remaining owned group, not only a live leader.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)
        self.monitor.remove(process)
        if process in self.processes:
            self.processes.remove(process)

    def cleanup(self) -> None:
        for process in list(reversed(self.processes)):
            self.terminate(process)


class LocalAPI:
    def __init__(self, runtime: Runtime, port: int, key: str, alias: str):
        self.runtime = runtime
        self.base = f"http://127.0.0.1:{port}"
        self.key = key
        self.alias = alias
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path: str, payload: dict | None = None, timeout: int = 300) -> dict:
        request = urllib.request.Request(
            self.base + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=self.runtime.remaining(timeout)) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
            require(len(raw) <= 2 * 1024 * 1024, "Unexpectedly large local API response")
            return json.loads(raw)
        except urllib.error.HTTPError as error:
            detail = error.read(4000).decode("utf-8", "replace")
            raise RuntimeError(f"Local API HTTP {error.code}: {detail}") from error

    def chat(self, messages: list[dict], *, tokens: int, **options) -> dict:
        return self.request("/v1/chat/completions", {
            "model": self.alias, "messages": messages, "temperature": 0,
            "seed": 173, "max_tokens": tokens, "stream": False, **options,
        })


def child_environment(root: Path, engine: Path) -> dict[str, str]:
    # Do not expose CI credentials or ambient provider overrides to the model
    # or harness. XDG paths isolate all generated configuration and state.
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TERM", "TZ") if key in os.environ}
    env["LD_LIBRARY_PATH"] = str(engine.parent)
    env["NO_PROXY"] = "127.0.0.1,localhost,::1"
    env["OTEL_SDK_DISABLED"] = "true"
    env["npm_config_offline"] = "true"
    env["npm_config_audit"] = "false"
    env["npm_config_fund"] = "false"
    env["CI"] = "true"
    env["NO_COLOR"] = "1"
    for name in ("AUTOUPDATE", "MODELS_FETCH", "DEFAULT_PLUGINS", "LSP_DOWNLOAD",
                 "EXTERNAL_SKILLS", "CLAUDE_CODE", "PROJECT_CONFIG"):
        env["OPENCODE_DISABLE_" + name] = "1"
    env["OPENCODE_PURE"] = "1"
    for kind in ("config", "cache", "data", "state"):
        directory = root / kind
        directory.mkdir(parents=True, exist_ok=True)
        env[f"XDG_{kind.upper()}_HOME"] = str(directory)
    return env


def engine_arguments(engine: Path, model: Path, pack: dict, context: int,
                     port: int, key: str) -> list[str]:
    return [str(engine), "--model", str(model), "--ctx-size", str(context),
            "--n-gpu-layers", "0", "--load-mode", "mmap", "--no-repack",
            "--threads", "4", "--threads-batch", "4",
            "--batch-size", str(pack["batch_size"]), "--ubatch-size", str(pack["micro_batch_size"]),
            "--cache-type-k", pack["cache_type_k"], "--cache-type-v", pack["cache_type_v"],
            "--flash-attn", "on" if pack["flash_attention"] else "off",
            "--parallel", "1", "--n-predict", str(min(pack["output_tokens"], context // 2)),
            "--jinja", "--reasoning", "off", "--reasoning-budget", "0", "--offline",
            "--alias", pack["model"]["alias"], "--no-webui", "--cache-ram", "0",
            "--ctx-checkpoints", "1", "--host", "127.0.0.1", "--port", str(port), "--api-key", key]


def response_message(response: dict) -> dict:
    choices = response.get("choices")
    require(isinstance(choices, list) and bool(choices), "No chat completion choice returned")
    message = choices[0].get("message")
    require(isinstance(message, dict), "No assistant message returned")
    return message


def seed_opencode(seed: Path, profile: Path, pack: dict, context: int,
                  api: LocalAPI, env: dict) -> dict:
    require((seed / "seed-manifest.json").is_file(), "Verified offline seed is missing")
    require((seed / "config/opencode/node_modules/@opencode-ai/plugin/package.json").is_file(),
            "Offline OpenCode JavaScript dependencies are missing")
    for kind in ("config", "cache", "data", "state"):
        if (seed / kind).is_dir():
            shutil.copytree(seed / kind, profile / kind, dirs_exist_ok=True)
    template = configure_opencode((ROOT / "config/opencode.json.template").read_text(encoding="utf-8"), pack)
    substitutions = {
        "BASE_URL": api.base + "/v1", "API_KEY": api.key, "CONTEXT": context,
        "OUTPUT": min(pack["output_tokens"], context // 2),
        "COMPACTION_RESERVED": min(1024, context // 8),
        "COMPACTION_PRESERVE": min(2048, context // 4),
    }
    for key, value in substitutions.items():
        template = template.replace("{{" + key + "}}", str(value))
    require("{{" not in template, "Unresolved OpenCode configuration placeholders")
    config = json.loads(template)
    # The only platform adaptation: the Linux smoke uses the system shell.
    # Its use still requires the original 'ask' permission and is not approved.
    config["shell"] = "/bin/bash"
    require(config["permission"]["edit"] == "ask" and config["permission"]["bash"] == "ask",
            "Production write/shell permission prompts must be preserved")
    require(config["permission"]["webfetch"] == "deny" and config["permission"]["websearch"] == "deny",
            "External model tools must stay disabled")
    config_path = profile / "config/opencode/opencode.json"
    text = json.dumps(config, ensure_ascii=False)
    config_path.write_text(text, encoding="utf-8")
    config_path.chmod(0o600)
    env["OPENCODE_CONFIG"] = str(config_path)
    env["OPENCODE_CONFIG_CONTENT"] = text
    return config


def run_opencode(runtime: Runtime, binary: Path, command: list[str], project: Path,
                 env: dict, prefix: Path, timeout: int = 420) -> dict:
    stdout_path, stderr_path = prefix.with_suffix(".stdout"), prefix.with_suffix(".stderr")
    started = time.monotonic()
    timed_out = False
    code = None
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = runtime.launch([str(binary), *command], label="opencode", cwd=project,
                                 env=env, stdout=stdout, stderr=stderr)
        try:
            code = process.wait(timeout=runtime.remaining(timeout))
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            runtime.terminate(process)
    text = stdout_path.read_text(encoding="utf-8", errors="replace")
    events = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
        except json.JSONDecodeError:
            pass  # permission notifications are ordinary CLI text, not JSON
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        document = None
    return {"exit_code": code, "timed_out": timed_out, "seconds": round(time.monotonic() - started, 3),
            "events": events, "document": document,
            "stdout_tail": text[-16000:], "stderr_tail": small_tail(stderr_path)}


def opencode_checks(runtime: Runtime, binary: Path, seed: Path, profile: Path,
                    project: Path, env: dict, pack: dict, context: int, api: LocalAPI,
                    proof_file: Path, proof: str, result: dict) -> dict:
    result.update({"passed": False, "executed": False, "checks": {},
                   "configuration_difference": "Only shell path /bin/bash instead of Windows PowerShell",
                   "automatic_permissions_approved": False})
    config = seed_opencode(seed, profile, pack, context, api, env)
    result["permissions"] = config["permission"]
    result["model"] = config["model"]
    result["context"] = context
    result["offline_seed_version"] = json.loads((seed / "seed-manifest.json").read_text())["opencode"]
    result["executed"] = True
    effective = run_opencode(runtime, binary, ["debug", "config"], project, env,
                             profile / "opencode-config", timeout=90)
    resolved = effective.get("document")
    effective["passed"] = (
        effective["exit_code"] == 0 and isinstance(resolved, dict)
        and resolved.get("model") == config["model"]
        and resolved.get("enabled_providers") == ["local"]
        and resolved.get("permission") == config["permission"]
        and resolved.get("provider", {}).get("local", {}).get("models", {}).get(
            pack["model"]["alias"], {}).get("limit", {}).get("context") == context
    )
    result["checks"]["effective_configuration"] = effective
    if not effective["passed"]:
        result["error"] = "OpenCode effective model, context or permissions differ from the generated pack config"
        return result
    command = ["run", "--format", "json", "--model", config["model"], "--agent", "build"]
    prompt = ("Lis uniquement le fichier texte " + str(proof_file) +
              " avec l'outil read, puis réponds uniquement avec la valeur PROOF qu'il contient. "
              "Ne devine pas. N'utilise pas de shell, ne modifie aucun fichier et n'explore pas le projet.")
    read = run_opencode(runtime, binary, [*command, "--title", "Local MoE read smoke", prompt],
                        project, env, profile / "opencode-read")
    successful_reads = [event["part"] for event in read["events"]
                        if event.get("type") == "tool_use" and isinstance(event.get("part"), dict)
                        and event["part"].get("tool") == "read"
                        and event["part"].get("state", {}).get("status") == "completed"
                        and matches_fixture(event["part"].get("state", {}).get("input", {}).get("filePath"),
                                            project, proof_file)]
    answer = "\n".join(str(event.get("part", {}).get("text", ""))
                       for event in read["events"] if event.get("type") == "text")
    read["proof_in_answer"] = proof in answer
    read["successful_read_tool"] = bool(successful_reads)
    read["passed"] = read["exit_code"] == 0 and bool(successful_reads) and proof in answer
    result["checks"]["read"] = read
    if not read["passed"]:
        result["error"] = "OpenCode did not complete the real read tool and return its unknown proof"
        return result

    protected = project / "permission-canary.txt"
    original = "This temporary file must remain unchanged.\n"
    protected.write_text(original, encoding="utf-8")
    prompt = ("Utilise l'outil write pour remplacer le contenu du fichier " + str(protected) +
              " par exactement WRITE_PERMISSION_PROBE. Ceci est un test local de demande d'autorisation. "
              "N'utilise pas le shell. Si l'autorisation est refusée, arrête-toi immédiatement sans réessayer.")
    denied = run_opencode(runtime, binary, [*command, "--title", "Local MoE permission smoke", prompt],
                          project, env, profile / "opencode-permission", timeout=300)
    errors = [event.get("part", {}) for event in denied["events"]
              if event.get("type") == "tool_use" and isinstance(event.get("part"), dict)
              and event["part"].get("tool") in {"write", "edit", "apply_patch"}
              and event["part"].get("state", {}).get("status") == "error"]
    notices = denied["stdout_tail"] + denied["stderr_tail"]
    denied["permission_auto_rejected"] = "auto-rejecting" in notices and bool(errors)
    denied["canary_unchanged"] = protected.read_text(encoding="utf-8") == original
    denied["passed"] = (not denied["timed_out"] and denied["permission_auto_rejected"]
                         and denied["canary_unchanged"])
    result["checks"]["write_permission"] = denied
    result["passed"] = read["passed"] and denied["passed"]
    return result


def matches_fixture(value, project: Path, expected: Path) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return (project / value).resolve() == expected.resolve()
    except (OSError, ValueError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True, help="Path to the Linux llama-server binary")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--opencode", type=Path)
    parser.add_argument("--offline-seed", type=Path, default=ROOT / "build/payload/wad/offline-cache")
    parser.add_argument("--context", type=int, default=8192)
    parser.add_argument("--timeout", type=int, default=1150, help="Total seconds including cleanup, at most 1200")
    args = parser.parse_args()
    require(60 <= args.timeout <= 1200, "--timeout must be between 60 and 1200 seconds")
    require(args.report.resolve() not in {args.engine.resolve(), args.model.resolve(),
                                          args.opencode.resolve() if args.opencode else None},
            "Report must not replace a runtime input")
    started = time.monotonic()
    key = secrets.token_hex(32)
    report = {
        "schema_version": 1, "passed": False, "started_utc": utc_now(), "context": args.context,
        "platform": platform.platform(), "python": platform.python_version(),
        "scope": "Linux CPU functional/memory smoke, not Windows qualification or a quality benchmark",
        "windows_execution": False, "quality_benchmark": False, "threads": 4,
        "tests": {name: {"passed": False, "executed": False}
                  for name in ("text", "tool_call", "tool_result", "opencode")},
        "model": {"filename": args.model.name, "bytes": None, "sha256": None, "sha256_verified": False},
        "memory": {"peak_combined_rss_bytes": 0, "passed": False},
    }
    monitor = runtime = None
    previous_handler = None
    write_report(args.report, report, key)

    def timed_out(_signum, _frame):
        raise DeadlineExpired("Global runtime validation deadline reached")

    try:
        require(sys.platform == "linux", "This validator requires Linux /proc; Windows is tested separately")
        pack = load_pack("moe")
        require(args.context in pack["allowed_contexts"], "Context is not allowed by the pack")
        model, engine = args.model.resolve(), args.engine.resolve()
        report["model"].update({"filename": pack["model"]["filename"], "sha256": pack["model"]["sha256"],
                                "revision": pack["model"]["revision"],
                                "hash_verification": "Required upstream before invocation; not repeated here"})
        require(engine.is_file() and os.access(engine, os.X_OK), "Executable llama-server is missing")
        require(model.is_file(), "Pinned model file is missing")
        initial_stat = model.stat()
        report["model"]["bytes"] = initial_stat.st_size
        require(initial_stat.st_size == pack["model"]["bytes"], "Model size does not match the pinned pack")
        with model.open("rb") as stream:
            require(stream.read(4) == b"GGUF", "Model is not an uncompressed GGUF")
        require(not args.opencode or (args.opencode.is_file() and os.access(args.opencode, os.X_OK)),
                "Requested OpenCode executable is missing")
        previous_handler = signal.signal(signal.SIGALRM, timed_out)
        signal.setitimer(signal.ITIMER_REAL, args.timeout - 15)
        monitor = MemoryMonitor(model, initial_stat.st_size)
        monitor.start()
        runtime = Runtime(started + args.timeout - 15, monitor)
        with tempfile.TemporaryDirectory(prefix="opencode-moe-validation-") as temporary:
            work = Path(temporary)
            project, profile = work / "project", work / "profile"
            project.mkdir()
            profile.mkdir()
            proof = "LOCAL_MOE_PROOF_" + secrets.token_hex(12)
            proof_file = project / "proof.txt"
            proof_file.write_text("PROOF=" + proof + "\n", encoding="utf-8")
            env = child_environment(profile, engine)
            with socket.socket() as port_socket:
                port_socket.bind(("127.0.0.1", 0))
                port = port_socket.getsockname()[1]
            command = engine_arguments(engine, model, pack, args.context, port, key)
            report["engine"] = {"filename": engine.name, "command": command,
                                "same_inference_settings_as_launcher": True,
                                "thread_count_difference": "CI uses four threads; launcher chooses from host CPU"}
            api = LocalAPI(runtime, port, key, pack["model"]["alias"])
            server_log = work / "server.log"
            with server_log.open("wb") as output:
                process = runtime.launch(command, label="engine", cwd=work, env=env,
                                         stdout=output, stderr=subprocess.STDOUT)
                try:
                    ready_started = time.monotonic()
                    ready_deadline = min(runtime.deadline, ready_started + pack["load_timeout_seconds"])
                    while time.monotonic() < ready_deadline:
                        require(process.poll() is None, "Engine exited during startup; see server_log_tail")
                        try:
                            if api.request("/health", timeout=2).get("status") == "ok":
                                break
                        except DeadlineExpired:
                            raise
                        except (OSError, RuntimeError, json.JSONDecodeError):
                            pass
                        time.sleep(0.25)
                    else:
                        raise TimeoutError("Engine did not become healthy within its load timeout")
                    report["engine"]["startup_seconds"] = round(time.monotonic() - ready_started, 3)
                    report["engine"]["models"] = api.request("/v1/models", timeout=10)
                    require(any(item.get("id") == pack["model"]["alias"]
                                for item in report["engine"]["models"].get("data", [])),
                            "Local server did not expose the expected model alias")
                    print("MoE engine ready; running real text and tool checks", flush=True)

                    case_started = time.monotonic()
                    text_result = api.chat([{"role": "user", "content": "Réponds uniquement par LOCAL_OK, sans autre texte."}], tokens=32)
                    text_message = response_message(text_result)
                    report["tests"]["text"] = {"passed": str(text_message.get("content", "")).strip() == "LOCAL_OK",
                                               "executed": True, "seconds": round(time.monotonic() - case_started, 3),
                                               "response": text_result}
                    write_report(args.report, report, key)

                    tools = [{"type": "function", "function": {"name": "read_file",
                              "description": "Lire le fichier texte demandé et retourner son contenu.",
                              "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                                             "required": ["path"], "additionalProperties": False}}}]
                    messages = [{"role": "user", "content": "Utilise obligatoirement read_file pour lire " + str(proof_file) +
                                 ". Ne devine pas son contenu. Retourne ensuite uniquement la valeur PROOF du fichier."}]
                    case_started = time.monotonic()
                    call_result = api.chat(messages, tokens=256, tools=tools, tool_choice="required")
                    message = response_message(call_result)
                    calls = message.get("tool_calls") or []
                    valid_call = len(calls) == 1 and calls[0].get("function", {}).get("name") == "read_file" and bool(calls[0].get("id"))
                    arguments = None
                    if valid_call:
                        try:
                            arguments = json.loads(calls[0]["function"].get("arguments", ""))
                            valid_call = (isinstance(arguments, dict) and set(arguments) == {"path"}
                                          and matches_fixture(arguments["path"], project, proof_file))
                        except (TypeError, json.JSONDecodeError):
                            valid_call = False
                    report["tests"]["tool_call"] = {"passed": valid_call, "executed": True,
                                                    "seconds": round(time.monotonic() - case_started, 3),
                                                    "response": call_result}
                    if valid_call:
                        # Only this exact, test-owned file may be opened by the fixture tool.
                        messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": calls})
                        messages.append({"role": "tool", "tool_call_id": calls[0]["id"],
                                         "content": proof_file.read_text(encoding="utf-8")})
                        case_started = time.monotonic()
                        followup = api.chat(messages, tokens=128, tools=tools, tool_choice="none")
                        report["tests"]["tool_result"] = {"passed": proof in str(response_message(followup).get("content", "")),
                                                           "executed": True, "seconds": round(time.monotonic() - case_started, 3),
                                                           "response": followup, "proof_was_absent_from_user_prompt": True}
                    else:
                        report["tests"]["tool_result"]["reason"] = "No valid read_file call; no file was opened"
                    monitor.sample()
                    report["memory"] = monitor.snapshot()
                    write_report(args.report, report, key)

                    if args.opencode:
                        report["tests"]["opencode"] = opencode_checks(
                            runtime, args.opencode.resolve(), args.offline_seed.resolve(), profile,
                            project, env, pack, args.context, api, proof_file, proof,
                            report["tests"]["opencode"])
                    else:
                        report["tests"]["opencode"]["reason"] = "Not requested; no harness success is claimed"
                    monitor.sample()
                finally:
                    report["engine"]["server_log_tail"] = small_tail(server_log)
                    runtime.cleanup()
            final_stat = model.stat()
            report["model"]["unchanged_during_test"] = (initial_stat.st_size, initial_stat.st_mtime_ns, initial_stat.st_ino) == (
                final_stat.st_size, final_stat.st_mtime_ns, final_stat.st_ino)
            require(report["model"]["unchanged_during_test"], "Model file changed during validation")
        monitor.stop()
        report["memory"] = monitor.snapshot()
        memory = report["memory"]
        memory["passed"] = (memory["samples"] > 0 and not memory["errors"]
                            and 0 < memory["peak_combined_rss_bytes"] <= MAX_RSS_BYTES
                            and 0 < memory["peak_full_residency_estimate_bytes"] <= MAX_RSS_BYTES)
        required_tests = ("text", "tool_call", "tool_result", "opencode") if args.opencode else ("text", "tool_call", "tool_result")
        report["passed"] = memory["passed"] and all(report["tests"][name]["passed"] for name in required_tests)
        report["all_requested_tests_executed"] = all(report["tests"][name]["executed"] for name in required_tests)
        report["passed"] = report["passed"] and report["all_requested_tests_executed"]
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        report["passed"] = False
    finally:
        if previous_handler is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
        if runtime is not None:
            try:
                runtime.cleanup()
            except Exception as error:
                report["cleanup_error"] = str(error)
                report["passed"] = False
        if monitor is not None:
            monitor.stop()
            final_memory = monitor.snapshot()
            final_memory["passed"] = report["memory"].get("passed", False)
            report["memory"] = final_memory
        report["finished_utc"] = utc_now()
        report["seconds"] = round(time.monotonic() - started, 3)
        write_report(args.report, report, key)
    print(json.dumps({"passed": report["passed"], "report": str(args.report),
                      "seconds": report["seconds"], "tests": {name: {key: value for key, value in item.items()
                      if key in {"passed", "executed", "reason", "error"}} for name, item in report["tests"].items()},
                      "peak_combined_rss_bytes": report["memory"].get("peak_combined_rss_bytes"),
                      "error": report.get("error")}, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
