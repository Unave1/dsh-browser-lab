#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动一个能用的 Chromium 上下文。

这台机器的事实：
  * headless shell 会被识别 -> 一律用 channel="chromium" 的完整版
  * 无 GUI -> 要有头就必须 xvfb-run
  * 内存紧（2 GB 可用）-> 只开一个实例，别并行
"""
import os

STEALTH = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || {runtime: {}, app: {}};
const _q = navigator.permissions && navigator.permissions.query;
if (_q) navigator.permissions.query = p =>
    p && p.name === 'notifications' ? Promise.resolve({state: 'denied'}) : _q(p);
const _gp = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function (p) {
    if (p === 37445) return 'Qualcomm';
    if (p === 37446) return 'Adreno (TM) 740';
    return _gp.call(this, p);
};
"""

UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
UA_MOBILE = ("Mozilla/5.0 (Linux; Android 14; V2405A) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")

ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-blink-features=AutomationControlled",
]


async def new_page(p, headless=True, profile=None, mobile=True,
                   viewport=(412, 915), ua=None, stealth=True, locale="zh-CN"):
    """返回 (owner, page)。owner.close() 收尾；profile 模式下是 Context，否则是 Browser。"""
    ua = ua or (UA_MOBILE if mobile else UA_DESKTOP)

    if profile:
        os.makedirs(profile, exist_ok=True)
        ctx = await p.chromium.launch_persistent_context(
            profile, channel="chromium", headless=headless, args=ARGS,
            locale=locale, user_agent=ua,
            viewport={"width": viewport[0], "height": viewport[1]},
            is_mobile=mobile, has_touch=mobile,
        )
        if stealth:
            await ctx.add_init_script(STEALTH)
        pg = ctx.pages[0] if ctx.pages else await ctx.new_page()
        return ctx, pg

    b = await p.chromium.launch(channel="chromium", headless=headless, args=ARGS)
    ctx = await b.new_context(
        locale=locale, user_agent=ua,
        viewport={"width": viewport[0], "height": viewport[1]},
        is_mobile=mobile, has_touch=mobile,
    )
    if stealth:
        await ctx.add_init_script(STEALTH)
    return b, await ctx.new_page()


async def settle(pg, ms=1500):
    """等网络静下来。SPA 里 domcontentloaded 之后还有一堆 XHR。"""
    try:
        await pg.wait_for_load_state("networkidle", timeout=ms * 4)
    except Exception:
        pass
    await pg.wait_for_timeout(ms)
