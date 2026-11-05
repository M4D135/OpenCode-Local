#!/usr/bin/env python3
"""Generate matching C++, installer and OpenCode settings from pinned RAM packs."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def pack_names() -> tuple[str, ...]:
    document = json.loads((ROOT / "config/packs.json").read_text(encoding="utf-8"))
    names = tuple(document["packs"])
    if document.get("schema_version") != 1 or not names or any(
            not re.fullmatch(r"[a-z0-9_-]+", name) for name in names):
        raise ValueError("Invalid pack configuration")
    return names


def load_pack(name: str) -> dict:
    document = json.loads((ROOT / "config/packs.json").read_text(encoding="utf-8"))
    if document.get("schema_version") != 1 or name not in document["packs"]:
        raise ValueError("Unknown pack or schema: " + name)
    pack = dict(document["packs"][name])
    pack["id"] = name
    pack["app_version"] = document["app_version"]
    model = pack["model"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.gguf", model["filename"]):
        raise ValueError("Invalid model filename")
    if not re.fullmatch(r"[0-9a-f]{64}", model["sha256"]):
        raise ValueError("Invalid pinned model hash")
    if pack["default_context"] not in pack["allowed_contexts"]:
        raise ValueError("Default context must be an allowed context")
    if not 0 < pack["output_tokens"] < pack["default_context"]:
        raise ValueError("Output budget must be smaller than default context")
    if any(not isinstance(value, int) or value <= 0 for value in pack["allowed_contexts"]):
        raise ValueError("Context lengths must be positive integers")
    for field in ("memory_reserve_mb", "runtime_overhead_mb", "cache_memory_per_4096_mb",
                  "batch_size", "micro_batch_size", "max_cpu_threads"):
        if not isinstance(pack[field], int) or pack[field] <= 0:
            raise ValueError("Invalid runtime budget: " + field)
    if pack["micro_batch_size"] > pack["batch_size"]:
        raise ValueError("Micro-batch cannot exceed batch size")
    if pack["cache_type_k"] not in ("bf16", "f16", "q8_0") or pack["cache_type_v"] not in ("bf16", "f16", "q8_0"):
        raise ValueError("Unsupported cache format")
    if not isinstance(pack["flash_attention"], bool):
        raise ValueError("Flash Attention must be explicit")
    part_size = int(document["slice_bytes"])
    if not 0 < part_size < 2**31:
        raise ValueError("Slices must fit signed 32-bit installer file offsets")
    parts = []
    remaining = int(model["bytes"])
    number = 1
    while remaining:
        count = min(remaining, part_size)
        suffix = str(number).zfill(pack["part_zero_pad"])
        parts.append({"name": f'{pack["part_prefix"]}{suffix}.bin', "bytes": count})
        remaining -= count
        number += 1
    pack["parts"] = parts
    return pack


def nsis_escape(value: str) -> str:
    return value.replace("$", "$$").replace('"', '$\\"')


def cpp_string(value: str, wide: bool = False) -> str:
    return ("L" if wide else "") + json.dumps(value, ensure_ascii=False)


def generate(pack: dict, output: Path, non_model_bytes: int = 450_000_000) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    model = pack["model"]
    fields = {
        "DisplayName": pack["display_name"],
        "ProfileName": pack["profile_name"],
        "ModelFileW": model["filename"],
        "ModelLabel": model["label"],
        "ModelAliasW": model["alias"],
        "VersionW": pack["app_version"],
        "CacheTypeK": pack["cache_type_k"],
        "CacheTypeV": pack["cache_type_v"],
    }
    header = ["// Generated from config/packs.json; do not edit.", "#pragma once",
              "#include <cstdint>", "#include <cstddef>", "namespace PackConfig {"]
    for key, value in fields.items():
        header.append(f"inline constexpr wchar_t {key}[] = {cpp_string(value, True)};")
    for key, value in {"ModelFile": model["filename"], "ModelAlias": model["alias"],
                       "ModelSha256": model["sha256"]}.items():
        header.append(f"inline constexpr char {key}[] = {cpp_string(value)};")
    header.append(f'inline constexpr uint64_t ModelBytes = {model["bytes"]}ULL;')
    for key, value in {"DefaultContext": pack["default_context"], "OutputTokens": pack["output_tokens"],
                       "MinInstalledMemoryMB": pack["min_installed_memory_mb"],
                       "MinFreeMemoryMB": pack["min_free_memory_mb"],
                       "MemoryReserveMB": pack["memory_reserve_mb"],
                       "RuntimeOverheadMB": pack["runtime_overhead_mb"],
                       "CacheMemoryPer4096MB": pack["cache_memory_per_4096_mb"],
                       "BatchSize": pack["batch_size"], "MicroBatchSize": pack["micro_batch_size"],
                       "MaxCpuThreads": pack["max_cpu_threads"],
                       "LoadTimeoutSeconds": pack["load_timeout_seconds"]}.items():
        header.append(f"inline constexpr int {key} = {value};")
    header.append("inline constexpr bool FlashAttention = " + str(pack["flash_attention"]).lower() + ";")
    header.append("inline constexpr int AllowedContexts[] = {" + ", ".join(map(str, pack["allowed_contexts"])) + "};")
    header += ["struct Part { const char* name; uint64_t bytes; };", "inline constexpr Part Parts[] = {"]
    for part in pack["parts"]:
        header.append(f'    {{{cpp_string(part["name"])}, {part["bytes"]}ULL}},')
    header += ["};", "inline constexpr size_t PartCount = sizeof(Parts) / sizeof(Parts[0]);", "}", ""]
    (output / "pack_config.h").write_text("\n".join(header), encoding="utf-8")

    needed_gib = math.ceil((model["bytes"] + non_model_bytes + 2**30) / 2**30)
    values = {
        "PACK_ID": pack["id"], "PACK_DISPLAY_NAME": pack["display_name"],
        "PACK_VERSION": pack["app_version"], "PACK_PROFILE_NAME": pack["profile_name"],
        "PACK_INSTALL_DIR": pack["install_dir_name"], "PACK_SHORTCUT_NAME": pack["shortcut_name"],
        "PACK_UNINSTALL_KEY": pack["uninstall_key"], "PACK_MARKER": pack["installation_marker"],
        "PACK_MODEL_FILENAME": model["filename"], "PACK_MODEL_LABEL": model["label"],
        "PACK_MODEL_BYTES": str(model["bytes"]),
        "PACK_MODEL_SIZE_TEXT": f'{model["bytes"] / 1e9:.2f}'.replace(".", ",") + " Go",
        "PACK_MODEL_EXTRA_KIB": str(math.ceil(model["bytes"] / 1024)),
        "PACK_DISK_REQUIRED_BYTES": str(needed_gib * 2**30),
        "PACK_DISK_REQUIRED_GIB": str(needed_gib), "PACK_RAM_GIB": str(pack["ram_gib"]),
        "PACK_PART_COUNT": str(len(pack["parts"])),
    }
    lines = ["; Generated from config/packs.json; do not edit."]
    lines += [f'!define {key} "{nsis_escape(value)}"' for key, value in values.items()]
    lines += ["", "; Called once inside ValidateDataParts. A full GGUF is checked first.", "!macro ValidatePackParts"]
    for part in pack["parts"]:
        lines += ["  ClearErrors", f'  FileOpen $0 "$EXEDIR\\{part["name"]}" r',
                  "  IfErrors parts_missing", "  FileSeek $0 0 END $1", "  FileClose $0",
                  f'  StrCmp $1 "{part["bytes"]}" 0 parts_bad']
    lines += ["  Goto parts_done", "!macroend", ""]
    (output / "pack-profile.nsh").write_text("\n".join(lines), encoding="utf-8")
    (output / "installation.id").write_text(pack["installation_marker"] + "\n", encoding="utf-8")
    (output / "pack.json").write_text(json.dumps(pack, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"pack": pack["id"], "parts": len(pack["parts"]), "model_bytes": model["bytes"],
            "required_free_gib": needed_gib, "output": str(output)}


def configure_opencode(template: str, pack: dict) -> str:
    # Placeholders are deliberately retained for the launcher's session values.
    text = template.replace("{{CONTEXT}}", '"__CONTEXT__"').replace("{{OUTPUT}}", '"__OUTPUT__"')
    data = json.loads(text)
    alias = pack["model"]["alias"]
    data["model"] = data["small_model"] = "local/" + alias
    models = data["provider"]["local"]["models"]
    if len(models) != 1:
        raise ValueError("Expected one local model template")
    settings = next(iter(models.values()))
    settings["name"] = pack["model"]["label"] + " — MoE local"
    data["provider"]["local"]["models"] = {alias: settings}
    data["compaction"]["reserved"] = "__COMPACTION_RESERVED__"
    data["compaction"]["preserve_recent_tokens"] = "__COMPACTION_PRESERVE__"
    result = json.dumps(data, indent=2, ensure_ascii=False)
    for key in ("CONTEXT", "OUTPUT", "COMPACTION_RESERVED", "COMPACTION_PRESERVE"):
        result = result.replace('"__' + key + '__"', "{{" + key + "}}")
    return result + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", choices=pack_names(), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(generate(load_pack(args.pack), args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
