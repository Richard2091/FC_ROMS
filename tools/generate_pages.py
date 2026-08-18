#!/usr/bin/env python3
"""Generate a static GitHub Pages site for FC_ROMS."""

from __future__ import annotations

import argparse
import html
import json
import posixpath
import shutil
from pathlib import Path


JSON_FILES = ["manifest.v1.json", "search-index.v1.json", "zh-metadata.v1.json", "cover-source.v1.json"]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def clean_output(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def copy_if_exists(source: Path, target: Path) -> None:
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def copy_json_files(root: Path, output: Path) -> None:
    for name in JSON_FILES:
        copy_if_exists(root / name, output / name)


def copy_game_assets(root: Path, output: Path, manifest: dict) -> None:
    for game in manifest.get("games", []):
        assets = game.get("assets", {})
        paths: list[str] = []
        cover = assets.get("cover")
        if cover:
            paths.append(cover)
        paths.extend(assets.get("screenshots", []) or [])
        paths.extend(assets.get("logos", []) or [])
        for rel_path in paths:
            copy_if_exists(root / rel_path, output / rel_path)


def title_text(game: dict) -> str:
    title = game.get("title") or {}
    return game.get("displayTitle") or title.get("zh") or title.get("en") or title.get("ja") or game.get("id", "")


def detail_href(game: dict) -> str:
    rom_dir = game.get("romDir") or ""
    return f"{rom_dir.rstrip('/')}/" if rom_dir else "#"


def card_html(game: dict) -> str:
    title = title_text(game)
    title_meta = game.get("title") or {}
    cover = (game.get("assets") or {}).get("cover")
    image = (
        f'<img src="{html.escape(cover)}" alt="{html.escape(title)}" loading="lazy" />'
        if cover
        else '<div class="placeholder">无图</div>'
    )
    search_text = " ".join(
        [
            title,
            title_meta.get("zh", ""),
            title_meta.get("en", ""),
            title_meta.get("ja", ""),
            game.get("id", ""),
        ]
    )
    return f"""
      <article class="game-card" data-search="{html.escape(search_text.casefold())}">
        <a class="cover" href="{html.escape(detail_href(game))}" aria-label="{html.escape(title)}">
          {image}
        </a>
        <div class="info">
          <strong><a href="{html.escape(detail_href(game))}">{html.escape(title)}</a></strong>
          <span>{html.escape(game.get('id', ''))} · {html.escape(game.get('releaseDate') or '日期未知')}</span>
        </div>
      </article>
    """


def rel_url(from_dir: str, target: str) -> str:
    if not target:
        return ""
    value = posixpath.relpath(target, from_dir or ".")
    return "" if value == "." else value


def asset_url_for_game(game: dict, asset_path: str) -> str:
    return rel_url(game.get("romDir", ""), asset_path)


def format_bytes(value: int | None) -> str:
    if not value:
        return "大小未知"
    units = ["B", "KB", "MB", "GB"]
    size = float(value)
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"


def metadata_row(label: str, value: str) -> str:
    return f"<div><span>{html.escape(label)}</span><strong>{html.escape(value or '未填写')}</strong></div>"


def rom_list_html(game: dict) -> str:
    roms = game.get("roms") or []
    if not roms:
        return '<p class="muted">这个条目暂时没有可用 ROM 文件。</p>'

    items = []
    for rom in roms:
        filename = rom.get("filename") or rom.get("path") or "ROM 文件"
        raw_url = rom.get("url") or ""
        size = format_bytes((rom.get("hash") or {}).get("sizeBytes"))
        crc32 = (rom.get("hash") or {}).get("crc32") or ""
        mapper = ((rom.get("ines") or {}).get("mapper"))
        mapper_text = f"Mapper {mapper}" if mapper is not None else rom.get("format", "")
        action = (
            f'<a class="button primary" href="{html.escape(raw_url)}" target="_blank" rel="noreferrer">打开文件</a>'
            if raw_url
            else '<span class="button disabled">暂无链接</span>'
        )
        items.append(
            f"""
            <article class="rom-card">
              <div>
                <strong>{html.escape(filename)}</strong>
                <span>{html.escape(size)} · {html.escape(mapper_text)}</span>
                <code>CRC32 {html.escape(crc32 or "未知")}</code>
              </div>
              {action}
            </article>
            """
        )
    return "\n".join(items)


def image_gallery_html(game: dict) -> str:
    assets = game.get("assets") or {}
    image_paths = [
        *(assets.get("logos") or []),
        *(assets.get("screenshots") or []),
    ]
    image_paths = [path for path in image_paths if path and path != assets.get("cover")]
    if not image_paths:
        return '<p class="muted">暂无额外截图。</p>'

    return "\n".join(
        f"""
        <a class="shot" href="{html.escape(asset_url_for_game(game, image_path))}">
          <img src="{html.escape(asset_url_for_game(game, image_path))}" alt="{html.escape(title_text(game))} 截图" loading="lazy" />
        </a>
        """
        for image_path in image_paths
    )


def game_page_html(game: dict) -> str:
    title = title_text(game)
    title_meta = game.get("title") or {}
    assets = game.get("assets") or {}
    cover = assets.get("cover") or ""
    cover_html = (
        f'<img src="{html.escape(asset_url_for_game(game, cover))}" alt="{html.escape(title)} 封面" />'
        if cover
        else '<div class="placeholder">无图</div>'
    )
    back_href = rel_url(game.get("romDir", ""), "index.html") or "../../"
    manifest_href = rel_url(game.get("romDir", ""), "manifest.v1.json")
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{html.escape(title)} · 红白机游戏库</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f5f7f8;
        --panel: #ffffff;
        --text: #162022;
        --muted: #647174;
        --line: #d8e0e2;
        --accent: #1f7a5c;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        background: var(--bg);
        color: var(--text);
        font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
        line-height: 1.55;
      }}
      a {{ color: inherit; text-decoration: none; }}
      .wrap {{
        max-width: 1120px;
        margin: 0 auto;
        padding: 20px 16px 42px;
      }}
      .topbar {{
        display: flex;
        flex-wrap: wrap;
        justify-content: space-between;
        align-items: center;
        gap: 10px;
        margin-bottom: 18px;
      }}
      .button {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 38px;
        padding: 0 14px;
        border: 1px solid var(--line);
        border-radius: 7px;
        background: var(--panel);
        font-weight: 700;
        font-size: 14px;
      }}
      .button.primary {{
        border-color: rgba(31, 122, 92, 0.55);
        color: #0d5c42;
      }}
      .button.disabled {{
        color: var(--muted);
        opacity: 0.66;
      }}
      .hero {{
        display: grid;
        grid-template-columns: minmax(240px, 360px) minmax(0, 1fr);
        gap: 22px;
        align-items: start;
      }}
      .cover {{
        overflow: hidden;
        border: 1px solid var(--line);
        border-radius: 10px;
        background: #050505;
        aspect-ratio: 1 / 1;
      }}
      .cover img,
      .placeholder {{
        display: block;
        width: 100%;
        height: 100%;
        object-fit: contain;
        image-rendering: pixelated;
      }}
      .placeholder {{
        display: grid;
        place-items: center;
        color: #9aa4a6;
      }}
      h1 {{
        margin: 0 0 8px;
        font-size: 30px;
        line-height: 1.2;
      }}
      .subtitle {{
        color: var(--muted);
        margin: 0 0 16px;
      }}
      .meta {{
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
      }}
      .meta div,
      .panel,
      .rom-card {{
        border: 1px solid var(--line);
        border-radius: 9px;
        background: var(--panel);
      }}
      .meta div {{
        display: grid;
        gap: 4px;
        padding: 12px;
      }}
      .meta span,
      .muted {{
        color: var(--muted);
      }}
      .meta strong {{
        font-size: 15px;
      }}
      .section {{
        margin-top: 22px;
      }}
      .section h2 {{
        margin: 0 0 12px;
        font-size: 20px;
      }}
      .rom-list {{
        display: grid;
        gap: 10px;
      }}
      .rom-card {{
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 14px;
        align-items: center;
        padding: 14px;
      }}
      .rom-card div {{
        display: grid;
        gap: 5px;
        min-width: 0;
      }}
      .rom-card strong,
      .rom-card span,
      .rom-card code {{
        overflow-wrap: anywhere;
      }}
      .rom-card span {{
        color: var(--muted);
        font-size: 13px;
      }}
      code {{
        width: fit-content;
        padding: 2px 6px;
        border-radius: 5px;
        background: #eef3f0;
        color: #285242;
      }}
      .gallery {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 12px;
      }}
      .shot {{
        display: block;
        overflow: hidden;
        border: 1px solid var(--line);
        border-radius: 9px;
        background: #050505;
      }}
      .shot img {{
        display: block;
        width: 100%;
        aspect-ratio: 4 / 3;
        object-fit: contain;
        image-rendering: pixelated;
      }}
      @media (max-width: 760px) {{
        .hero {{
          grid-template-columns: 1fr;
        }}
        .meta {{
          grid-template-columns: 1fr;
        }}
        .rom-card {{
          grid-template-columns: 1fr;
        }}
      }}
    </style>
  </head>
  <body>
    <main class="wrap">
      <nav class="topbar">
        <a class="button" href="{html.escape(back_href)}">返回首页</a>
        <a class="button" href="{html.escape(manifest_href)}">查看清单</a>
      </nav>
      <section class="hero">
        <div class="cover">{cover_html}</div>
        <div>
          <h1>{html.escape(title)}</h1>
          <p class="subtitle">{html.escape(title_meta.get("ja") or "")} · {html.escape(title_meta.get("en") or "")}</p>
          <div class="meta">
            {metadata_row("编号", game.get("id", ""))}
            {metadata_row("平台", game.get("platform", ""))}
            {metadata_row("发售日期", game.get("releaseDate") or "日期未知")}
            {metadata_row("ROM 数量", str(game.get("romCount", 0)))}
          </div>
        </div>
      </section>
      <section class="section">
        <h2>ROM 文件</h2>
        <div class="rom-list">{rom_list_html(game)}</div>
      </section>
      <section class="section">
        <h2>截图</h2>
        <div class="gallery">{image_gallery_html(game)}</div>
      </section>
    </main>
  </body>
</html>
"""


def write_game_pages(output: Path, manifest: dict) -> None:
    for game in manifest.get("games", []):
        rom_dir = game.get("romDir") or ""
        if not rom_dir:
            continue
        page_path = output / rom_dir / "index.html"
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(game_page_html(game), encoding="utf-8", newline="\n")


def build_html(manifest: dict) -> str:
    games = manifest.get("games", [])
    cards = "\n".join(card_html(game) for game in games)
    game_count = manifest.get("gameCount", len(games))
    rom_count = manifest.get("romCount", 0)
    generated_at = manifest.get("generatedAt", "")
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>红白机游戏库</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f5f7f8;
        --panel: #ffffff;
        --text: #162022;
        --muted: #647174;
        --line: #d8e0e2;
        --accent: #1f7a5c;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        background: var(--bg);
        color: var(--text);
        font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
      }}
      header {{
        position: sticky;
        top: 0;
        z-index: 2;
        background: rgba(245, 247, 248, 0.94);
        border-bottom: 1px solid var(--line);
        backdrop-filter: blur(10px);
      }}
      .bar {{
        display: grid;
        gap: 12px;
        max-width: 1280px;
        margin: 0 auto;
        padding: 16px;
      }}
      h1 {{
        margin: 0;
        font-size: 22px;
        line-height: 1.25;
      }}
      .summary {{
        display: flex;
        flex-wrap: wrap;
        gap: 10px 18px;
        color: var(--muted);
        font-size: 13px;
      }}
      input {{
        width: 100%;
        height: 42px;
        border: 1px solid var(--line);
        border-radius: 6px;
        padding: 0 12px;
        background: var(--panel);
        color: var(--text);
        font-size: 15px;
      }}
      main {{
        max-width: 1280px;
        margin: 0 auto;
        padding: 16px;
      }}
      .grid {{
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
        gap: 14px;
      }}
      .game-card {{
        overflow: hidden;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: var(--panel);
      }}
      .cover {{
        display: block;
        aspect-ratio: 1 / 1;
        background: #050505;
      }}
      .cover img,
      .placeholder {{
        display: block;
        width: 100%;
        height: 100%;
        object-fit: contain;
        image-rendering: pixelated;
      }}
      .placeholder {{
        display: grid;
        place-items: center;
        color: #9aa4a6;
        font-size: 14px;
      }}
      .info {{
        display: grid;
        gap: 4px;
        padding: 10px;
        min-height: 72px;
      }}
      .info strong {{
        font-size: 14px;
        line-height: 1.35;
      }}
      .info span {{
        color: var(--muted);
        font-size: 12px;
      }}
      .hidden {{ display: none; }}
      footer {{
        max-width: 1280px;
        margin: 0 auto;
        padding: 18px 16px 28px;
        color: var(--muted);
        font-size: 12px;
      }}
      a {{ color: inherit; text-decoration: none; }}
    </style>
  </head>
  <body>
    <header>
      <div class="bar">
        <h1>红白机游戏库</h1>
        <div class="summary">
          <span>游戏 {game_count}</span>
          <span>ROM {rom_count}</span>
          <span>生成时间 {html.escape(generated_at)}</span>
          <span><a href="manifest.v1.json">游戏清单</a></span>
          <span><a href="search-index.v1.json">搜索索引</a></span>
        </div>
        <input id="search" type="search" placeholder="搜索中文名、英文名、日文名或编号" autocomplete="off" />
      </div>
    </header>
    <main>
      <section id="grid" class="grid" aria-label="游戏列表">
        {cards}
      </section>
    </main>
    <footer>封面优先使用标题屏截图，Pages 只发布图片、详情页和清单文件；ROM 文件链接指向仓库 raw 地址。</footer>
    <script>
      const search = document.getElementById('search');
      const cards = Array.from(document.querySelectorAll('.game-card'));
      search.addEventListener('input', () => {{
        const keyword = search.value.trim().toLocaleLowerCase();
        for (const card of cards) {{
          card.classList.toggle('hidden', keyword && !card.dataset.search.includes(keyword));
        }}
      }});
    </script>
  </body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate FC_ROMS GitHub Pages static site.")
    parser.add_argument("--repo-root", default=".", help="Repository root path.")
    parser.add_argument("--output", default="public", help="Output directory.")
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()
    output = (root / args.output).resolve()
    manifest = read_json(root / "manifest.v1.json")
    clean_output(output)
    copy_json_files(root, output)
    copy_game_assets(root, output, manifest)
    (output / "index.html").write_text(build_html(manifest), encoding="utf-8", newline="\n")
    write_game_pages(output, manifest)
    print(f"生成 Pages：{output}，游戏 {manifest.get('gameCount', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
