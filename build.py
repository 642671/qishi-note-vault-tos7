#!/usr/bin/env python3
"""Build a TOS 7 single-package .deb without requiring dpkg-deb.

The script is intentionally written with only the Python standard library so
it can run on Windows, macOS, Linux, or a TOS development machine.
"""

from __future__ import annotations

import argparse
import bz2
import configparser
import gzip
import hashlib
import io
import json
import re
import shutil
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent
APP_ID = "qishi-note-vault"
SYSTEM_ID = "qishinotevault-system"
PLATFORM_ARCH = {"x86_64": "amd64", "aarch64": "arm64"}
REQUIRED_LANGUAGES = {
    "zh-cn",
    "zh-hk",
    "en-us",
    "fr-fr",
    "de-de",
    "it-it",
    "es-es",
    "hu-hu",
    "ja-jp",
    "ko-kr",
    "pl-pl",
    "ru-ru",
    "tr-tr",
    "pt-pt",
}
REQUIRED_LANG_FIELDS = {"name", "auth", "descript"}
MTIME = 1_704_067_200  # 2024-01-01 UTC, deterministic archive timestamp


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return value


def validate_config(config: dict, platform: str) -> None:
    required = {
        "id",
        "icon",
        "publisher",
        "exec",
        "version",
        "low_version",
        "category",
        "platform",
        "user",
        "system_id",
        "package",
        "application_type",
        "path",
        "type",
    }
    missing = sorted(required - set(config))
    if missing:
        raise SystemExit("config.ini is missing fields: " + ", ".join(missing))
    if config["id"] != APP_ID:
        raise SystemExit(f"config.ini id must be {APP_ID}")
    if config["system_id"] != SYSTEM_ID:
        raise SystemExit(f"config.ini system_id must be {SYSTEM_ID}")
    if config["package"] != APP_ID:
        raise SystemExit(f"config.ini package must be {APP_ID}")
    if config["application_type"] != "deb":
        raise SystemExit("config.ini application_type must be deb")
    if config["type"] != "iframe":
        raise SystemExit("config.ini type must be iframe for this application")
    if "open_path" in config:
        raise SystemExit("config.ini must not contain open_path with type=iframe")
    if config["path"] != f"/{APP_ID}/":
        raise SystemExit(f"config.ini path must be /{APP_ID}/")
    if config["platform"] not in PLATFORM_ARCH:
        raise SystemExit("config.ini platform must be x86_64 or aarch64")
    if config["version"] != ROOT.joinpath("VERSION").read_text(encoding="utf-8").strip():
        raise SystemExit("VERSION and config.ini version do not match")
    if not re.fullmatch(r"\d+(?:\.\d+){0,2}", str(config["version"])):
        raise SystemExit("version must contain 1 to 3 numeric segments")


def validate_language(path: Path) -> None:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    sections = set(parser.sections())
    missing_sections = REQUIRED_LANGUAGES - sections
    extra_sections = sections - REQUIRED_LANGUAGES
    if missing_sections or extra_sections:
        raise SystemExit(
            "language sections do not match the TOS 14-language set: "
            f"missing={sorted(missing_sections)}, extra={sorted(extra_sections)}"
        )
    for section in REQUIRED_LANGUAGES:
        for field in REQUIRED_LANG_FIELDS:
            if not parser[section].get(field, "").strip():
                raise SystemExit(f"language section {section} is missing {field}")


def validate_icon(path: Path) -> None:
    if not path.is_file():
        raise SystemExit(f"icon not found: {path}")
    raw = path.read_bytes()
    if len(raw) > 50 * 1024:
        raise SystemExit("icon must be at most 50 KB")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise SystemExit(f"icon is not valid SVG/XML: {exc}") from exc

    forbidden_tags = {"script", "foreignobject", "iframe", "object", "embed"}
    forbidden_prefixes = ("javascript:", "vbscript:", "file:", "ftp:", "about:", "blob:")
    start_tags = 0
    for element in root.iter():
        start_tags += 1
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag in forbidden_tags:
            raise SystemExit(f"icon contains forbidden tag: {tag}")
        for name, value in element.attrib.items():
            if name.lower().startswith("on"):
                raise SystemExit(f"icon contains event attribute: {name}")
            if name.lower().endswith("href") and value.lower().startswith(forbidden_prefixes):
                raise SystemExit(f"icon contains unsafe href: {value}")
    if start_tags > 50:
        raise SystemExit("icon contains too many XML elements")


def normalize_lf(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def make_tar_bytes(base: Path, entries: Iterable[tuple[Path, str]]) -> bytes:
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for source, archive_name in entries:
            if not source.exists():
                raise SystemExit(f"staging entry does not exist: {source}")
            info = archive.gettarinfo(str(source), arcname=archive_name)
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = MTIME
            if info.isdir():
                info.mode = 0o755
                archive.addfile(info)
                continue
            if info.isfile() and source.name in {
                "qishi-note-vault-service",
                "postinst",
                "prerm",
                "postrm",
            }:
                info.mode = 0o755
            else:
                info.mode = 0o644
            if info.isfile():
                data = normalize_lf(source) if source.suffix.lower() in {
                    ".cfg",
                    ".conf",
                    ".html",
                    ".ini",
                    ".js",
                    ".lang",
                    ".md",
                    ".py",
                    ".service",
                    ".sh",
                    ".txt",
                    ".yaml",
                    ".yml",
                } else source.read_bytes()
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            else:
                archive.addfile(info)
    return tar_buffer.getvalue()


def make_tar_gz(base: Path, entries: Iterable[tuple[Path, str]]) -> bytes:
    return gzip.compress(make_tar_bytes(base, entries), compresslevel=9, mtime=0)


def make_tar_bz2(base: Path, entries: Iterable[tuple[Path, str]]) -> bytes:
    return bz2.compress(make_tar_bytes(base, entries), compresslevel=9)


def write_ar(path: Path, members: list[tuple[str, bytes]]) -> None:
    with path.open("wb") as output:
        output.write(b"!<arch>\n")
        for name, data in members:
            encoded_name = name.encode("ascii")
            if len(encoded_name) > 16:
                raise SystemExit(f"ar member name is too long: {name}")
            header = (
                encoded_name.ljust(16, b" ")
                + b"0".ljust(12, b" ")
                + b"0".ljust(6, b" ")
                + b"0".ljust(6, b" ")
                + b"100644".ljust(8, b" ")
                + str(len(data)).encode("ascii").ljust(10, b" ")
                + b"`\n"
            )
            output.write(header)
            output.write(data)
            if len(data) % 2:
                output.write(b"\n")


def read_ar(path: Path) -> dict[str, bytes]:
    raw = path.read_bytes()
    if not raw.startswith(b"!<arch>\n"):
        raise SystemExit(f"{path} is not an ar archive")
    members: dict[str, bytes] = {}
    offset = 8
    while offset < len(raw):
        header = raw[offset : offset + 60]
        if len(header) != 60 or header[58:60] != b"`\n":
            raise SystemExit("invalid ar member header")
        name = header[0:16].decode("ascii").strip().rstrip("/")
        try:
            size = int(header[48:58].decode("ascii").strip())
        except ValueError as exc:
            raise SystemExit("invalid ar member size") from exc
        data_start = offset + 60
        data_end = data_start + size
        members[name] = raw[data_start:data_end]
        offset = data_end + (size % 2)
    return members


def verify_package(path: Path, platform: str, version: str) -> None:
    members = read_ar(path)
    expected_members = {"debian-binary", "control.tar.gz", "data.tar.gz"}
    if set(members) != expected_members:
        raise SystemExit(f"unexpected deb members: {sorted(members)}")
    if members["debian-binary"] != b"2.0\n":
        raise SystemExit("debian-binary must contain 2.0")

    with tarfile.open(fileobj=io.BytesIO(members["control.tar.gz"]), mode="r:gz") as control:
        control_names = {member.name for member in control.getmembers()}
        required_control = {"./control", "./md5sums", "./postinst", "./prerm", "./postrm"}
        if not required_control.issubset(control_names):
            raise SystemExit("control tar is missing lifecycle files")
        control_file = control.extractfile("./control")
        if control_file is None:
            raise SystemExit("control file cannot be read")
        control_text = control_file.read().decode("utf-8")
        if f"Version: {version}" not in control_text:
            raise SystemExit("control version mismatch")
        if f"Architecture: {PLATFORM_ARCH[platform]}" not in control_text:
            raise SystemExit("control architecture mismatch")
        for script_name in ("./postinst", "./prerm", "./postrm"):
            member = control.getmember(script_name)
            if not member.mode & 0o111:
                raise SystemExit(f"{script_name} is not executable")

    with tarfile.open(fileobj=io.BytesIO(members["data.tar.gz"]), mode="r:gz") as data:
        data_names = {member.name for member in data.getmembers()}
        required_data = {
            f"./usr/local/{APP_ID}/config.ini",
            f"./usr/local/{APP_ID}/{APP_ID}.lang",
            f"./usr/local/{APP_ID}/{APP_ID}.env",
            f"./usr/local/{APP_ID}/bin/{APP_ID}-service",
            f"./usr/local/{APP_ID}/images/icons/{APP_ID}.svg",
            f"./usr/local/{APP_ID}/init.d/{SYSTEM_ID}.service",
            f"./usr/local/{APP_ID}/webui.bz2",
            f"./usr/local/{APP_ID}/lib/qishi_note_vault/server.py",
            f"./usr/local/{APP_ID}/lib/qishi_note_vault/database.py",
            f"./usr/local/{APP_ID}/lib/qishi_note_vault/notes.py",
        }
        missing = required_data - data_names
        if missing:
            raise SystemExit("data tar is missing: " + ", ".join(sorted(missing)))
        config_file = data.extractfile(f"./usr/local/{APP_ID}/config.ini")
        if config_file is None:
            raise SystemExit("packaged config.ini cannot be read")
        packaged_config = json.loads(config_file.read().decode("utf-8"))
        if packaged_config.get("platform") != platform:
            raise SystemExit("packaged config platform mismatch")
        if packaged_config.get("version") != version:
            raise SystemExit("packaged config version mismatch")
        executable = data.getmember(f"./usr/local/{APP_ID}/bin/{APP_ID}-service")
        if not executable.mode & 0o111:
            raise SystemExit("backend entry point is not executable")
        webui_file = data.extractfile(f"./usr/local/{APP_ID}/webui.bz2")
        if webui_file is None:
            raise SystemExit("webui.bz2 cannot be read")
        with tarfile.open(fileobj=io.BytesIO(webui_file.read()), mode="r:bz2") as webui:
            webui_names = {member.name for member in webui.getmembers()}
            if not {"./index.html", "./app.js", "./styles.css"}.issubset(webui_names):
                raise SystemExit("webui.bz2 is missing frontend files")


def md5_file(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def build(platform: str, clean: bool) -> Path:
    if platform not in PLATFORM_ARCH:
        raise SystemExit("platform must be x86_64 or aarch64")

    version = ROOT.joinpath("VERSION").read_text(encoding="utf-8").strip()
    config = read_json(ROOT / "config.ini")
    validate_config(config, platform)
    validate_language(ROOT / f"{APP_ID}.lang")
    validate_icon(ROOT / "images" / "icons" / f"{APP_ID}.svg")

    build_dir = ROOT / "build" / platform
    staging = build_dir / "staging"
    output_dir = ROOT / "dist"
    if clean and build_dir.exists():
        shutil.rmtree(build_dir)
    if staging.exists():
        shutil.rmtree(staging)
    output_dir.mkdir(parents=True, exist_ok=True)
    package_root = staging / "usr" / "local" / APP_ID
    (staging / "DEBIAN").mkdir(parents=True)
    (package_root / "bin").mkdir(parents=True)
    (package_root / "lib").mkdir(parents=True)
    (package_root / "images" / "icons").mkdir(parents=True)
    (package_root / "init.d").mkdir(parents=True)
    (package_root / "data").mkdir(parents=True)
    (package_root / "logs").mkdir(parents=True)

    staged_config = dict(config)
    staged_config["platform"] = platform
    (package_root / "config.ini").write_text(
        json.dumps(staged_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    shutil.copy2(ROOT / f"{APP_ID}.lang", package_root / f"{APP_ID}.lang")
    shutil.copy2(ROOT / f"{APP_ID}.env", package_root / f"{APP_ID}.env")
    shutil.copy2(ROOT / "images" / "icons" / f"{APP_ID}.svg", package_root / "images" / "icons" / f"{APP_ID}.svg")
    shutil.copy2(ROOT / "init.d" / f"{SYSTEM_ID}.service", package_root / "init.d" / f"{SYSTEM_ID}.service")
    shutil.copy2(
        ROOT / "backend" / "qishi-note-vault-service",
        package_root / "bin" / "qishi-note-vault-service",
    )
    shutil.copytree(
        ROOT / "backend" / "qishi_note_vault",
        package_root / "lib" / "qishi_note_vault",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    # Build the frontend archive at the fixed path required by the TOS parser.
    frontend_entries: list[tuple[Path, str]] = []
    for source in sorted((ROOT / "frontend").rglob("*")):
        if source.is_file():
            frontend_entries.append((source, "./" + source.relative_to(ROOT / "frontend").as_posix()))
    webui = make_tar_bz2(ROOT / "frontend", frontend_entries)
    (package_root / "webui.bz2").write_bytes(webui)

    # Copy lifecycle scripts into the staging control directory.
    for script_name in ("control", "postinst", "prerm", "postrm"):
        shutil.copy2(ROOT / "DEBIAN" / script_name, staging / "DEBIAN" / script_name)

    # Keep control version and architecture synchronized with the selected target.
    control_path = staging / "DEBIAN" / "control"
    control_text = control_path.read_text(encoding="utf-8")
    control_text = re.sub(r"^Version:.*$", f"Version: {version}", control_text, flags=re.MULTILINE)
    control_text = re.sub(
        r"^Architecture:.*$",
        f"Architecture: {PLATFORM_ARCH[platform]}",
        control_text,
        flags=re.MULTILINE,
    )
    control_path.write_text(control_text, encoding="utf-8", newline="\n")

    # Generate md5sums from the staged data files.
    data_entries: list[tuple[Path, str]] = []
    data_entries.append((staging / "usr", "./usr"))
    for path in sorted((staging / "usr").rglob("*")):
        if path == staging / "usr":
            continue
        relative = "./" + path.relative_to(staging).as_posix()
        if path.is_dir():
            data_entries.append((path, relative))
        elif path.is_file():
            data_entries.append((path, relative))

    checksum_lines = []
    for path in sorted((staging / "usr").rglob("*")):
        if path.is_file():
            relative = path.relative_to(staging).as_posix()
            checksum_lines.append(f"{md5_file(path.read_bytes())}  {relative}\n")
    (staging / "DEBIAN" / "md5sums").write_text(
        "".join(checksum_lines),
        encoding="ascii",
        newline="\n",
    )

    control_entries = [
        (staging / "DEBIAN" / name, f"./{name}")
        for name in ("control", "md5sums", "postinst", "prerm", "postrm")
    ]
    control_tar = make_tar_gz(staging / "DEBIAN", control_entries)
    data_tar = make_tar_gz(staging, data_entries)
    archive_path = output_dir / f"{APP_ID}_{version}_{platform}.deb"
    write_ar(
        archive_path,
        [
            ("debian-binary", b"2.0\n"),
            ("control.tar.gz", control_tar),
            ("data.tar.gz", data_tar),
        ],
    )
    verify_package(archive_path, platform, version)
    checksum = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    (output_dir / f"{archive_path.name}.sha256").write_text(
        f"{checksum}  {archive_path.name}\n",
        encoding="ascii",
        newline="\n",
    )
    print(f"Built: {archive_path}")
    print("Verification: OK")
    print(f"SHA256: {checksum}")
    print(f"Size: {archive_path.stat().st_size} bytes")
    return archive_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Qishi Note Vault TOS 7 package")
    parser.add_argument(
        "--platform",
        choices=sorted(PLATFORM_ARCH),
        default="x86_64",
        help="target platform",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="keep the existing platform build directory",
    )
    args = parser.parse_args()
    build(args.platform, clean=not args.no_clean)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
