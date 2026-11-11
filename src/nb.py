#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nb —— 这台机器上的浏览器命令行。curl 拿不到的东西用它。

  python3 src/nb.py shot  https://example.com -o out.png --full
  python3 src/nb.py text  https://example.com
  python3 src/nb.py html  https://example.com -o out.html
  python3 src/nb.py pdf   https://example.com -o out.pdf
  python3 src/nb.py cookies https://example.com -o cookies.json

有头模式（推荐，反检测好得多）:
  xvfb-run -a python3 src/nb.py shot https://example.com --headful

登录态持久化（登录一次管很久）:
  xvfb-run -a python3 src/nb.py text https://site.com --profile ./profile --headful
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from launcher import new_page, settle  # noqa: E402


def build():
    ap = argparse.ArgumentParser(prog="nb", description="浏览器命令行")
    ap.add_argument("action", choices=["shot", "text", "html", "pdf", "cookies"])
    ap.add_argument("url")
    ap.add_argument("-o", "--out", default="")
    ap.add_argument("--headful", action="store_true", help="xvfb 下必须有头")
    ap.add_argument("--profile", default="", help="持久化目录，保留登录态")
    ap.add_argument("--wait", type=int, default=2000, help="页面稳定后额外等待 ms")
    ap.add_argument("--full", action="store_true", help="整页截图")
    ap.add_argument("--sel", default="", help="只截这个选择器 / 只取这个元素的文本")
    ap.add_argument("--desktop", action="store_true", help="桌面 UA（默认移动端）")
    ap.add_argument("--timeout", type=int, default=45000)
    return ap


async def run(a):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        owner, pg = await new_page(
            p, headless=not a.headful, profile=a.profile or None,
            mobile=not a.desktop,
        )
        try:
            await pg.goto(a.url, wait_until="domcontentloaded", timeout=a.timeout)
            await settle(pg, a.wait)

            if a.action == "text":
                txt = (await pg.locator(a.sel).inner_text()) if a.sel else \
                      await pg.evaluate("() => document.body.innerText")
                print(txt)
            elif a.action == "html":
                html = (await pg.locator(a.sel).inner_html()) if a.sel else await pg.content()
                if a.out:
                    Path(a.out).write_text(html, encoding="utf-8")
                    print(f"-> {a.out}  {len(html)} 字节")
                else:
                    print(html)
            elif a.action == "shot":
                out = a.out or "shot.png"
                if a.sel:
                    await pg.locator(a.sel).screenshot(path=out)
                else:
                    await pg.screenshot(path=out, full_page=a.full)
                print(f"-> {out}")
            elif a.action == "pdf":
                out = a.out or "page.pdf"
                await pg.pdf(path=out, format="A4", print_background=True)
                print(f"-> {out}")
            elif a.action == "cookies":
                out = a.out or "cookies.json"
                Path(out).write_text(json.dumps(await pg.context.cookies(), indent=1),
                                     encoding="utf-8")
                print(f"-> {out}  {len(await pg.context.cookies())} 条")
        finally:
            try:
                await owner.close()
            except Exception:
                pass


def main():
    a = build().parse_args()
    if a.headful and not (Path("/tmp/.X11-unix").exists() or __import__("os").environ.get("DISPLAY")):
        print("! 没有 X 显示。用 `xvfb-run -a` 包一层，或者去掉 --headful", file=sys.stderr)
    asyncio.run(run(a))


if __name__ == "__main__":
    main()
