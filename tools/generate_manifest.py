#!/usr/bin/env python3
"""Generate RetroHall manifest files from FC_ROMS."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import quote


ROM_EXTENSIONS = {".nes", ".fds"}
COVER_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
README_ROW_RE = re.compile(r"^\|\s*\[(?P<id>\d{4})\]\(\./ROM/(?P=id)\)\s*\|")


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slugify(text: str) -> str:
    value = text.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "game"


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def raw_url(repository: str | None, ref: str | None, relative_path: str) -> str | None:
    if not repository or not ref or not relative_path:
        return None
    return f"https://raw.githubusercontent.com/{repository}/{ref}/{quote(relative_path)}"


def split_markdown_row(line: str) -> list[str]:
    return [part.strip() for part in line.strip().strip("|").split("|")]


def parse_readme_index(root: Path) -> dict[str, dict[str, Any]]:
    readme = root / "README.md"
    if not readme.exists():
        return {}

    index: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(readme.read_text(encoding="utf-8").splitlines(), start=1):
        if not README_ROW_RE.match(line):
            continue
        cells = split_markdown_row(line)
        if len(cells) < 6:
            continue
        game_id = re.search(r"\[(\d{4})\]", cells[0])
        if not game_id:
            continue
        key = game_id.group(1)
        release_date = cells[1]
        warnings: list[str] = []
        if release_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", release_date):
            warnings.append(f"README 第 {line_number} 行发售日期格式异常：{release_date}")
        elif release_date.startswith("19") and release_date < "1970-01-01":
            warnings.append(f"README 第 {line_number} 行发售日期可疑：{release_date}")

        index[key] = {
            "id": key,
            "releaseDate": release_date,
            "platform": cells[2] or "FC/NES",
            "title": {
                "ja": cells[3],
                "en": cells[4],
                "zh": cells[5],
            },
            "metadataWarnings": warnings,
        }
    return index


def file_hashes(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "sizeBytes": len(data),
        "crc32": f"{zlib.crc32(data) & 0xFFFFFFFF:08X}",
        "md5": hashlib.md5(data).hexdigest().upper(),
        "sha1": hashlib.sha1(data).hexdigest().upper(),
        "sha256": hashlib.sha256(data).hexdigest().upper(),
    }


def parse_ines(path: Path) -> dict[str, Any] | None:
    data = path.read_bytes()
    if len(data) < 16 or data[:4] != b"NES\x1A":
        return None

    flags6 = data[6]
    flags7 = data[7]
    trainer_size = 512 if flags6 & 0x04 else 0
    prg_rom_bytes = data[4] * 16 * 1024
    chr_rom_bytes = data[5] * 8 * 1024
    expected_size = 16 + trainer_size + prg_rom_bytes + chr_rom_bytes

    return {
        "container": "iNES",
        "format": "NES 2.0" if (flags7 & 0x0C) == 0x08 else "iNES",
        "mapper": (flags6 >> 4) | (flags7 & 0xF0),
        "mirroring": "vertical" if flags6 & 0x01 else "horizontal",
        "batteryBacked": bool(flags6 & 0x02),
        "trainerPresent": bool(flags6 & 0x04),
        "prgRomBytes": prg_rom_bytes,
        "chrRomBytes": chr_rom_bytes,
        "expectedSizeBytes": expected_size,
        "extraBytes": len(data) - expected_size,
    }


def parse_rom_filename(path: Path) -> dict[str, Any]:
    stem = path.stem
    tags = re.findall(r"\(([^()]*)\)|\[([^\[\]]*)\]", stem)
    flat_tags = [left or right for left, right in tags if left or right]
    title = re.sub(r"\s*(\([^()]*\)|\[[^\[\]]*\])", "", stem).strip()
    region = next((tag for tag in flat_tags if tag in {"J", "U", "E", "W", "JU", "UE", "USA", "World", "Japan", "Europe"}), "")
    quality_tags = [tag for tag in flat_tags if tag in {"!", "b", "h", "o"} or tag.startswith(("b", "h", "o"))]
    revision = next((tag for tag in flat_tags if tag.upper().startswith(("REV", "PRG"))), "")
    return {
        "title": title or stem,
        "region": region,
        "revision": revision,
        "tags": flat_tags,
        "qualityTags": quality_tags,
    }


def find_cover(game_dir: Path) -> Path | None:
    if not game_dir.exists():
        return None
    covers = sorted(
        (child for child in game_dir.iterdir() if child.is_file() and child.suffix.lower() in COVER_EXTENSIONS),
        key=lambda item: item.name.lower(),
    )
    if not covers:
        return None
    for candidate in covers:
        if candidate.stem.lower() in {"cover", "boxart", "folder"}:
            return candidate
    return covers[0]


def rom_entry(root: Path, path: Path, repository: str | None, ref: str | None) -> dict[str, Any]:
    parsed = parse_rom_filename(path)
    relative_path = rel(path, root)
    entry: dict[str, Any] = {
        "path": relative_path,
        "filename": path.name,
        "format": path.suffix.lower().lstrip(".").upper(),
        "titleFromFilename": parsed["title"],
        "region": parsed["region"],
        "revision": parsed["revision"],
        "tags": parsed["tags"],
        "qualityTags": parsed["qualityTags"],
        "url": raw_url(repository, ref, relative_path),
        "hash": file_hashes(path),
    }
    ines = parse_ines(path) if path.suffix.lower() == ".nes" else None
    if ines:
        entry["ines"] = ines
    return entry


def discover_rom_dirs(root: Path) -> dict[str, list[Path]]:
    rom_root = root / "ROM"
    result: dict[str, list[Path]] = {}
    if not rom_root.exists():
        return result
    for game_dir in sorted((item for item in rom_root.iterdir() if item.is_dir()), key=lambda item: item.name):
        if not re.match(r"^\d{4}$", game_dir.name):
            continue
        roms = sorted(
            (item for item in game_dir.iterdir() if item.is_file() and item.suffix.lower() in ROM_EXTENSIONS),
            key=lambda item: item.name.lower(),
        )
        result[game_dir.name] = roms
    return result


def build_manifest(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    repository = os.environ.get("GITHUB_REPOSITORY")
    ref = os.environ.get("GITHUB_SHA") or os.environ.get("GITHUB_REF_NAME")
    generated_at = utc_now()
    readme_index = parse_readme_index(root)
    rom_dirs = discover_rom_dirs(root)
    all_ids = sorted(set(readme_index) | set(rom_dirs))

    games: list[dict[str, Any]] = []
    warnings: list[str] = []

    for game_id in all_ids:
        metadata = readme_index.get(game_id)
        game_dir = root / "ROM" / game_id
        roms = rom_dirs.get(game_id, [])
        title = metadata.get("title") if metadata else {"ja": "", "en": "", "zh": ""}
        primary_title = title.get("zh") or title.get("en") or title.get("ja") or f"FC 游戏 {game_id}"
        game_warnings = list(metadata.get("metadataWarnings", []) if metadata else [f"README 缺少编号 {game_id}"])
        if metadata and not roms:
            game_warnings.append(f"README 有编号 {game_id}，但没有对应 ROM 文件")
        if not metadata and roms:
            game_warnings.append(f"ROM 目录 {game_id} 没有 README 索引行")

        cover = find_cover(game_dir)
        relative_cover = rel(cover, root) if cover else ""
        entries = [rom_entry(root, rom, repository, ref) for rom in roms]

        game = {
            "id": game_id,
            "slug": slugify(f"{game_id}-{primary_title}"),
            "title": title,
            "displayTitle": primary_title,
            "releaseDate": metadata.get("releaseDate", "") if metadata else "",
            "platform": metadata.get("platform", "FC/NES") if metadata else "FC/NES",
            "category": "",
            "description": "",
            "romDir": rel(game_dir, root),
            "romCount": len(entries),
            "roms": entries,
            "assets": {
                "cover": relative_cover or None,
                "coverUrl": raw_url(repository, ref, relative_cover) if relative_cover else None,
                "screenshots": [],
            },
            "metadataWarnings": game_warnings,
        }
        warnings.extend(f"{game_id}: {item}" for item in game_warnings)
        games.append(game)

    manifest = {
        "schemaVersion": "1.0",
        "generatedAt": generated_at,
        "source": {
            "type": "github",
            "repository": repository or "",
            "ref": ref or "",
        },
        "gameCount": len(games),
        "romCount": sum(game["romCount"] for game in games),
        "warningCount": len(warnings),
        "warnings": warnings,
        "games": games,
    }

    search_index = {
        "schemaVersion": "1.0",
        "generatedAt": generated_at,
        "gameCount": len(games),
        "games": [
            {
                "id": game["id"],
                "slug": game["slug"],
                "displayTitle": game["displayTitle"],
                "title": game["title"],
                "platform": game["platform"],
                "releaseDate": game["releaseDate"],
                "romCount": game["romCount"],
                "romDir": game["romDir"],
                "hasCover": bool(game["assets"]["cover"]),
            }
            for game in games
        ],
    }
    return manifest, search_index


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate RetroHall manifest files.")
    parser.add_argument("--repo-root", default=".", help="Repository root path.")
    parser.add_argument("--manifest", default="manifest.v1.json", help="Manifest output path.")
    parser.add_argument("--search-index", default="search-index.v1.json", help="Search index output path.")
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()
    manifest, search_index = build_manifest(root)
    write_json(root / args.manifest, manifest)
    write_json(root / args.search_index, search_index)
    print(f"生成游戏清单：{manifest['gameCount']} 个游戏，{manifest['romCount']} 个 ROM，{manifest['warningCount']} 个警告")
    return 0


if __name__ == "__main__":
    sys.exit(main())
