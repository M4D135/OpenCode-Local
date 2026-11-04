#!/usr/bin/env python3
"""Exercise the installer's actual NSIS path functions without installing a payload.

The generated amd64 NSIS harness can be run on Windows directly, or with Wine.
Only temporary fixtures bearing a per-process name are created and removed.
Use --build-only where Windows execution is unavailable. Compilation alone is
reported separately from runtime validation; it is never considered a pass.
"""
import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build/test-installer-paths"
CASES = {
    "missing_nested": True,
    "default_location": True,
    "documents_nested": True,
    "existing_empty": True,
    "existing_empty_trailing_separator": True,
    "drive_root": False,
    "documents_root": False,
    "unowned_nonempty": False,
    "personal_data": False,
}


def nsis_function(source, name):
    match = re.search(r"^Function " + re.escape(name) + r"\n.*?^FunctionEnd\s*$",
                      source, re.MULTILINE | re.DOTALL)
    if not match:
        raise ValueError("Installer function not found: " + name)
    return match.group(0).replace("${PREFIX}", "")


def make_harness(source, label):
    marker = re.search(r'^!define PRODUCT_MARKER (.+)$', source, re.MULTILINE).group(1)
    marker_path = re.search(r'^!define MARKER_PATH (.+)$', source, re.MULTILINE).group(1)
    functions = "\n\n".join(nsis_function(source, name) for name in (
        "${PREFIX}CheckAncestors", "${PREFIX}CheckTree", "${PREFIX}ReadOwner", "ValidateTarget"))
    header = f'''Target amd64-unicode
Unicode true
RequestExecutionLevel user
SilentInstall silent
AutoCloseWindow true
Name "OpenCode Local path regression"
OutFile "{(BUILD / (label + '.exe')).as_posix()}"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!define PRODUCT_MARKER {marker}
!define MARKER_PATH {marker_path}
Var Problem
Var Owned
Var HasContent
Var FixtureDir
Var ReportPath
Var Failures

!macro TestPath NAME PATH EXPECTED
  StrCpy $R9 "${{PATH}}"
  StrCpy $INSTDIR $R9
  Call ValidateTarget
  StrCpy $R8 0
  StrCmp $Problem "" 0 +2
  StrCpy $R8 1
  WriteINIStr "$ReportPath" "${{NAME}}" "input" "$R9"
  WriteINIStr "$ReportPath" "${{NAME}}" "normalized" "$INSTDIR"
  WriteINIStr "$ReportPath" "${{NAME}}" "allowed" "$R8"
  WriteINIStr "$ReportPath" "${{NAME}}" "expected" "${{EXPECTED}}"
  WriteINIStr "$ReportPath" "${{NAME}}" "problem" "$Problem"
  StrCmp $R8 "${{EXPECTED}}" +2
  IntOp $Failures $Failures + 1
!macroend

'''
    section = r'''
Section
  SetShellVarContext current
  SetRegView 64
  StrCpy $Failures 0
  System::Call 'kernel32::GetCurrentProcessId() i .r7'
  StrCpy $FixtureDir "$TEMP\OpenCodeLocal-PathRegression-$7"
  StrCpy $ReportPath "$EXEDIR\REPORT_NAME.ini"
  CreateDirectory "$FixtureDir\empty"
  CreateDirectory "$FixtureDir\nonempty"
  FileOpen $6 "$FixtureDir\nonempty\keep.txt" w
  FileWrite $6 "Regression fixture."
  FileClose $6

  !insertmacro TestPath "missing_nested" "$FixtureDir\new\nested\OpenCodeLocal" 1
  !insertmacro TestPath "default_location" "$LOCALAPPDATA\Programs\OpenCodeLocal-PathRegression-$7" 1
  !insertmacro TestPath "documents_nested" "$DOCUMENTS\OpenCodeLocal-PathRegression-$7\IA\OpenCodeLocal" 1
  !insertmacro TestPath "existing_empty" "$FixtureDir\empty" 1
  !insertmacro TestPath "existing_empty_trailing_separator" "$FixtureDir\empty\" 1
  ${GetRoot} "$WINDIR" $R7
  !insertmacro TestPath "drive_root" "$R7\" 0
  !insertmacro TestPath "documents_root" "$DOCUMENTS" 0
  !insertmacro TestPath "unowned_nonempty" "$FixtureDir\nonempty" 0
  !insertmacro TestPath "personal_data" "$LOCALAPPDATA\OpenCodeLocal\nested" 0

  Delete "$FixtureDir\nonempty\keep.txt"
  RMDir "$FixtureDir\nonempty"
  RMDir "$FixtureDir\empty"
  RMDir "$FixtureDir"
  WriteINIStr "$ReportPath" "summary" "failures" "$Failures"
  SetErrorLevel $Failures
SectionEnd
'''.replace("REPORT_NAME", label)
    return header + functions + "\n" + section


def read_results(path):
    raw = path.read_bytes()
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "cp1252"
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(raw.decode(encoding))
    results = []
    for name, expected in CASES.items():
        if not config.has_section(name):
            raise ValueError("Missing runtime case: " + name)
        actual = config.getboolean(name, "allowed")
        results.append({"case": name, "expected_allowed": expected,
                        "actual_allowed": actual, "passed": actual == expected,
                        "input": config.get(name, "input"),
                        "normalized": config.get(name, "normalized"),
                        "problem": config.get(name, "problem")})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "installer/OpenCodeLocal.nsi")
    parser.add_argument("--baseline", type=Path, help="Optional original installer source for comparison")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--runtime-blocker", help="Record an observed reason runtime could not run")
    parser.add_argument("--report", type=Path, default=ROOT / "validation/installer-paths.json")
    parser.add_argument("--wine", default="/usr/lib/wine/wine64")
    args = parser.parse_args()
    BUILD.mkdir(parents=True, exist_ok=True)
    sources = {"current": args.source}
    if args.baseline:
        sources = {"baseline": args.baseline, **sources}
    report = {"scope": "Actual installer path functions extracted into native NSIS harnesses",
              "runtime": "not_run", "runtime_blocker": args.runtime_blocker,
              "cases": CASES, "artifacts": {}}
    for label, path in sources.items():
        source = path.read_text(encoding="utf-8")
        nsi = BUILD / (label + ".nsi")
        nsi.write_text(make_harness(source, label), encoding="utf-8")
        compiled = subprocess.run(["makensis", "-V2", str(nsi)], capture_output=True, text=True)
        artifact = {"source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                    "compile_passed": compiled.returncode == 0,
                    "compiler_output": compiled.stdout + compiled.stderr,
                    "harness": nsi.relative_to(ROOT).as_posix(),
                    "executable": nsi.with_suffix(".exe").relative_to(ROOT).as_posix()}
        report["artifacts"][label] = artifact
        if compiled.returncode:
            report["runtime_blocker"] = "NSIS harness compilation failed"
            break
        if args.build_only:
            continue
        exe = nsi.with_suffix(".exe")
        env = os.environ.copy()
        if os.name == "nt":
            command = [str(exe), "/S"]
        else:
            env.update(WINEPREFIX=str(ROOT / "build/wine-installer-test"),
                       WINEARCH="win64", WINEDEBUG="-all")
            command = [args.wine, str(exe), "/S"]
            if shutil.which("xvfb-run"):
                command = ["xvfb-run", "-a", *command]
        try:
            ini = nsi.with_suffix(".ini")
            if ini.exists():
                ini.unlink()  # Never mistake a previous run for fresh execution.
            executed = subprocess.run(command, capture_output=True, text=True, env=env, timeout=45)
            artifact["runtime_output"] = executed.stdout + executed.stderr
            artifact["runtime_returncode"] = executed.returncode
            if not ini.exists():
                report["runtime"] = "blocked"
                report["runtime_blocker"] = artifact["runtime_output"].strip() or "No runtime results produced"
                break
            artifact["results"] = read_results(ini)
            artifact["runtime_passed"] = all(row["passed"] for row in artifact["results"])
            report["runtime"] = "executed"
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            report["runtime"] = "blocked"
            report["runtime_blocker"] = str(exc)
            break
    if args.runtime_blocker and args.build_only:
        report["runtime"] = "blocked"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    current = report["artifacts"].get("current", {})
    if args.build_only:
        return 0 if all(a["compile_passed"] for a in report["artifacts"].values()) else 1
    return 0 if current.get("runtime_passed") else 1


if __name__ == "__main__":
    sys.exit(main())
