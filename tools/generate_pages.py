#!/usr/bin/env python3
"""Generate a static GitHub Pages site for FC_ROMS."""

from __future__ import annotations

import argparse
import html
import json
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
        <a class="cover" href="{html.escape(game.get('romDir', ''))}/" aria-label="{html.escape(title)}">
          {image}
        </a>
        <div class="info">
          <strong>{html.escape(title)}</strong>
          <span>{html.escape(game.get('id', ''))} · {html.escape(game.get('releaseDate') or '日期未知')}</span>
        </div>
      </article>
    """


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
    <footer>封面优先使用标题屏截图，页面只发布图片和清单文件。</footer>
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
    print(f"生成 Pages：{output}，游戏 {manifest.get('gameCount', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
