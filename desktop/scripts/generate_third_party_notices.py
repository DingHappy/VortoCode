"""Generate deterministic notices from the files PyInstaller actually froze."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any


LICENSE_PREFIXES = ("COPYING", "COPYRIGHT", "LICENSE", "NOTICE")


def _walk_strings(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set)):
        strings: set[str] = set()
        for item in value:
            strings.update(_walk_strings(item))
        return strings
    if isinstance(value, dict):
        strings = set()
        for key, item in value.items():
            strings.update(_walk_strings(key))
            strings.update(_walk_strings(item))
        return strings
    return set()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _license_files(distribution: importlib.metadata.Distribution) -> list[tuple[str, Path]]:
    found: dict[str, Path] = {}
    for entry in distribution.files or ():
        name = Path(str(entry)).name.upper()
        if not name.startswith(LICENSE_PREFIXES):
            continue
        path = Path(distribution.locate_file(entry)).resolve()
        if path.is_file():
            found[str(entry)] = path
    return [(key, found[key]) for key in sorted(found, key=str.casefold)]


def _declared_license(metadata: importlib.metadata.PackageMetadata) -> str:
    expression = (metadata.get("License-Expression") or "").strip()
    if expression:
        return expression
    declared = (metadata.get("License") or "").strip()
    if declared and declared.upper() not in {"UNKNOWN", "N/A", "NONE"}:
        return declared
    classifiers = [
        item.removeprefix("License :: ").strip()
        for item in metadata.get_all("Classifier", [])
        if item.startswith("License :: ")
    ]
    return "; ".join(classifiers)


def _project_url(metadata: importlib.metadata.PackageMetadata) -> str:
    urls: dict[str, str] = {}
    for value in metadata.get_all("Project-URL", []):
        if "," in value:
            label, url = value.split(",", 1)
            urls[label.strip().casefold()] = url.strip()
    for label in ("source", "repository", "homepage", "home"):
        if urls.get(label):
            return urls[label]
    return (metadata.get("Home-page") or "").strip()


def _python_runtime() -> dict[str, Any]:
    standalone_root = Path(sys.base_prefix).parent
    metadata_path = standalone_root / "PYTHON.json"
    licenses_root = standalone_root / "licenses"
    if metadata_path.is_file() and licenses_root.is_dir():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        license_paths = sorted(licenses_root.glob("LICENSE*.txt"), key=lambda path: path.name.casefold())
        if not license_paths:
            raise RuntimeError(f"Standalone CPython license bundle is empty: {licenses_root}")
        provider = "astral-sh/python-build-standalone"
        distribution = "cpython"
        spdx_licenses = metadata.get("licenses", [])
        build_options = metadata.get("build_options", "")
        target_triple = metadata.get("target_triple", "")
        metadata_sha256 = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
    else:
        version = f"python{sys.version_info.major}.{sys.version_info.minor}"
        candidates = [
            Path(sys.base_prefix) / "lib" / version / "LICENSE.txt",
            Path(sys.base_prefix) / "LICENSE.txt",
        ]
        license_paths = [candidate for candidate in candidates if candidate.is_file()]
        if not license_paths:
            raise RuntimeError(f"Could not locate the CPython license below {sys.base_prefix}")
        is_anaconda = "anaconda" in sys.version.casefold()
        provider = "anaconda" if is_anaconda else "system"
        distribution = "anaconda" if is_anaconda else "cpython"
        spdx_licenses = ["Python-2.0", "CNRI-Python"]
        build_options = ""
        target_triple = ""
        metadata_sha256 = ""

    return {
        "buildOptions": build_options,
        "compiler": platform.python_compiler(),
        "distribution": distribution,
        "implementation": platform.python_implementation(),
        "licenseFiles": [
            {
                "path": str(path.relative_to(standalone_root)) if path.is_relative_to(standalone_root) else path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "text": path.read_text(encoding="utf-8", errors="replace").strip(),
            }
            for path in license_paths
        ],
        "machine": platform.machine(),
        "metadataSha256": metadata_sha256,
        "provider": provider,
        "spdxLicenses": spdx_licenses,
        "targetTriple": target_triple,
        "version": platform.python_version(),
    }


def build_inventory(analysis_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    analysis = ast.literal_eval(analysis_path.read_text(encoding="utf-8"))
    frozen_paths = {
        str(Path(value).resolve())
        for value in _walk_strings(analysis)
        if os.path.isabs(value) and "site-packages" in Path(value).parts
    }

    components: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for distribution in importlib.metadata.distributions():
        owned_paths = {
            str(Path(distribution.locate_file(entry)).resolve())
            for entry in distribution.files or ()
        }
        included = sorted(frozen_paths.intersection(owned_paths))
        if not included:
            continue

        metadata = distribution.metadata
        name = metadata.get("Name") or "unknown"
        license_paths = _license_files(distribution)
        declared_license = _declared_license(metadata)
        if not license_paths and not declared_license:
            unresolved.append(f"{name}=={distribution.version}")
        components.append(
            {
                "declaredLicense": declared_license,
                "licenseFiles": [
                    {
                        "path": relative_path,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "text": path.read_text(encoding="utf-8", errors="replace").strip(),
                    }
                    for relative_path, path in license_paths
                ],
                "name": name,
                "projectUrl": _project_url(metadata),
                "sourceFileCount": len(included),
                "version": distribution.version,
            }
        )

    if unresolved:
        raise RuntimeError(
            "Frozen distributions without license metadata or files: " + ", ".join(sorted(unresolved))
        )
    components.sort(key=lambda item: (item["name"].casefold(), item["version"]))
    if not components:
        raise RuntimeError("PyInstaller analysis did not map to any installed distributions")

    runtime = _python_runtime()
    return runtime, components


def render_notices(runtime: dict[str, Any], components: list[dict[str, Any]]) -> str:
    lines = [
        "VortoCode Desktop Third-Party Notices",
        "======================================",
        "",
        "This file is generated from the source and metadata paths present in the",
        "PyInstaller Analysis table for the bundled core Gateway runtime.",
        "",
        f"Python runtime: {runtime['implementation']} {runtime['version']} ({runtime['distribution']}, {runtime['machine']})",
        f"Frozen Python distributions: {len(components)}",
        "",
    ]
    for license_file in runtime["licenseFiles"]:
        lines.extend(
            [
                f"--- {runtime['implementation']} runtime: {license_file['path']} ---",
                license_file["text"],
                "",
            ]
        )
    for component in components:
        lines.extend(
            [
                f"=== {component['name']} {component['version']} ===",
                f"Declared license: {component['declaredLicense'] or 'See bundled license text'}",
                f"Project: {component['projectUrl'] or 'Not declared in package metadata'}",
                f"Frozen source evidence: {component['sourceFileCount']} files",
                "",
            ]
        )
        if component["licenseFiles"]:
            for license_file in component["licenseFiles"]:
                lines.extend(
                    [
                        f"--- {license_file['path']} ---",
                        license_file["text"],
                        "",
                    ]
                )
        else:
            lines.extend([component["declaredLicense"], ""])
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--notices", required=True, type=Path)
    args = parser.parse_args()

    runtime, components = build_inventory(args.analysis)
    inventory = {
        "componentCount": len(components),
        "components": [
            {key: value for key, value in component.items() if key != "licenseFiles"}
            | {
                "licenseFiles": [
                    {key: value for key, value in license_file.items() if key != "text"}
                    for license_file in component["licenseFiles"]
                ]
            }
            for component in components
        ],
        "formatVersion": 1,
        "pythonRuntime": {
            key: value for key, value in runtime.items() if key != "licenseFiles"
        }
        | {
            "licenseFiles": [
                {key: value for key, value in license_file.items() if key != "text"}
                for license_file in runtime["licenseFiles"]
            ]
        },
    }
    _atomic_write(args.inventory, json.dumps(inventory, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(args.notices, render_notices(runtime, components))
    print(f"Generated notices for {len(components)} frozen Python distributions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
