#!/usr/bin/env python3
"""Compile and execute small Windows fixtures against the real launcher source.

The production pack header is generated unchanged. Fixtures exercise Windows
processes, local HTTP, and temporary profile files; they never download or load
a model, alter endpoint protection, or claim antivirus acceptance. --build-only
is an explicit cross-compilation check, never a successful Windows runtime test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from pack_profiles import generate, load_pack, pack_names
from windows_test_support import run_process

ROOT = Path(__file__).resolve().parents[1]
LIBRARIES = ("user32", "gdi32", "comctl32", "comdlg32", "shell32", "ole32",
             "uuid", "winhttp", "ws2_32", "bcrypt", "iphlpapi")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_compiler(requested: str | None) -> str:
    if requested:
        found = shutil.which(requested)
        if found:
            return found
        path = Path(requested)
        if path.is_file():
            return str(path.resolve())
        raise RuntimeError("The requested C++ compiler is unavailable: " + requested)
    candidates = (("g++", "cl.exe") if os.name == "nt" else
                  ("x86_64-w64-mingw32-g++",))
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    raise RuntimeError("A Windows x64 C++ compiler is required; pass --cxx after initializing its environment")


def compile_fixture(source: Path, folder: Path, header: Path, compiler: str) -> tuple[Path, dict]:
    executable = folder / (source.stem + ".exe")
    if Path(compiler).name.lower() in ("cl", "cl.exe"):
        command = [compiler, "/nologo", "/std:c++17", "/O2", "/W4", "/EHsc", "/MT", "/utf-8",
                   "/I" + str(header), str(source), "/Fe" + str(executable),
                   "/Fo" + str(folder / "fixture.obj"), "/link", "/SUBSYSTEM:CONSOLE"]
        command += [name + ".lib" for name in LIBRARIES]
    else:
        command = [compiler, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                   "-static", "-static-libgcc", "-static-libstdc++", "-mconsole",
                   "-I", str(header), str(source), "-o", str(executable)]
        command += ["-l" + name for name in LIBRARIES]
    return executable, run_process(command, timeout=180, cwd=folder)


def read_results(execution: dict) -> tuple[dict | None, list[str]]:
    problems = []
    if execution["timed_out"]:
        problems.append("The Windows fixture exceeded its deadline")
    if execution["exit_code"] != 0:
        problems.append("The Windows fixture did not exit successfully")
    try:
        result = json.loads(execution["stdout"])
    except (json.JSONDecodeError, TypeError):
        return None, problems + ["The Windows fixture did not return a JSON report"]
    checks = result.get("checks") if isinstance(result, dict) else None
    if not isinstance(checks, list) or not checks:
        return result, problems + ["The Windows fixture returned no checks"]
    if result.get("passed") is not True or any(
            not isinstance(check, dict) or not isinstance(check.get("name"), str)
            or not check["name"] or check.get("passed") is not True for check in checks):
        problems.append("One or more Windows regression checks failed")
    return result, problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", help="Windows x64 g++ or initialized cl.exe")
    parser.add_argument("--pack", choices=pack_names(), default="moe")
    parser.add_argument("--report", type=Path, default=ROOT / "validation/launcher-runtime.json")
    parser.add_argument("--build-only", action="store_true", help="Compile only; no Windows execution is claimed")
    args = parser.parse_args()
    report = {
        "schema_version": 1,
        "platform": sys.platform,
        "build_only": args.build_only,
        "runtime_executed": False,
        "model_downloaded": False,
        "model_loaded": False,
        "endpoint_protection_changed": False,
        "antivirus_qualification": False,
        "pack": args.pack,
        "launcher_source_sha256": digest(ROOT / "src/launcher.cpp"),
        "pack_config_source_sha256": digest(ROOT / "config/packs.json"),
        "checks": [],
        "passed": False,
    }
    try:
        pack = load_pack(args.pack)
        report["app_version"] = pack["app_version"]
        if os.name != "nt" and not args.build_only:
            raise RuntimeError("These fixtures must execute on Windows; use --build-only for cross-compilation")
        compiler = find_compiler(args.cxx)
        report["compiler"] = compiler
        sources = sorted((ROOT / "tests").glob("launcher_*_tests.cpp"))
        if not sources:
            raise RuntimeError("No launcher regression fixtures were found")
        with tempfile.TemporaryDirectory(prefix="opencode-launcher-runtime-") as temporary:
            work = Path(temporary)
            generated = work / "generated"
            generate(pack, generated)
            report["production_header_sha256"] = digest(generated / "pack_config.h")
            for source in sources:
                folder = work / source.stem
                folder.mkdir()
                executable, compilation = compile_fixture(source, folder, generated, compiler)
                row = {
                    "name": source.stem,
                    "source_sha256": digest(source),
                    "compilation": compilation,
                    "runtime_executed": False,
                    "passed": False,
                    "problems": [],
                }
                report["checks"].append(row)
                if compilation["exit_code"] != 0 or compilation["timed_out"] or not executable.is_file():
                    row["problems"].append("The fixture did not compile successfully")
                    continue
                row["executable_sha256"] = digest(executable)
                if args.build_only:
                    row["passed"] = True
                    continue
                environment = dict(os.environ)
                environment.update({"TEMP": str(folder), "TMP": str(folder)})
                execution = run_process([str(executable)], timeout=180, cwd=folder, env=environment)
                row["execution"] = execution
                row["runtime_executed"] = execution["exit_code"] is not None or execution["timed_out"]
                report["runtime_executed"] = report["runtime_executed"] or row["runtime_executed"]
                row["results"], row["problems"] = read_results(execution)
                row["passed"] = not row["problems"]
        report["passed"] = bool(report["checks"]) and all(row["passed"] for row in report["checks"])
    except Exception as error:
        report["error"] = str(error)
        report["passed"] = False
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    phase = "compilation only" if args.build_only else "Windows runtime"
    print(f"Launcher fixtures ({phase}): {'PASS' if report['passed'] else 'FAIL'}; {args.report}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
