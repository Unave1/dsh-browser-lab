"""拦截请求：看接口、改参数、或者直接掐掉。

这个手法最有用的一条：把某个请求 abort 掉，
参数照样能从 request.headers 里拿到，但副作用不发生。
"""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from launcher import new_page

WATCH = "/api/"
KILL  = "email-code"      # 想掐掉的路径片段，留空就只观察

async def main(url):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        owner, pg = await new_page(p, headless=False)

        async def route(r):
            await r.abort()

        def on_req(r):
            if WATCH in r.url:
                print("→", r.method, r.url[:110])
                for k, v in r.headers.items():
                    if k.lower().startswith("x-"):
                        print("    ", k, "=", v[:70])
                if r.post_data:
                    print("     body:", r.post_data[:180])

        pg.on("request", on_req)
        if KILL:
            await pg.route(f"**/*{KILL}*", route)

        await pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        await pg.wait_for_timeout(12000)
        await owner.close()

asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "https://example.com"))
