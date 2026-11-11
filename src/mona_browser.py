#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MONA 浏览器侧：攒阿里云验证码参数 / 浏览器内直接注册
  python3 mona_browser.py harvest --count 10      # 产出 captcha_pool.jsonl
  python3 mona_browser.py register --count 1      # 浏览器内完整注册一轮（调试用）

关键点（实测）：
  * 必须用完整版 chromium（channel="chromium"），无头 shell 会被阿里云识别 -> 点了没反应
  * 点验证码前要有真实鼠标移动/触摸，否则 cloudauth 设备指纹判失败
  * 通过后 POST /api/auth/email-code 会带 X-MONA-Captcha-Verify-Param
"""
import argparse, asyncio, json, os, random, re, string, time
import requests

SITE = "https://monastudiopro.com"
POOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captcha_pool.jsonl")

UA = ("Mozilla/5.0 (Linux; Android 14; V2405A) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")

STEALTH = """
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
Object.defineProperty(navigator,'languages',{get:()=>['zh-CN','zh','en']});
Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
window.chrome = window.chrome || {runtime:{}, app:{}};
const _q = navigator.permissions && navigator.permissions.query;
if (_q) navigator.permissions.query = p => p && p.name==='notifications' ? Promise.resolve({state:'denied'}) : _q(p);
const _gp = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(p){
  if (p===37445) return 'Qualcomm';
  if (p===37446) return 'Adreno (TM) 740';
  return _gp.call(this,p);
};
"""


def rnd_email():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=12)) + "@mailinator.com"


async def new_page(p, headless=True):
    b = await p.chromium.launch(channel="chromium", headless=headless,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
              "--disable-blink-features=AutomationControlled"])
    ctx = await b.new_context(locale="zh-CN", user_agent=UA,
        viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
    await ctx.add_init_script(STEALTH)
    return b, ctx, await ctx.new_page()


async def goto_register(pg):
    await pg.goto(f"{SITE}/login", wait_until="domcontentloaded", timeout=45000)
    await pg.wait_for_timeout(3500)
    for name in ("仅使用必要项", "全部接受"):
        try:
            await pg.get_by_role("button", name=name).click(timeout=2500)
            break
        except Exception:
            continue
    try:
        await pg.get_by_role("button", name="注册", exact=True).click(timeout=5000)
    except Exception:
        await pg.get_by_text("注册", exact=True).first.click(timeout=5000)
    await pg.wait_for_timeout(600)


async def solve_captcha(pg, timeout=70):
    """等组件真正渲染完成再点；点太早会把这次交互作废。返回 True/False"""
    ready = False
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ready = await pg.evaluate("""()=>{
                const w = document.getElementById('aliyunCaptcha-window-embed');
                const t = document.getElementById('aliyunCaptcha-checkbox-text');
                const l = document.getElementById('aliyunCaptcha-loading');
                if (!w || !t) return false;
                if (l && (l.offsetWidth || l.offsetHeight)) return false;   // 还在骨架屏
                return (t.innerText || '').includes('不是机器人');
            }""")
        except Exception:
            ready = False
        if ready:
            break
        await pg.wait_for_timeout(800)
    if not ready:
        return False

    await pg.wait_for_timeout(2500)          # 渲染后再稳一下
    try:
        bb = await pg.locator("#aliyunCaptcha-window-embed").bounding_box()
    except Exception:
        return False
    if not bb:
        return False

    x, y = bb["x"] + 22, bb["y"] + bb["height"] / 2
    for attempt in range(3):                 # 没点上就再补一次
        await pg.mouse.move(x - 90, y, steps=10)
        await pg.wait_for_timeout(250)
        await pg.mouse.move(x, y, steps=14)
        await pg.wait_for_timeout(180)
        await pg.mouse.click(x, y)
        for _ in range(12):
            await pg.wait_for_timeout(800)
            try:
                gone = await pg.evaluate("() => !document.querySelector('#aliyunCaptcha-window-embed')")
            except Exception:
                gone = False
            if gone:
                return True
        await pg.wait_for_timeout(1200)
    return False


async def harvest(count, delay):
    async with __import__("playwright.async_api", fromlist=["async_playwright"]).async_playwright() as p:
        got = 0
        b, ctx, pg = await new_page(p)   # 复用同一个浏览器，省掉每次启动开销

        async def _abort(route):
            await route.abort()
        await pg.route("**/api/auth/email-code", _abort)   # 拦掉站点发码：只留参数不消耗

        for i in range(count):
            if pg.is_closed():
                b, ctx, pg = await new_page(p)
            cap = {}
            pg.on("request", lambda r: cap.update({"h": dict(r.headers)}) if "/api/auth/email-code" in r.url else None)
            try:
                await goto_register(pg)
                await pg.locator("input[autocomplete=email]").fill(rnd_email())
                await pg.locator("button.code-action").click()
                await pg.wait_for_timeout(2000)
                ok = await solve_captcha(pg)
                await pg.wait_for_timeout(3500)
                param = (cap.get("h") or {}).get("x-mona-captcha-verify-param")
                if param:
                    with open(POOL, "a") as f:
                        f.write(json.dumps({"param": param, "ts": int(time.time()), "ttl": 900}) + "\n")
                    got += 1
                    print(f"[+] {i+1}/{count} 参数已入库 len={len(param)}")
                else:
                    print(f"[-] {i+1} 未拿到参数 (solved={ok})")
            except Exception as e:
                print(f"[-] {i+1} {type(e).__name__}: {e}")
            await asyncio.sleep(delay)
        print(f"\n入库 {got} 个 -> {POOL}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["harvest", "register"])
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--delay", type=float, default=6)
    ap.add_argument("--headful", action="store_true", help="xvfb 下跑有头模式，通过率更高")
    a = ap.parse_args()
    if a.mode == "harvest":
        asyncio.run(harvest(a.count, a.delay))
    else:
        print("用 mona_reg.py 走 HTTP 注册更快；这里保留浏览器入口便于排错")


if __name__ == "__main__":
    main()
