#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MONA 生图客户端（走 Agent Session 接口）
  python3 mona_client.py "提示词"            # 用第 1 个可用号跑一次
  python3 mona_client.py "提示词" --account 2
  python3 mona_client.py --balance           # 查 5 个号积分

链路（2026-11 实测 monastudiopro.com）:
  POST /api/projects {}                                    -> projectId
  POST /api/v2/agent/sessions {projectId,title}            -> sessionId
  POST /api/v2/agent/sessions/{sid}/messages {content,requestId,frontendContext}
       -> runId, quotedCredits/chargedCredits
  轮询  GET /api/v2/agent/sessions/{sid}/messages 或 /runs/{runId}
  产物  GET /api/files/sign-urls 里的 OSS 直链（mona-ai.oss-cn-beijing.aliyuncs.com）

鉴权：Authorization: Bearer <token>
"""
import argparse, csv, json, os, sys, time, uuid
import requests

BASE = "https://monastudiopro.com"
CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts.csv")
OUTDIR = "/sdcard/Download/DSH-Go/mona"
UA = ("Mozilla/5.0 (Linux; Android 14; V2405A) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")


def accounts():
    return list(csv.DictReader(open(CSV, encoding="utf-8")))


def hdr(tok, extra=None):
    h = {"Authorization": "Bearer " + tok, "User-Agent": UA, "Accept": "application/json",
         "Origin": BASE, "Referer": BASE + "/?module=canvas"}
    if extra:
        h.update(extra)
    return h


def balance():
    out = []
    for a in accounts():
        try:
            r = requests.get(BASE + "/api/auth/user", headers=hdr(a["token"]), timeout=20)
            j = r.json().get("data") or {}
            out.append((a["email"], j.get("credits"), r.status_code))
        except Exception as e:
            out.append((a["email"], f"ERR {e}", 0))
    for e, c, s in out:
        print(f"{e:28s} credits={c} http={s}")
    return out


def api(method, path, tok, payload=None, timeout=40):
    r = requests.request(method, BASE + path, headers=hdr(tok), json=payload, timeout=timeout)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"raw": r.text[:300]}


def new_session(tok, title="DSH 生图"):
    sc, j = api("POST", "/api/projects", tok, {})
    pid = (j.get("data") or {}).get("projectId")
    if not pid:
        return None, None, ("create project failed", sc, j)
    sc, j = api("POST", "/api/v2/agent/sessions", tok, {"projectId": pid, "title": title})
    sid = (j.get("data") or {}).get("sessionId")
    if not sid:
        return pid, None, ("create session failed", sc, j)
    return pid, sid, None


def send(tok, sid, prompt, project_id):
    ctx = {"contextVersion": 3, "source": "browser-canvas",
           "canvasLayoutPolicy": "The browser automatically lays out new nodes using their rendered size and connections; actions usually do not need to provide x/y coordinates."}
    payload = {"content": prompt, "requestId": "dsh:" + str(uuid.uuid4()),
               "frontendContext": json.dumps(ctx, ensure_ascii=False)}
    return api("POST", f"/api/v2/agent/sessions/{sid}/messages", tok, payload)


def poll(tok, sid, run_id=None, limit=180):
    paths = [f"/api/v2/agent/sessions/{sid}/messages?page=1&pageSize=20",
             f"/api/v2/agent/sessions/{sid}/runs?page=1&pageSize=10"]
    if run_id:
        paths.insert(0, f"/api/v2/agent/runs/{run_id}")
    last = None
    for _ in range(limit // 4):
        for p in paths:
            sc, j = api("GET", p, tok)
            if sc == 200 and isinstance(j, dict):
                last = j
                blob = json.dumps(j, ensure_ascii=False)
                if any(k in blob for k in ('"COMPLETED"', '"FAILED"', '"SUCCEEDED"', '"FINISHED"')):
                    return last
        time.sleep(4)
    return last


def collect_urls(blob):
    import re
    urls = set(re.findall(r"https://[^\s\"'\\]+?\.(?:png|jpg|jpeg|webp)", blob))
    return sorted(urls)


def gen(prompt, idx=1):
    accs = accounts()
    a = accs[(idx - 1) % len(accs)]
    tok = a["token"]
    print(f"[*] 账号 {a['email']}")
    pid, sid, err = new_session(tok)
    if err:
        print("[!]", err)
        return
    print(f"[*] project={pid} session={sid}")
    sc, j = send(tok, sid, prompt, pid)
    print("[*] send:", sc, json.dumps(j, ensure_ascii=False)[:200])
    run_id = ((j.get("data") or {}) if isinstance(j, dict) else {}).get("runId")
    blob = poll(tok, sid, run_id)
    txt = json.dumps(blob, ensure_ascii=False)
    urls = collect_urls(txt)
    os.makedirs(OUTDIR, exist_ok=True)
    saved = []
    for u in urls:
        try:
            fn = os.path.join(OUTDIR, f"{int(time.time())}_{uuid.uuid4().hex[:6]}.png")
            r = requests.get(u, timeout=60)
            if r.status_code == 200 and len(r.content) > 2000:
                open(fn, "wb").write(r.content)
                saved.append(fn)
        except Exception as e:
            print("   dl err", e)
    print(f"[+] 找到 {len(urls)} 个图链，存下 {len(saved)} 张 -> {OUTDIR}")
    for s in saved:
        print("   ", s)
    open(os.path.join(OUTDIR, "last_run.json"), "w").write(txt)
    return saved


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="?")
    ap.add_argument("--account", type=int, default=1)
    ap.add_argument("--balance", action="store_true")
    a = ap.parse_args()
    if a.balance:
        balance()
    elif a.prompt:
        gen(a.prompt, a.account)
    else:
        ap.print_help()
