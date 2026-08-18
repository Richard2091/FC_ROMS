#!/usr/bin/env python3
"""Preprocess FC/NES title-screen covers from no-intro-pictures."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote, unquote, urljoin

from PIL import Image


ROM_EXTENSIONS = {".nes", ".fds"}
SOURCE_REPOSITORY = "teeedubb/no-intro-pictures"
SOURCE_REF = "master"
LIBRETRO_SOURCE_NAME = "libretro-thumbnails"
NES_TITLE_ROOT = "Nintendo - Nintendo Entertainment System/Named_Titles"
FDS_TITLE_ROOT = "Nintendo - Famicom Disk System/Named_Titles"
LIBRETRO_TITLE_SOURCES = {
    "nes": (
        NES_TITLE_ROOT,
        "https://thumbnails.libretro.com/Nintendo%20-%20Nintendo%20Entertainment%20System/Named_Titles/",
    ),
    "fds": (
        FDS_TITLE_ROOT,
        "https://thumbnails.libretro.com/Nintendo%20-%20Family%20Computer%20Disk%20System/Named_Titles/",
    ),
}
REGION_EXPANSIONS = {
    "J": ["Japan"],
    "U": ["USA"],
    "E": ["Europe"],
    "W": ["World"],
    "JU": ["Japan, USA"],
    "UE": ["USA, Europe"],
}
REGION_PRIORITY = {
    "U": ["usa", "usa, europe", "world", "japan", "europe"],
    "USA": ["usa", "usa, europe", "world", "japan", "europe"],
    "W": ["world", "usa, europe", "usa", "japan", "europe"],
    "WORLD": ["world", "usa, europe", "usa", "japan", "europe"],
    "J": ["japan", "world", "usa"],
    "JAPAN": ["japan", "world", "usa"],
    "E": ["europe", "usa, europe", "world", "usa", "japan"],
    "EUROPE": ["europe", "usa, europe", "world", "usa", "japan"],
}


@dataclass(frozen=True)
class SourceImage:
    platform: str
    path: str
    stem: str
    url: str = ""


def normalize(text: str) -> str:
    value = text.casefold()
    value = re.sub(r"['’]", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def title_key_variants(text: str) -> list[str]:
    key = normalize(text)
    if not key:
        return []
    variants = {key}
    words = key.split()
    articles = {"the", "a", "an"}
    if len(words) > 1 and words[0] in articles:
        variants.add(" ".join(words[1:] + [words[0]]))
        variants.add(" ".join(words[1:]))
    if len(words) > 1 and words[-1] in articles:
        variants.add(" ".join([words[-1]] + words[:-1]))
        variants.add(" ".join(words[:-1]))
    return sorted(variant for variant in variants if variant)


def base_title(stem: str) -> str:
    value = re.sub(r"\s*\([^()]*\)", "", stem)
    value = re.sub(r"\s*\[[^\[\]]*\]", "", value)
    return value.strip()


def clean_rom_stem(stem: str) -> str:
    value = re.sub(r"\s*\[[^\[\]]*\]", "", stem)
    return re.sub(r"\s+", " ", value).strip()


def region_tags(stem: str) -> list[str]:
    tags = re.findall(r"\(([^()]*)\)|\[([^\[\]]*)\]", stem)
    flat = [left or right for left, right in tags if left or right]
    return [tag.upper() for tag in flat]


def expand_region_candidates(stem: str) -> list[str]:
    candidates = [clean_rom_stem(stem)]

    def replace_region(match: re.Match[str]) -> str:
        tag = match.group(1).upper()
        if tag in REGION_EXPANSIONS:
            return f"({REGION_EXPANSIONS[tag][0]})"
        return match.group(0)

    expanded = re.sub(r"\(([^()]*)\)", replace_region, clean_rom_stem(stem))
    candidates.append(expanded)
    candidates.append(base_title(stem))
    seen: set[str] = set()
    result: list[str] = []
    for candidate in candidates:
        key = normalize(candidate)
        if candidate and key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def rom_priority(path: Path) -> tuple[int, int, str]:
    tags = region_tags(path.stem)
    tag_text = " ".join(tags)
    region_score = 9
    for idx, marker in enumerate(["U", "USA", "W", "WORLD", "JU", "UE", "J", "JAPAN", "E", "EUROPE"]):
        if marker in tags or marker in tag_text:
            region_score = idx
            break
    format_score = 0 if path.suffix.lower() == ".nes" else 1
    return (region_score, format_score, path.name.casefold())


def source_platform(path: Path) -> str:
    return "fds" if path.suffix.lower() == ".fds" else "nes"


def run_git_lines(source_git: Path, title_root: str) -> list[str]:
    command = ["git", "-C", str(source_git), "ls-tree", "-r", "--name-only", "HEAD", title_root]
    completed = subprocess.run(command, check=True, text=True, capture_output=True)
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def build_source_index(source_git: Path) -> tuple[dict[str, dict[str, SourceImage]], dict[str, dict[str, list[SourceImage]]]]:
    exact: dict[str, dict[str, SourceImage]] = {"nes": {}, "fds": {}}
    by_base: dict[str, dict[str, list[SourceImage]]] = {"nes": {}, "fds": {}}
    for platform, root in [("nes", NES_TITLE_ROOT), ("fds", FDS_TITLE_ROOT)]:
        for item in run_git_lines(source_git, root):
            if not item.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                continue
            stem = Path(item).stem
            source = SourceImage(platform=platform, path=item, stem=stem)
            exact[platform][normalize(stem)] = source
            for key in title_key_variants(base_title(stem)):
                by_base[platform].setdefault(key, []).append(source)
    return exact, by_base


def cached_url_text(url: str, cache_dir: Path) -> str:
    cache_name = hashlib.sha256(url.encode("utf-8")).hexdigest() + ".html"
    cache_path = cache_dir / cache_name
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8", errors="replace")
    request = urllib.request.Request(url, headers={"User-Agent": "FC_ROMS cover preprocessor"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(data)
    return data.decode("utf-8", errors="replace")


def build_libretro_index(
    cache_dir: Path,
    nes_index_html: Path | None = None,
    fds_index_html: Path | None = None,
) -> tuple[dict[str, dict[str, SourceImage]], dict[str, dict[str, list[SourceImage]]]]:
    exact: dict[str, dict[str, SourceImage]] = {"nes": {}, "fds": {}}
    by_base: dict[str, dict[str, list[SourceImage]]] = {"nes": {}, "fds": {}}
    href_re = re.compile(r'href="([^"]+\.(?:png|jpg|jpeg|webp))"', re.IGNORECASE)
    for platform, (root, base_url) in LIBRETRO_TITLE_SOURCES.items():
        index_html = nes_index_html if platform == "nes" else fds_index_html
        if index_html and index_html.exists():
            html_text = index_html.read_text(encoding="utf-8", errors="replace")
        else:
            html_text = cached_url_text(base_url, cache_dir)
        for href in href_re.findall(html_text):
            file_url = urljoin(base_url, href)
            filename = unquote(file_url.rstrip("/").split("/")[-1])
            stem = Path(filename).stem
            source = SourceImage(platform=platform, path=f"{root}/{filename}", stem=stem, url=file_url)
            exact[platform][normalize(stem)] = source
            for key in title_key_variants(base_title(stem)):
                by_base[platform].setdefault(key, []).append(source)
    return exact, by_base


def score_source(source: SourceImage, rom: Path) -> tuple[int, int, str]:
    tags = region_tags(rom.stem)
    source_norm = normalize(source.stem)
    priorities = ["usa", "usa, europe", "world", "japan", "europe"]
    for tag in tags:
        if tag in REGION_PRIORITY:
            priorities = REGION_PRIORITY[tag]
            break
    region_score = len(priorities) + 5
    for idx, marker in enumerate(priorities):
        if marker in source_norm:
            region_score = idx
            break
    noisy = 0
    if any(word in source_norm for word in ["beta", "prototype", "rev", "sample"]):
        noisy = 1
    return (region_score, noisy, source.stem.casefold())


def match_rom(
    rom: Path,
    exact: dict[str, dict[str, SourceImage]],
    by_base: dict[str, dict[str, list[SourceImage]]],
) -> SourceImage | None:
    platform = source_platform(rom)
    for candidate in expand_region_candidates(rom.stem):
        found = exact[platform].get(normalize(candidate))
        if found:
            return found

    options: list[SourceImage] = []
    seen: set[str] = set()
    for base_key in title_key_variants(base_title(rom.stem)):
        for source in by_base[platform].get(base_key, []):
            if source.path not in seen:
                seen.add(source.path)
                options.append(source)
    if options:
        return sorted(options, key=lambda source: score_source(source, rom))[0]
    return None


def match_game(
    game_dir: Path,
    exact: dict[str, dict[str, SourceImage]],
    by_base: dict[str, dict[str, list[SourceImage]]],
) -> tuple[Path | None, SourceImage | None]:
    roms = sorted(
        (item for item in game_dir.iterdir() if item.is_file() and item.suffix.lower() in ROM_EXTENSIONS),
        key=rom_priority,
    )
    for rom in roms:
        source = match_rom(rom, exact, by_base)
        if source:
            return rom, source
    return (roms[0] if roms else None, None)


def github_raw_url(source_path: str) -> str:
    quoted = quote(source_path, safe="/")
    return f"https://raw.githubusercontent.com/{SOURCE_REPOSITORY}/{SOURCE_REF}/{quoted}"


def source_url(source: SourceImage) -> str:
    return source.url or github_raw_url(source.path)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_source(source: SourceImage, cache_dir: Path) -> bytes:
    cache_name = hashlib.sha256(source.path.encode("utf-8")).hexdigest() + Path(source.path).suffix.lower()
    cache_path = cache_dir / cache_name
    if cache_path.exists():
        return cache_path.read_bytes()
    request = urllib.request.Request(source_url(source), headers={"User-Agent": "FC_ROMS cover preprocessor"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(data)
    return data


def normalize_cover(data: bytes, output: Path) -> None:
    from io import BytesIO

    with Image.open(BytesIO(data)) as source:
        image = source.convert("RGB")
    target_w, target_h = 256, 240
    scale = min(target_w / image.width, target_h / image.height)
    resized = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.NEAREST,
    )
    canvas = Image.new("RGB", (256, 256), (0, 0, 0))
    x = (canvas.width - resized.width) // 2
    y = (canvas.height - resized.height) // 2
    canvas.paste(resized, (x, y))
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="WEBP", quality=88, method=6)


def discover_game_dirs(root: Path) -> list[Path]:
    rom_root = root / "ROM"
    if not rom_root.exists():
        return []
    return [
        item for item in sorted(rom_root.iterdir(), key=lambda path: path.name)
        if item.is_dir() and re.match(r"^\d{4}$", item.name)
    ]


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Preprocess FC_ROMS title-screen covers.")
    parser.add_argument("--repo-root", default=".", help="FC_ROMS repository root.")
    parser.add_argument("--source", choices=["libretro", "no-intro-git"], default="libretro", help="Title image source.")
    parser.add_argument("--source-git", default="", help="Local no-intro-pictures git clone path.")
    parser.add_argument("--nes-index-html", default="", help="Local libretro NES Named_Titles HTML index.")
    parser.add_argument("--fds-index-html", default="", help="Local libretro FDS Named_Titles HTML index.")
    parser.add_argument("--cache-dir", default="", help="Downloaded source image cache directory.")
    parser.add_argument("--report", default="cover-source.v1.json", help="Cover source report path.")
    parser.add_argument("--limit", type=int, default=0, help="Limit processed games for testing.")
    parser.add_argument("--workers", type=int, default=8, help="Concurrent download workers.")
    parser.add_argument("--dry-run", action="store_true", help="Only match sources; do not write cover files.")
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()
    cache_dir = Path(args.cache_dir) if args.cache_dir else Path(tempfile.gettempdir()) / "fc_roms_title_cover_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    if args.source == "libretro":
        source_name = LIBRETRO_SOURCE_NAME
        nes_index_html = Path(args.nes_index_html) if args.nes_index_html else None
        fds_index_html = Path(args.fds_index_html) if args.fds_index_html else None
        exact, by_base = build_libretro_index(cache_dir, nes_index_html, fds_index_html)
    else:
        if not args.source_git:
            print("--source no-intro-git 需要 --source-git", file=sys.stderr)
            return 2
        source_git = Path(args.source_git).resolve()
        if not (source_git / ".git").exists():
            print(f"源仓库不是有效 Git 仓库：{source_git}", file=sys.stderr)
            return 2
        source_name = SOURCE_REPOSITORY
        exact, by_base = build_source_index(source_git)
    game_dirs = discover_game_dirs(root)
    if args.limit:
        game_dirs = game_dirs[: args.limit]

    entries: list[dict] = []
    matched = 0
    written = 0
    skipped = 0
    failed = 0

    for game_dir in game_dirs:
        rom, source = match_game(game_dir, exact, by_base)
        cover = game_dir / "cover.webp"
        entry = {
            "id": game_dir.name,
            "rom": rom.name if rom else "",
            "cover": cover.relative_to(root).as_posix(),
            "status": "unmatched",
            "sourceRepository": source_name,
            "sourceRef": SOURCE_REF,
            "sourcePath": "",
            "sourceUrl": "",
            "sourceImageSha256": "",
            "coverKind": "title_screen",
            "confidence": 0.0,
        }
        if not source:
            skipped += 1
            entries.append(entry)
            continue

        matched += 1
        entry["status"] = "matched"
        entry["sourcePath"] = source.path
        entry["sourceUrl"] = source_url(source)
        entry["confidence"] = 0.92 if normalize(base_title(source.stem)) == normalize(base_title(rom.stem if rom else "")) else 0.75
        if not args.dry_run:
            try:
                data = fetch_source(source, cache_dir)
                entry["sourceImageSha256"] = sha256_bytes(data)
                normalize_cover(data, cover)
                written += 1
                entry["status"] = "written"
            except Exception as exc:  # noqa: BLE001 - report and continue batch.
                failed += 1
                entry["status"] = "failed"
                entry["error"] = str(exc)
        entries.append(entry)

    if not args.dry_run:
        pending = [
            (entry, SourceImage("nes", entry["sourcePath"], Path(entry["sourcePath"]).stem, entry["sourceUrl"]))
            for entry in entries
            if entry["status"] == "matched"
        ]
        total = len(pending)
        done = 0

        def process_one(item: tuple[dict, SourceImage]) -> None:
            entry, source = item
            cover = root / entry["cover"]
            data = fetch_source(source, cache_dir)
            entry["sourceImageSha256"] = sha256_bytes(data)
            normalize_cover(data, cover)
            return None

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(process_one, item): item[0] for item in pending}
            for future in as_completed(futures):
                entry = futures[future]
                done += 1
                try:
                    future.result()
                    entry["status"] = "written"
                    written += 1
                except Exception as exc:  # noqa: BLE001 - batch report.
                    entry["status"] = "failed"
                    entry["error"] = str(exc)
                    failed += 1
                if done == total or done % 25 == 0:
                    print(f"进度：{done}/{total}")

    report = {
        "schemaVersion": "1.0",
        "coverKind": "title_screen",
        "source": {
            "repository": source_name,
            "ref": SOURCE_REF if args.source == "no-intro-git" else "",
            "nesTitleCount": len(exact["nes"]),
            "fdsTitleCount": len(exact["fds"]),
        },
        "gameCount": len(game_dirs),
        "matchedCount": matched,
        "writtenCount": written,
        "unmatchedCount": skipped,
        "failedCount": failed,
        "entries": entries,
    }
    write_json(root / args.report, report)
    print(
        f"封面预处理：{len(game_dirs)} 个游戏，匹配 {matched}，写入 {written}，未匹配 {skipped}，失败 {failed}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
