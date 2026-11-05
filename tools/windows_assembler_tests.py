#!/usr/bin/env python3
"""Run small real assembler fixtures and metadata checks on the exact pack PEs.

The fixture compiler copies src/assemble_model.cpp unchanged into a temporary
directory and uses an explicitly separate miniature test header. Production
configurations are neither patched nor relaxed. The supplied production
assemblers are additionally exercised with their actual pinned model sizes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile

from pack_profiles import load_pack, pack_names
from windows_test_support import run_process, sparse_file

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/assemble_model.cpp"


def compile_fixture(work: Path, count: int, payload: bytes, compiler: str) -> dict:
    folder = work / f"fixture-{count}"
    folder.mkdir()
    sizes = ([11111, len(payload) - 11111] if count == 2 else
             [1, 2, 7, 23, 61, 127, 257, 509, 1021, 2047])
    if count != 2:
        sizes.append(len(payload) - sum(sizes))
    names = (["Setup-1.bin", "Setup-2.bin"] if count == 2 else
             [f"Setup-Fixture-{number:02d}.bin" for number in range(1, count + 1)])
    model = f"fixture-{count}.gguf"
    entries = ",\n".join(f'    {{"{name}", {size}ULL}}' for name, size in zip(names, sizes))
    header = f'''// TEST FIXTURE ONLY. Not a production model or production configuration.
#pragma once
#include <cstddef>
#include <cstdint>
namespace PackConfig {{
constexpr char ModelFile[] = "{model}";
constexpr char ModelAlias[] = "fixture-{count}";
constexpr char ModelSha256[] = "{hashlib.sha256(payload).hexdigest()}";
constexpr std::uint64_t ModelBytes = {len(payload)}ULL;
struct Part {{ const char* name; std::uint64_t bytes; }};
constexpr Part Parts[] = {{
{entries}
}};
constexpr std::size_t PartCount = sizeof(Parts) / sizeof(Parts[0]);
}}
'''
    (folder / "pack_config.h").write_text(header, encoding="utf-8")
    copied = folder / "assemble_model.cpp"
    shutil.copyfile(SOURCE, copied)
    if copied.read_bytes() != SOURCE.read_bytes():
        raise RuntimeError("Fixture source is not byte-identical to production source")
    exe = folder / ("AssemblerFixture.exe" if os.name == "nt" else "assembler-fixture")
    if Path(compiler).name.lower() in ("cl", "cl.exe"):
        command = [compiler, "/nologo", "/std:c++17", "/O2", "/W4", "/EHsc", "/MT", "/utf-8",
                   str(copied), "/Fe" + str(exe), "/Fo" + str(folder / "fixture.obj")]
    else:
        command = [compiler, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", str(copied), "-o", str(exe)]
        if os.name == "nt":
            command += ["-static", "-municode"]
    compiled = run_process(command, timeout=180, cwd=folder)
    if compiled["exit_code"] != 0:
        raise RuntimeError("Fixture compilation failed:\n" + compiled["stdout"] + compiled["stderr"])
    return {"exe": exe, "names": names, "sizes": sizes, "model": model,
            "compilation": compiled, "header_sha256": hashlib.sha256(header.encode()).hexdigest()}


def write_parts(source: Path, profile: dict, payload: bytes) -> None:
    offset = 0
    for name, size in zip(profile["names"], profile["sizes"]):
        (source / name).write_bytes(payload[offset:offset + size])
        offset += size
    assert offset == len(payload)


def exercise_small(work: Path, profile: dict, payload: bytes) -> list[dict]:
    folder = work / f'cases-{len(profile["names"])}'
    folder.mkdir()
    rows = []
    previous = b"An existing installation must survive a failed replacement."

    def fixture(name: str, *, parts=True, prior=True, unicode=False):
        case = folder / name
        source = case / ("source été 旅" if unicode else "source")
        source.mkdir(parents=True)
        destination = case / ("installed été 旅" if unicode else "installed") / profile["model"]
        destination.parent.mkdir()
        if prior:
            destination.write_bytes(previous)
        if parts:
            write_parts(source, profile, payload)
        return source, destination

    def check(name, source, destination, code, expected, *, protected=(), stable=None):
        result = run_process([str(profile["exe"]), str(source), str(destination)], timeout=30)
        problems = []
        if result["exit_code"] != code:
            problems.append(f'Exit {result["exit_code"]}, expected {code}')
        if expected is not None and (not destination.is_file() or destination.read_bytes() != expected):
            problems.append("Destination content was not preserved or published correctly")
        for path, contents in protected:
            if not path.is_file() or path.read_bytes() != contents:
                problems.append("A protected input changed: " + path.name)
        if stable and destination.is_file():
            stat = destination.stat()
            if (stat.st_ino, stat.st_mtime_ns) != stable:
                problems.append("A verified existing destination was rewritten")
        if list(destination.parent.glob(destination.name + ".partial.*")):
            problems.append("A temporary model file remained after completion")
        rows.append({"case": name, "parts": len(profile["names"]), "expected_exit": code,
                     "execution": result, "passed": not problems, "problems": problems})

    for name, prior, unicode in (("split_success", False, False),
                                 ("replace_existing_after_verification", True, False),
                                 ("spaces_and_unicode_paths", False, True)):
        source, destination = fixture(name, prior=prior, unicode=unicode)
        check(name, source, destination, 0, payload)

    for name, mode in (("missing_last_part", "missing"), ("truncated_last_part", "short"),
                       ("oversized_last_part", "long"), ("corrupt_part_preserves_previous", "corrupt")):
        source, destination = fixture(name)
        part = source / profile["names"][-1]
        if mode == "missing":
            part.unlink()
        else:
            data = part.read_bytes()
            if mode == "short": data = data[:-1]
            if mode == "long": data += b"x"
            if mode == "corrupt": data = data[:-1] + bytes([data[-1] ^ 1])
            part.write_bytes(data)
        check(name, source, destination, 7 if mode == "corrupt" else 3, previous)

    source, destination = fixture("full_gguf_only", parts=False)
    (source / profile["model"]).write_bytes(payload)
    check("full_gguf_only", source, destination, 0, payload)

    source, destination = fixture("full_gguf_takes_priority")
    (source / profile["model"]).write_bytes(payload)
    (source / profile["names"][-1]).unlink()
    check("full_gguf_takes_priority", source, destination, 0, payload)

    for name, data, code in (("corrupt_full_does_not_fall_back", payload[:-1] + b"?", 7),
                             ("truncated_full_does_not_fall_back", payload[:-1], 3),
                             ("archive_magic_is_rejected", b"PK\x03\x04" + payload[4:], 7)):
        source, destination = fixture(name)
        (source / profile["model"]).write_bytes(data)
        check(name, source, destination, code, previous)

    source, destination = fixture("quarantined_suffix_is_not_a_source", parts=False)
    (source / (profile["model"] + ".quarantine")).write_bytes(payload)
    check("quarantined_suffix_is_not_a_source", source, destination, 3, previous)

    source, unused = fixture("destination_is_part", prior=False)
    destination = source / profile["names"][-1]
    contents = destination.read_bytes()
    check("destination_is_part", source, destination, 4, contents, protected=[(destination, contents)])

    source, unused = fixture("destination_is_complete_source", parts=False, prior=False)
    destination = source / profile["model"]
    destination.write_bytes(payload)
    check("destination_is_complete_source", source, destination, 4, payload)

    source, destination = fixture("destination_hardlinks_source", parts=False, prior=False)
    complete = source / profile["model"]
    complete.write_bytes(payload)
    os.link(complete, destination)
    check("destination_hardlinks_source", source, destination, 4, payload, protected=[(complete, payload)])

    source, destination = fixture("verified_existing_reuse", parts=False)
    destination.write_bytes(payload)
    stat = destination.stat()
    check("verified_existing_reuse", source, destination, 0, payload,
          stable=(stat.st_ino, stat.st_mtime_ns))

    result = run_process([str(profile["exe"]), "--self-test"])
    rows.append({"case": "sha256_self_test", "parts": len(profile["names"]),
                 "execution": result, "passed": result["exit_code"] == 0 and "PASS SHA-256" in result["stdout"]})
    result = run_process([str(profile["exe"]), "--hash-file", str(destination)])
    rows.append({"case": "hash_file_matches_independent_sha256", "parts": len(profile["names"]),
                 "execution": result, "passed": result["exit_code"] == 0 and
                 result["stdout"].strip() == hashlib.sha256(payload).hexdigest()})
    return rows


def exercise_production(work: Path, exe: Path, pack: dict) -> dict:
    previous = b"Previously installed bytes must survive every invalid model fixture."
    folder = work / ("production-" + pack["id"])
    folder.mkdir()
    rows = []
    self_test = run_process([str(exe), "--self-test"])
    rows.append({"case": "production_sha256_self_test", "expected_exit": 0,
                 "execution": self_test, "passed": self_test["exit_code"] == 0 and
                 "PASS SHA-256" in self_test["stdout"]})
    whole = pack["model"]["bytes"]
    specs = [("full_truncated_64bit", whole - 1, [3]),
             ("full_oversized_64bit", whole + 1, [3]),
             ("full_exact_size_invalid_magic", whole, [6, 7])]
    if whole > 2**32:
        specs.append(("low_32_bits_cannot_match_model_size", whole % 2**32, [3]))
    for name, length, expected in specs:
        case = folder / name
        source = case / "source"
        source.mkdir(parents=True)
        destination = case / "installed/model.gguf"
        destination.parent.mkdir()
        destination.write_bytes(previous)
        model = source / pack["model"]["filename"]
        fixture = sparse_file(model, length, b"NOPE")
        result = run_process([str(exe), str(source), str(destination)], timeout=45)
        input_unchanged = model.stat().st_size == length
        with model.open("rb") as stream:
            input_unchanged = input_unchanged and stream.read(4) == b"NOPE"
        passed = (result["exit_code"] in expected and input_unchanged and
                  destination.read_bytes() == previous and
                  not list(destination.parent.glob(destination.name + ".partial.*")))
        row = {"case": name, "fixture": fixture, "expected_exit": expected,
               "execution": result, "passed": passed,
               "destination_preserved": destination.read_bytes() == previous,
               "production_model_bytes": whole}
        if name == "full_exact_size_invalid_magic":
            # Both branches are meaningful: the production reader accepted
            # the exact 64-bit length, then either the real disk-space guard
            # fired or the first four bytes were rejected. No weights are read.
            accepted = "Source : fichier GGUF complet" in result["stdout"]
            row.update(size_accepted=accepted, invalid_magic_checked=result["exit_code"] == 7,
                       disk_space_guard_hit=result["exit_code"] == 6,
                       passed=passed and accepted)
        rows.append(row)
        shutil.rmtree(case)
    return {"pack": pack["id"], "executable_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
            "model_bytes": whole, "cases": rows, "passed": all(row["passed"] for row in rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-root", type=Path)
    parser.add_argument("--pack", choices=pack_names(), action="append")
    parser.add_argument("--cxx", default="cl" if os.name == "nt" else "g++")
    parser.add_argument("--fixtures-only", action="store_true",
                        help="For local source testing; does not claim validation of production PEs")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    config_hash = hashlib.sha256((ROOT / "config/packs.json").read_bytes()).hexdigest()
    report = {"schema_version": 1, "platform": sys.platform,
              "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
              "production_config_sha256": config_hash, "model_loaded": False,
              "fixture_model_bytes": 262168, "small_fixtures": [], "production": [],
              "passed": False, "production_pe_execution": "not_run"}
    try:
        if not args.fixtures_only and not args.native_root:
            raise ValueError("--native-root is required unless --fixtures-only is explicit")
        payload = b"GGUF" + struct.pack("<IQQ", 3, 0, 0) + bytes(range(256)) * 1024
        report["fixture_model_bytes"] = len(payload)
        with tempfile.TemporaryDirectory(prefix="opencode-assembler-fixtures-") as temporary:
            work = Path(temporary)
            for count in (2, 11):
                profile = compile_fixture(work, count, payload, args.cxx)
                rows = exercise_small(work, profile, payload)
                report["small_fixtures"].append({"parts": count, "compilation": profile["compilation"],
                                                 "test_header_sha256": profile["header_sha256"],
                                                 "cases": rows, "passed": all(row["passed"] for row in rows)})
            if not args.fixtures_only:
                for name in args.pack or pack_names():
                    exe = (args.native_root / name / "AssembleModel.exe").resolve(strict=True)
                    report["production"].append(exercise_production(work, exe, load_pack(name)))
                report["production_pe_execution"] = "executed"
        report["production_config_unchanged"] = config_hash == hashlib.sha256(
            (ROOT / "config/packs.json").read_bytes()).hexdigest()
        report["passed"] = all(row["passed"] for row in report["small_fixtures"] + report["production"]) and report["production_config_unchanged"]
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "platform": sys.platform,
                      "small_cases": sum(len(row["cases"]) for row in report["small_fixtures"]),
                      "production_cases": sum(len(row["cases"]) for row in report["production"]),
                      "production_pe_execution": report["production_pe_execution"],
                      "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
