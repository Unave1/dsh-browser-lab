"""登录一次，之后一直复用这个目录里的登录态。

第一次：xvfb-run -a python3 examples/login_keep.py --profile ./profile
（弹出页面后手动登录，回车结束）
之后：  python3 examples/login_keep.py --profile ./profile --check
"""
import asyncio, argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from launcher import new_page, settle

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="./profile")
    ap.add_argument("--url", default="https://example.com")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        ctx, pg = await new_page(p, headless=a.check, profile=a.profile)
        await pg.goto(a.url, wait_until="domcontentloaded", timeout=45000)
        await settle(pg, 1500)
        if a.check:
            print("当前 URL:", pg.url)
            print((await pg.evaluate("()=>document.body.innerText"))[:300])
        else:
            input("在打开的窗口里登录，完事回车...")
            print("已保存到", a.profile)
        await ctx.close()

asyncio.run(main())
