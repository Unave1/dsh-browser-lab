#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MONA 批量注册（HTTP 直连版）
站点: https://monastudiopro.com   API: https://monastudiopro.com/api

流程（前端 bundle 里扒出来的真实接口）:
  1. POST /auth/email-code  {"email","scene":"register"}
     header  X-MONA-Captcha-Verify-Param  = 阿里云验证码2.0 的 captchaVerifyParam
     (缺 header -> CAPTCHA_REQUIRED; 伪造 -> CAPTCHA_FAILED)
  2. 收件箱取 6 位数字验证码
  3. POST /auth/register {"email","code","username?","nickname?","password","inviteCode?","rememberMe"}
     password >= 8 位; inviteCode 8 位 [A-Za-z0-9]，非必填
     返回 {token, userInfo, redirectUrl}，token 走 Authorization

用法:
  # 1) 先用 mona_browser.py harvest 攒验证码参数池
  python3 mona_browser.py harvest --count 10
  # 2) 批量注册
  python3 mona_reg.py --count 10 --out accounts.csv
"""
import argparse, csv, itertools, json, os, random, re, string, sys, threading, time
import concurrent.futures as cf
import requests

BASE   = "https://monastudiopro.com/api"
SITE   = "https://monastudiopro.com"
CAP_H  = "X-MONA-Captcha-Verify-Param"
OUTDIR = os.path.dirname(os.path.abspath(__file__))

UA_POOL = [
    "Mozilla/5.0 (Linux; Android 14; V2405A) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]

_lock = threading.Lock()
_print = lambda *a: print(*a, flush=True)


# ---------------------------------------------------------------- 邮箱
class MailTM:
    """mail.tm 临时邮箱（免登录、免 key）。量大时换成自有域名 catch-all 更稳。"""
    API = "https://api.mail.tm"

    def __init__(self, domain=None):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = random.choice(UA_POOL)
        self.domain = domain or self._pick_domain()

    def _pick_domain(self):
        r = self.session.get(f"{self.API}/domains", timeout=20).json()
        return r["hydra:member"][0]["domain"]

    def create(self):
        for _ in range(6):
            user  = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
            addr  = f"{user}@{self.domain}"
            pwd   = "".join(random.choices(string.ascii_letters + string.digits, k=16))
            r = self.session.post(f"{self.API}/accounts", json={"address": addr, "password": pwd}, timeout=20)
            if r.status_code in (200, 201):
                try:
                    tok = self.session.post(f"{self.API}/token", json={"address": addr, "password": pwd}, timeout=20).json()["token"]
                except Exception:
                    time.sleep(3); continue
                return addr, tok
            if r.status_code == 429:
                time.sleep(8); continue
            time.sleep(2)
        raise RuntimeError("mail.tm 建箱失败")

    def wait_code(self, token, timeout=180, poll=4):
        hdr = {"Authorization": f"Bearer {token}"}
        end = time.time() + timeout
        seen = set()
        while time.time() < end:
            r = self.session.get(f"{self.API}/messages", headers=hdr, timeout=20)
            if r.status_code == 200:
                for m in r.json().get("hydra:member", []):
                    if m["id"] in seen:
                        continue
                    seen.add(m["id"])
                    d = self.session.get(f"{self.API}/messages/{m['id']}", headers=hdr, timeout=20).json()
                    body = " ".join([d.get("subject") or "", d.get("text") or "", " ".join(d.get("html") or [])])
                    mm = re.search(r"(?<!\d)(\d{6})(?!\d)", body)
                    if mm:
                        return mm.group(1)
            time.sleep(poll)
        raise TimeoutError("等验证码超时")


# ---------------------------------------------------------------- 验证码池
class CaptchaPool:
    """captcha_pool.jsonl 一行一个阿里云 captchaVerifyParam（mona_browser.py harvest 产出）。
    只读池文件 + 独立 used 记录，不重写池子，避免和采集进程抢写。"""
    def __init__(self, path, used_path=None):
        self.path = path
        self.used_path = used_path or (path + ".used")
        self.lock = threading.Lock()
        self.consumed = set()
        if os.path.exists(self.used_path):
            with open(self.used_path) as f:
                self.consumed = {l.strip() for l in f if l.strip()}

    def _fresh(self):
        out = []
        if os.path.exists(self.path):
            with open(self.path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        it = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if it.get("param") in self.consumed:
                        continue
                    if time.time() - it.get("ts", 0) > it.get("ttl", 900):
                        continue
                    out.append(it["param"])
        return out

    def take(self, wait=0):
        deadline = time.time() + wait
        while True:
            with self.lock:
                got = self._fresh()
                if got:
                    p = got[0]
                    self.consumed.add(p)
                    with open(self.used_path, "a") as f:
                        f.write(p + "\n")
                    return p
            if time.time() >= deadline:
                raise RuntimeError("验证码池空了 —— 采集进程没跟上或参数已过期")
            time.sleep(2)


# ---------------------------------------------------------------- 业务
def new_session(proxy=None):
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(UA_POOL),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": SITE,
        "Referer": f"{SITE}/login",
        "Content-Type": "application/json",
    })
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    try:
        s.get(SITE + "/", timeout=20)   # 拿 acw_tc / cdn_sec_tc
    except requests.RequestException:
        pass
    return s


def api(s, path, payload, extra_headers=None, timeout=25):
    h = dict(extra_headers or {})
    r = s.post(BASE + path, data=json.dumps(payload), headers=h, timeout=timeout)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"code": r.status_code, "message": r.text[:200], "reasonCode": "NON_JSON"}


def gen_username():
    return "u" + "".join(random.choices(string.ascii_lowercase + string.digits, k=9))


def gen_password():
    core = "".join(random.choices(string.ascii_letters + string.digits, k=10))
    return core + "!aA9"


def register_one(pool, out_writer, proxy=None, invite=None, log=_print):
    mail = MailTM()
    email, mail_tok = mail.create()
    password = gen_password()
    s = new_session(proxy)

    last = None
    for attempt in range(4):
        param = pool.take(wait=600)
        st, js = api(s, "/auth/email-code", {"email": email, "scene": "register"}, {CAP_H: param})
        rc = js.get("reasonCode")
        if rc in ("CAPTCHA_REQUIRED", "CAPTCHA_FAILED"):
            last = f"captcha {rc}"
            log(f"    ~ {email} 参数失效，换下一个 ({attempt+1}/4)")
            time.sleep(1.5)
            continue
        if js.get("code") not in (0, 200):
            raise RuntimeError(f"email-code: {rc} {js.get('message')}")
        last = None
        break
    if last:
        raise RuntimeError(last)

    code = mail.wait_code(mail_tok)
    body = {"email": email, "code": code, "password": password,
            "username": gen_username(), "rememberMe": True}
    if invite:
        body["inviteCode"] = invite
    st, js = api(s, "/auth/register", body)
    if js.get("code") not in (0, 200):
        raise RuntimeError(f"register: {js.get('reasonCode')} {js.get('message')}")
    data = js.get("data") or {}

    rec = {"email": email, "password": password, "mail_token": mail_tok,
           "token": data.get("token") or "", "user_id": (data.get("userInfo") or {}).get("id", ""),
           "status": "ok", "ts": int(time.time())}
    with _lock:
        out_writer(rec)
    log(f"[+] {email}  {password}  token={rec['token'][:18]}...")
    return rec


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1, help="注册数量")
    ap.add_argument("--workers", type=int, default=1, help="并发（建议 1-3）")
    ap.add_argument("--pool", default=os.path.join(OUTDIR, "captcha_pool.jsonl"))
    ap.add_argument("--out", default=os.path.join(OUTDIR, "accounts.csv"))
    ap.add_argument("--proxy", default="", help="单代理，如 http://127.0.0.1:7890")
    ap.add_argument("--proxy-file", default="", help="代理列表，一行一个")
    ap.add_argument("--invite", default="", help="邀请码（8 位，可选）")
    ap.add_argument("--delay", type=float, default=3.0, help="每个号之间最小间隔秒")
    a = ap.parse_args()

    proxies = []
    if a.proxy_file and os.path.exists(a.proxy_file):
        proxies = [l.strip() for l in open(a.proxy_file) if l.strip()]
    elif a.proxy:
        proxies = [a.proxy]

    pool = CaptchaPool(a.pool)
    _print(f"[*] 验证码池: {len(pool._fresh())} 个可用参数")

    exists = set()
    newfile = not os.path.exists(a.out)
    fh = open(a.out, "a", newline="", encoding="utf-8")
    fields = ["email", "password", "mail_token", "token", "user_id", "status", "ts"]
    w = csv.DictWriter(fh, fieldnames=fields)
    if newfile:
        w.writeheader()
    for row in csv.DictReader(open(a.out, encoding="utf-8")):
        exists.add(row["email"])

    def writer(rec):
        w.writerow(rec)
        fh.flush()

    ok = fail = 0
    cyc = itertools.cycle(proxies) if proxies else None
    tasks = list(range(a.count))

    def job(i):
        nonlocal ok, fail
        try:
            register_one(pool, writer, next(cyc) if cyc else None, a.invite)
            with _lock:
                ok += 1
        except Exception as e:
            with _lock:
                fail += 1
            _print(f"[-] #{i+1} {e}")
        time.sleep(a.delay + random.uniform(0, a.delay))

    if a.workers <= 1:
        for i in tasks:
            job(i)
    else:
        with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
            list(ex.map(job, tasks))

    fh.close()
    _print(f"\n完成: 成功 {ok} / 失败 {fail} -> {a.out}")


if __name__ == "__main__":
    main()
