#!/usr/bin/env python3
"""Build separate, pinned 16/32 GiB offline installers from a prepared payload.

All large build outputs are completed before atomic publication. The Windows
installer does not download models or bypass endpoint security.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

from pack_profiles import ROOT, configure_opencode, generate, load_pack
from signing import HOUSE_FILES, check_source, image_record, source_identity, validate_files


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def run(command: list, log: Path | None = None, cwd: Path = ROOT) -> None:
    print("RUN", " ".join(map(str, command[:5])), flush=True)
    if log:
        with log.open("w", encoding="utf-8") as output:
            result = subprocess.run(list(map(str, command)), cwd=cwd, stdout=output, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(log.read_text(encoding="utf-8", errors="replace")[-10000:])
    else:
        subprocess.run(list(map(str, command)), cwd=cwd, check=True)


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix="." + destination.name + ".", suffix=".complete", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as incoming:
            shutil.copyfileobj(incoming, output, 4 * 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if temporary.stat().st_size != source.stat().st_size:
            raise RuntimeError("Incomplete output copy")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def verify_base(base: Path) -> dict:
    manifest_path = base / "wad/support/payload-manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Prepared base is missing payload-manifest.json")
    count = 0
    for entry in json.loads(manifest_path.read_text(encoding="utf-8")):
        if entry["path"].startswith("wad/model/"):
            continue
        target = base / entry["path"]
        if not target.is_file() or target.stat().st_size != entry["size"] or digest(target) != entry["sha256"]:
            raise ValueError("Prepared base differs from its manifest: " + entry["path"])
        count += 1
    return {"base_payload_files_verified": count}


def make_readme(pack: dict, signing: str = "unsigned") -> str:
    model = pack["model"]
    parts = "\n".join("  " + p["name"] for p in pack["parts"])
    reuse = "Les fichiers Setup-1.bin et Setup-2.bin de la version 1.0.0 restent compatibles.\n" if pack["id"] == "16go" else ""
    speed = "Le modèle 27B demande plus de temps de chargement et de calcul sur CPU.\n" if pack["id"] == "32go" else ""
    signature = ("Les exécutables maison intégrés ont une signature Authenticode vérifiée.\n"
                 "La signature ne garantit pas l'acceptation par l'antivirus de votre poste.\n"
                 if signing == "signed" else
                 "VERSION NON SIGNÉE : les exécutables maison n'ont pas de signature Authenticode.\n")
    return f'''{pack["display_name"]} — version {pack["app_version"]}

INSTALLATION HORS LIGNE
1. Extraire Setup.exe de Setup-{pack["ram_gib"]}Go-{pack["app_version"]}.zip.
2. Placer dans le même dossier SOIT le modèle complet :
   {model["filename"]}
   SOIT toutes ses {len(pack["parts"])} parties :
{parts}
3. Lancer Setup.exe et choisir un dossier dédié au programme.
4. Ouvrir le raccourci « {pack["shortcut_name"]} ».

{reuse}Aucun téléchargement n'est effectué par l'installateur ou le lanceur.
Le setup vérifie la taille et le SHA-256 complet du modèle avant publication.
Un modèle complet présent mais invalide provoque une erreur explicite.

MODÈLE
{model["label"]}
Taille : {model["bytes"]} octets
SHA-256 : {model["sha256"]}
Source figée : {model["url"]}
Contexte initial : {pack["default_context"]} tokens. Une session de calcul à la fois.
{speed}
DOSSIERS
Programme : %LOCALAPPDATA%\\Programs\\{pack["install_dir_name"]}
Données : %LOCALAPPDATA%\\{pack["profile_name"]}
Le pack 32 Go possède son propre profil ; les conversations du pack 16 Go
ne sont pas supprimées lors de son installation.

PROTECTION DU POSTE
{signature}Une détection TEHTRIS a été observée sur le lanceur 1.0.0/1.0.1 ;
sa cause n'est pas encore qualifiée.
Consulter wad\\docs\\TEHTRIS.md et les empreintes du pack pour l'analyse.
Ces packs ne modifient pas l'antivirus, le pare-feu ou leurs exclusions.

OpenCode nécessite une connexion locale à 127.0.0.1. Le mode Discussion
directe est disponible lorsque les connexions localhost sont interdites.
Les droits d'écriture et d'exécution restent ceux de la session Windows.
'''


def prepare_payload(base: Path, work: Path, pack: dict) -> Path:
    payload = work / "payload"
    if payload.exists():
        # Only this selected generated directory is replaced, never an installed app.
        shutil.rmtree(payload)
    def ignore(directory, names):
        relative = Path(directory).relative_to(base)
        if relative == Path("wad/model"):
            return names
        if relative == Path("wad/support"):
            return [name for name in names if name in {"Uninstall.exe", "payload-manifest.json"}]
        return []
    shutil.copytree(base, payload, ignore=ignore)
    (payload / "wad/model").mkdir(exist_ok=True)
    generated = work / "generated"
    generate(pack, generated)
    shutil.copyfile(generated / "installation.id", payload / "wad/support/installation.id")
    shutil.copyfile(generated / "pack.json", payload / "wad/support/pack.json")
    template = (base / "wad/config/opencode.json").read_text(encoding="utf-8")
    (payload / "wad/config/opencode.json").write_text(configure_opencode(template, pack), encoding="utf-8")
    for filename in ("TEHTRIS.md", "MODELES.md"):
        source = ROOT / "docs" / filename
        if source.is_file():
            shutil.copyfile(source, payload / "wad/docs" / filename)
    (payload / "wad/docs/LISEZ-MOI.txt").write_text(make_readme(pack), encoding="utf-8")
    return payload


def compile_native(work: Path, payload: Path, pack: dict) -> None:
    cxx = os.environ.get("OPENCODE_LOCAL_CXX", "x86_64-w64-mingw32-g++")
    windres = os.environ.get("OPENCODE_LOCAL_WINDRES", "x86_64-w64-mingw32-windres")
    # windres definitions need literal escaped quotes, not shell interpolation.
    display = pack["display_name"].replace("—", "-")
    version = pack["app_version"]
    manifest_template = (ROOT / "src/launcher.manifest").read_text(encoding="utf-8")
    for component in ("launcher", "assembler"):
        manifest, count = re.subn(r'(<assemblyIdentity\b[^>]*\bname="M4D135\.OpenCodeLocal"[^>]*\bversion=")[^"]+(" )',
                                  lambda match: match[1] + version + ".0" + match[2], manifest_template, count=1)
        if count != 1:
            raise ValueError("The asInvoker manifest template is missing its application identity")
        if component == "assembler":
            manifest = manifest.replace('name="M4D135.OpenCodeLocal"', 'name="M4D135.OpenCodeLocal.ModelAssembler"')
        manifest_path = work / "generated" / f"{component}.manifest"
        manifest_path.write_text(manifest, encoding="utf-8")
        run([windres, "--codepage=65001", "-I", ROOT / "src",
             '-DPACK_DISPLAY_NAME=\\"' + display + '\\"',
             '-DPACK_VERSION_STRING=\\"' + version + '\\"',
             '-DPACK_MANIFEST_PATH=\\"' + manifest_path.as_posix() + '\\"',
             "-DPACK_VERSION_NUMBER=" + version.replace(".", ",") + ",0",
             "-i", ROOT / f"src/{component}.rc", "-o", work / f"{component}-res.o", "-O", "coff"],
            work / f"{component}-resources.log")
    common = [cxx, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-static",
              "-static-libgcc", "-static-libstdc++", "-Wl,--no-insert-timestamp", "-I", work / "generated"]
    run(common + ["-municode", "-mwindows", ROOT / "src/launcher.cpp", work / "launcher-res.o",
                  "-o", payload / "OpenCodeLocal.exe", "-luser32", "-lgdi32", "-lcomctl32",
                  "-lcomdlg32", "-lshell32", "-lole32", "-luuid", "-lwinhttp", "-lws2_32",
                  "-lbcrypt", "-liphlpapi"], work / "launcher-build.log")
    run(common + ["-municode", ROOT / "src/assemble_model.cpp", work / "assembler-res.o", "-o", work / "AssembleModel.exe"],
        work / "assembler-build.log")


def write_manifest(payload: Path, pack: dict) -> list:
    files = []
    for path in sorted(payload.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlink in payload: " + str(path))
        if path.is_file() and path.name != "payload-manifest.json":
            files.append({"path": path.relative_to(payload).as_posix(), "size": path.stat().st_size, "sha256": digest(path)})
    model = pack["model"]
    files.append({"path": "wad/model/" + model["filename"], "size": model["bytes"], "sha256": model["sha256"]})
    (payload / "wad/support/payload-manifest.json").write_text(json.dumps(files, indent=2) + "\n", encoding="utf-8")
    return files


def split_model(model: Path, pack: dict, output: Path) -> list:
    if model.stat().st_size != pack["model"]["bytes"] or digest(model) != pack["model"]["sha256"]:
        raise ValueError("Model is not the pinned GGUF: " + str(model))
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with model.open("rb") as source:
        for part in pack["parts"]:
            destination = output / part["name"]
            fd, name = tempfile.mkstemp(prefix="." + destination.name + ".", suffix=".partial", dir=output)
            temporary = Path(name)
            sha = hashlib.sha256()
            remaining = part["bytes"]
            try:
                with os.fdopen(fd, "wb") as target:
                    while remaining:
                        block = source.read(min(4 * 1024 * 1024, remaining))
                        if not block:
                            raise ValueError("Model truncated while splitting")
                        target.write(block)
                        sha.update(block)
                        remaining -= len(block)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            results.append({"name": destination.name, "bytes": destination.stat().st_size, "sha256": sha.hexdigest()})
            print("MODEL PART", destination.name, destination.stat().st_size, flush=True)
        if source.read(1):
            raise ValueError("Unexpected bytes after last model part")
    return results


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_manifest(path: Path, phase: str) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or value.get("phase") != phase:
        raise ValueError("Invalid signing manifest: " + str(path))
    return value


def build_include(work: Path, payload: Path, pack: dict) -> dict:
    files = write_manifest(payload, pack)
    non_model = sum(e["size"] for e in files if not e["path"].startswith("wad/model/"))
    profile = generate(pack, work / "generated", non_model)
    run([sys.executable, ROOT / "installer/generate_payload_include.py", "--payload", payload,
         "--output", work / "payload-files.nsh", "--model-filename", pack["model"]["filename"]], work / "include.log")
    return profile


def build_nsis(work: Path, payload: Path, output: Path, *, export_uninstaller: Path | None = None,
               import_uninstaller: Path | None = None) -> None:
    command = [os.environ.get("OPENCODE_LOCAL_MAKENSIS", "makensis"), "-V3", f"-DPayloadDir={payload}",
               f"-DHelperPath={work / 'AssembleModel.exe'}",
               f"-DPackConfigPath={work / 'generated/pack-profile.nsh'}", f"-DOutputPath={output}"]
    if export_uninstaller:
        # !uninstfinalize uses system(). Keep its three quoted paths free of shell
        # expansion characters; no user-provided command or signing key is used.
        for path in (Path(sys.executable), ROOT / "tools/signing.py", export_uninstaller):
            if any(char in str(path) for char in '\"\'`$%\n\r;&|<>'):
                raise ValueError("Unsupported shell character in NSIS export path: " + str(path))
        command += [f"-DPythonExe={sys.executable}", f"-DSigningHelperPath={ROOT / 'tools/signing.py'}",
                    f"-DExportUninstallerPath={export_uninstaller}"]
    if import_uninstaller:
        command += [f"-DImportedUninstallerPath={import_uninstaller}"]
    command.append(ROOT / "installer/OpenCodeLocal.nsi")
    run(command, work / ("uninstaller-build.log" if export_uninstaller else "installer-build.log"))


def native_phase(names: tuple[str, ...], base: Path, work_root: Path, artifacts: Path) -> dict:
    print(json.dumps(verify_base(base)), flush=True)
    source = source_identity()
    records = []
    profiles = {}
    target = artifacts / "native"
    if target.exists():
        shutil.rmtree(target)
    for name in names:
        pack = load_pack(name)
        work = work_root / name
        work.mkdir(parents=True, exist_ok=True)
        payload = prepare_payload(base, work, pack)
        compile_native(work, payload, pack)
        profiles[name] = build_include(work, payload, pack)
        destination = target / name
        destination.mkdir(parents=True)
        atomic_copy(payload / "OpenCodeLocal.exe", destination / "OpenCodeLocal.exe")
        atomic_copy(work / "AssembleModel.exe", destination / "AssembleModel.exe")
        build_nsis(work, payload, work / "UninstallerExport.exe", export_uninstaller=destination / "Uninstall.exe")
        for filename in HOUSE_FILES:
            records.append({"path": f"{name}/{filename}", **image_record(destination / filename)})
        # The export-only stub is an intermediate, never an installable release.
        (work / "UninstallerExport.exe").unlink()
    check_source(source, signed=False)
    manifest = {"schema_version": 1, "phase": "native", "source": source, "packs": list(names),
                "profiles": profiles, "base_manifest_sha256": digest(base / "wad/support/payload-manifest.json"),
                "files": records, "signing": "unsigned", "model_downloads": False}
    save_json(artifacts / "native-manifest.json", manifest)
    print("NATIVE SIGNING INPUT", target, "(unsigned; six files for both packs)", flush=True)
    return manifest


def setup_phase(names: tuple[str, ...], base: Path, work_root: Path, artifacts: Path,
                mode: str, signed_native_root: Path | None, expected_signer: str | None,
                sevenzip: Path | None) -> dict:
    native = load_manifest(artifacts / "native-manifest.json", "native")
    if list(names) != native["packs"]:
        raise ValueError("--pack must match the native phase")
    check_source(native["source"], signed=mode == "signed")
    verify_base(base)
    if digest(base / "wad/support/payload-manifest.json") != native["base_manifest_sha256"]:
        raise ValueError("The upstream base changed between signing phases")
    if mode == "signed" and signed_native_root is None:
        raise ValueError("Signed setup phase requires --signed-native-root")
    incoming = signed_native_root if mode == "signed" else artifacts / "native"
    validated = validate_files(incoming, native["files"], signed=mode == "signed", expected_signer=expected_signer)
    target = artifacts / "setup"
    if target.exists():
        shutil.rmtree(target)
    records = []
    profiles = {}
    for name in names:
        pack = load_pack(name)
        work = work_root / name
        work.mkdir(parents=True, exist_ok=True)
        payload = prepare_payload(base, work, pack)
        atomic_copy(incoming / name / "OpenCodeLocal.exe", payload / "OpenCodeLocal.exe")
        atomic_copy(incoming / name / "AssembleModel.exe", work / "AssembleModel.exe")
        atomic_copy(incoming / name / "Uninstall.exe", payload / "wad/support/Uninstall.exe")
        (payload / "wad/docs/LISEZ-MOI.txt").write_text(make_readme(pack, mode), encoding="utf-8")
        # Hash after importing signed bytes, including the signed uninstaller.
        profiles[name] = build_include(work, payload, pack)
        destination = target / name
        destination.mkdir(parents=True)
        setup = destination / "Setup.exe"
        build_nsis(work, payload, setup, import_uninstaller=payload / "wad/support/Uninstall.exe")
        if sevenzip:
            run([sevenzip, "t", setup], work / "installer-integrity.log")
        records.append({"path": f"{name}/Setup.exe", **image_record(setup)})
    check_source(native["source"], signed=mode == "signed")
    manifest = {"schema_version": 1, "phase": "setup", "source": native["source"],
                "packs": list(names), "profiles": profiles, "files": records,
                "native_manifest_sha256": digest(artifacts / "native-manifest.json"),
                "native_signing": mode, "native_validation": validated,
                "signing": "unsigned", "installer_archive_integrity": "passed" if sevenzip else "not_run"}
    save_json(artifacts / "setup-manifest.json", manifest)
    print("SETUP SIGNING INPUT", target, "(setups await signature)", flush=True)
    return manifest


def finalize_phase(names: tuple[str, ...], artifacts: Path, output_root: Path, mode: str,
                   signed_native_root: Path | None, signed_setup_root: Path | None,
                   expected_signer: str | None, model_dir: Path | None = None,
                   include_parts: bool = False) -> dict:
    # Deliberately independent of build/payload and work_root: a Windows job can
    # validate the two returned archives and finalize without large dependencies.
    native = load_manifest(artifacts / "native-manifest.json", "native")
    setup_manifest = load_manifest(artifacts / "setup-manifest.json", "setup")
    if native["packs"] != list(names) or setup_manifest["packs"] != list(names):
        raise ValueError("--pack must match both preceding phases")
    if setup_manifest["native_manifest_sha256"] != digest(artifacts / "native-manifest.json"):
        raise ValueError("Native manifest changed after setup assembly")
    if native["source"] != setup_manifest["source"] or setup_manifest["native_signing"] != mode:
        raise ValueError("Source or signing mode changed between phases")
    check_source(native["source"], signed=mode == "signed")
    if mode == "signed" and (signed_native_root is None or signed_setup_root is None):
        raise ValueError("Signed finalization requires --signed-native-root and --signed-setup-root")
    native_root = signed_native_root if mode == "signed" else artifacts / "native"
    setup_root = signed_setup_root if mode == "signed" else artifacts / "setup"
    natives = validate_files(native_root, native["files"], signed=mode == "signed", expected_signer=expected_signer)
    setups = validate_files(setup_root, setup_manifest["files"], signed=mode == "signed", expected_signer=expected_signer)
    # A second signature on the same native image would still pass image matching
    # but differ from the bytes actually embedded. Reject that mismatch too.
    for path, value in natives.items():
        if value["sha256"] != setup_manifest["native_validation"][path]["sha256"]:
            raise ValueError("Native differs from the executable embedded in Setup.exe: " + path)
    results = []
    for name in names:
        pack = load_pack(name)
        output = output_root / name
        output.mkdir(parents=True, exist_ok=True)
        setup = setup_root / name / "Setup.exe"
        zip_name = f'Setup-{pack["ram_gib"]}Go-{pack["app_version"]}.zip'
        with tempfile.TemporaryDirectory(prefix="opencode-final-") as temporary:
            archive = Path(temporary) / zip_name
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zipped:
                zipped.write(setup, "Setup.exe")
            with zipfile.ZipFile(archive) as zipped:
                if zipped.namelist() != ["Setup.exe"] or zipped.testzip() is not None:
                    raise ValueError("Installer ZIP validation failed")
                with zipped.open("Setup.exe") as extracted:
                    if hashlib.file_digest(extracted, "sha256").hexdigest() != digest(setup):
                        raise ValueError("ZIP does not contain the exact final Setup.exe")
            atomic_copy(archive, output / zip_name)
        (output / f'LISEZ-MOI-{pack["ram_gib"]}Go.txt').write_text(make_readme(pack, mode), encoding="utf-8")
        save_json(output / f'PACK-{pack["ram_gib"]}Go.json', pack)
        save_json(output / "MODELE.json", {**pack["model"], "parts": pack["parts"]})
        parts = []
        if include_parts:
            if model_dir is None:
                raise ValueError("--model-dir required for --include-parts")
            parts = split_model(model_dir / pack["model"]["filename"], pack, output)
        checksums = {zip_name: digest(output / zip_name), "Setup.exe": digest(setup)}
        checksums.update({part["name"]: part["sha256"] for part in parts})
        if name == "16go" and not parts:
            checksums.update({"Setup-1.bin": "416e11f3cf0391279546e73fd7a3658c368ae4328f0a94d34b6d7d6d0394964f",
                              "Setup-2.bin": "d4761367a069f853868471b7c11cd9a65c6112e5d9476b9c3ac2bd32bcebacfb"})
        checksums[pack["model"]["filename"]] = pack["model"]["sha256"]
        (output / f'SHA256SUMS-{pack["ram_gib"]}Go.txt').write_text("".join(f"{sha}  {filename}\n" for filename, sha in checksums.items()), encoding="ascii")
        result = {"pack": name, "version": pack["app_version"], "source_commit": native["source"]["commit"],
                  "source_inputs_sha256": native["source"]["inputs_sha256"], "signing": mode,
                  "model": pack["model"], "zip": str(output / zip_name), "zip_contains": ["Setup.exe"],
                  "zip_bytes": (output / zip_name).stat().st_size, "zip_sha256": checksums[zip_name],
                  "setup": setups[f"{name}/Setup.exe"], "native_validation": {p: v for p, v in natives.items() if p.startswith(name + "/")},
                  "parts": parts, "model_downloads": False, "tehtris_compatibility": "not_validated"}
        save_json(output / "SIGNATURES.json", result)
        results.append(result)
    result = {"schema_version": 1, "phase": "finalize", "signing": mode, "packs": results}
    save_json(artifacts / "finalize-manifest.json", result)
    print("COMPLETE", output_root, "AUTHENTICODE:", mode, flush=True)
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("native", "setup", "finalize", "all"), default="all")
    parser.add_argument("--signing", choices=("unsigned", "signed"), required=True,
                        help="Explicit unsigned development build, or fail-closed signed import")
    parser.add_argument("--pack", choices=("16go", "32go", "all"), default="all")
    parser.add_argument("--base-payload", type=Path)
    parser.add_argument("--work-root", type=Path, default=ROOT / "build/signing")
    parser.add_argument("--artifact-root", type=Path, default=ROOT / "signing")
    parser.add_argument("--output-root", type=Path, default=ROOT / "dist")
    parser.add_argument("--signed-native-root", type=Path)
    parser.add_argument("--signed-setup-root", type=Path)
    parser.add_argument("--expected-signer-sha256")
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--include-parts", action="store_true")
    parser.add_argument("--setup-only", action="store_true", help="Never fetch or split a model; .bin files remain untouched")
    parser.add_argument("--sevenzip", type=Path)
    args = parser.parse_args(argv)
    if args.setup_only and args.include_parts:
        parser.error("--setup-only and --include-parts are mutually exclusive")
    if args.signing == "signed" and args.phase in ("native", "all"):
        parser.error("Native compilation is unsigned; use explicit native -> signing service -> setup -> signing service -> finalize phases")
    if args.phase in ("native", "setup", "all") and args.base_payload is None:
        parser.error("--base-payload is required for native/setup phases")
    names = ("16go", "32go") if args.pack == "all" else (args.pack,)
    artifacts = args.artifact_root.resolve()
    work = args.work_root.resolve()
    base = args.base_payload.resolve(strict=True) if args.base_payload else None
    native_root = args.signed_native_root.resolve(strict=True) if args.signed_native_root else None
    setup_root = args.signed_setup_root.resolve(strict=True) if args.signed_setup_root else None
    if args.phase in ("native", "all"):
        native_phase(names, base, work, artifacts)
    if args.phase in ("setup", "all"):
        setup_phase(names, base, work, artifacts, args.signing, native_root, args.expected_signer_sha256, args.sevenzip)
    if args.phase in ("finalize", "all"):
        finalize_phase(names, artifacts, args.output_root.resolve(), args.signing, native_root, setup_root,
                       args.expected_signer_sha256, args.model_dir, args.include_parts)


if __name__ == "__main__":
    main()
