#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MONA 注册流水线 v2（预建邮箱 + 浏览器取码 + HTTP 落号）

相对 v1 的改动：
  * 邮箱先并行建好一池（mail.tm 建箱可能 429 卡 40s+，这一步挪出关键路径）
  * 每个号走完立刻打时间戳日志，方便看卡在哪一步
  * 事件监听用完就摘，避免多轮累积
  * 一个验证码参数只服务一个号，不复用（复用会掉到 30% 通过率）

用法:
  python3 mona_pipe.py --count 10 --out accounts_batch4.csv
"""
import argparse, asyncio, csv, os, random, re, sys, time
import concurrent.futures as cf
import requests

import mona_browser as MB
import mona_reg as MR

OUTDIR = os.path.dirname(os.path.abspath(__file__))


def ts():
    return time.strftime("%H:%M:%S")


def log(*a):
    print(f"[{ts()}]", *a, flush=True)


class TLol:
    """tempmail.lol：无 key、建箱即时，作为 mail.tm 被限流时的备胎"""
    API = "https://api.tempmail.lol"
    UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 14; V2405A) Chrome/131.0.0.0 Mobile"}

    def create(self):
        r = requests.post(self.API + "/v2/inbox/create", timeout=25, headers=self.UA)
        j = r.json()
        return j["address"], j["token"]

    def wait_code(self, token, timeout=200, poll=4):
        end = time.time() + timeout
        seen = set()
        while time.time() < end:
            try:
                r = requests.get(self.API + "/v2/inbox",
                                 params={"token": token}, timeout=20, headers=self.UA)
                if r.status_code == 200:
                    for m in r.json().get("emails") or []:
                        k = m.get("id") or m.get("date") or m.get("subject")
                        if k in seen:
                            continue
                        seen.add(k)
                        body = " ".join([m.get("subject") or "", m.get("body") or "",
                                         m.get("html") or ""])
                        mm = re.search(r"(?<!\d)(\d{6})(?!\d)", body)
                        if mm:
                            return mm.group(1)
            except Exception:
                pass
            time.sleep(poll)
        raise TimeoutError("等验证码超时")


def new_mailbox(_i=0):
    for a in range(2):
        try:
            m = MR.MailTM()
            return (*m.create(), m)
        except Exception:
            break
    for a in range(3):
        try:
            m = TLol()
            return (*m.create(), m)
        except Exception:
            time.sleep(3 + 3 * a)
    return None


async def one_account(pg, email, mail_tok, mail, timeout=240, shot=False):
    password = MR.gen_password()
    cap, resp = {}, {}

    def on_req(r):
        if "/api/auth/email-code" in r.url:
            cap["h"] = dict(r.headers)

    def on_resp(r):
        if "/api/auth/email-code" in r.url:
            resp["status"] = r.status

    pg.on("request", on_req)
    pg.on("response", on_resp)
    try:
        t0 = time.time()
        await MB.goto_register(pg)
        log(f"    页面就绪 {time.time()-t0:.1f}s  {email}")
        await pg.locator("input[autocomplete=email]").fill(email)
        await pg.locator("button.code-action").click()
        t1 = time.time()
        solved = await MB.solve_captcha(pg)
        await pg.wait_for_timeout(2500)
        log(f"    验证码 solved={solved} {time.time()-t1:.1f}s status={resp.get('status')}")

        if resp.get("status") not in (200, 201):
            if shot:
                try:
                    fn = f"/tmp/capfail_{int(time.time())}.png"
                    await pg.screenshot(path=fn)
                    log(f"    失败截图 {fn}")
                except Exception:
                    pass
            raise RuntimeError(f"email-code 未发出 solved={solved} status={resp.get('status')}")
        param = (cap.get("h") or {}).get("x-mona-captcha-verify-param")
        if not param:
            raise RuntimeError("没抓到 captcha 参数")

        t2 = time.time()
        code = await asyncio.get_running_loop().run_in_executor(None, mail.wait_code, mail_tok)
        log(f"    收到验证码 {code} {time.time()-t2:.1f}s")

        s = MR.new_session(None)
        body = {"email": email, "code": code, "password": password,
                "username": MR.gen_username(), "rememberMe": True}
        st, js = MR.api(s, "/auth/register", body)
        if js.get("code") not in (0, 200):
            raise RuntimeError(f"register: {js.get('reasonCode')} {js.get('message')}")
        d = js.get("data") or {}
        return {"email": email, "password": password, "mail_token": mail_tok,
                "token": d.get("token") or "",
                "user_id": (d.get("userInfo") or {}).get("id", ""),
                "status": "ok", "ts": int(time.time())}
    finally:
        try:
            pg.remove_listener("request", on_req)
            pg.remove_listener("response", on_resp)
        except Exception:
            pass


async def run(count, out, retries=1, headless=True, recycle=4):
    newfile = not os.path.exists(out)
    fh = open(out, "a", newline="", encoding="utf-8")
    fields = ["email", "password", "mail_token", "token", "user_id", "status", "ts"]
    w = csv.DictWriter(fh, fieldnames=fields)
    if newfile:
        w.writeheader()
        fh.flush()

    log(f"预建 {count+3} 个邮箱...")
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        boxes = [b for b in ex.map(new_mailbox, range(count + 3)) if b]
    log(f"邮箱就绪 {len(boxes)} 个")
    if not boxes:
        log("邮箱全建失败，退出")
        return 0, 0
    count = min(count, len(boxes))

    ok = fail = 0
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b, ctx, pg = await MB.new_page(p, headless=headless)
        for i in range(count):
            if i and i % recycle == 0:
                try:
                    await b.close()
                except Exception:
                    pass
                b, ctx, pg = await MB.new_page(p, headless=headless)
                log(f"    浏览器已换新 ({i}/{count})")
            if i >= len(boxes) or i >= count:
                break
            email, mail_tok, mail = boxes[i]
            done = False
            for a in range(retries + 1):
                if pg.is_closed():
                    b, ctx, pg = await MB.new_page(p, headless=headless)
                try:
                    rec = await one_account(pg, email, mail_tok, mail, shot=True)
                except Exception as e:
                    log(f"[-] #{i+1}.{a+1} {type(e).__name__}: {e}")
                    try:
                        await pg.goto("about:blank")
                    except Exception:
                        pass
                    continue
                w.writerow(rec)
                fh.flush()
                ok += 1
                done = True
                log(f"[+] {rec['email']}  {rec['password']}  uid={rec['user_id']}  ({ok}/{count})")
                break
            if not done:
                fail += 1
            await asyncio.sleep(random.uniform(3, 6))
        await b.close()
    fh.close()
    log(f"完成: 成功 {ok} / 失败 {fail} -> {out}")
    return ok, fail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--out", default=os.path.join(OUTDIR, "accounts_batch4.csv"))
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--headful", action="store_true", help="xvfb 下有头模式，验证码通过率更高")
    ap.add_argument("--recycle", type=int, default=4, help="每 N 个号换一次浏览器")
    a = ap.parse_args()
    asyncio.run(run(a.count, a.out, a.retries, not a.headful, a.recycle))


if __name__ == "__main__":
    main()
