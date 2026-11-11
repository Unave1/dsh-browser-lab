#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""并发查一批账号的余额。

用法:
  python3 bal_all.py                      # 默认读 accounts.csv
  python3 bal_all.py accounts.csv a2.csv   # 读多个文件
"""
import csv
import sys
import concurrent.futures as cf

import requests

BASE = "https://monastudiopro.com"
UA = ("Mozilla/5.0 (Linux; Android 14; V2405A) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")


def hdr(t):
    return {"Authorization": "Bearer " + t, "User-Agent": UA,
            "Accept": "application/json", "Origin": BASE,
            "Referer": BASE + "/?module=canvas"}


def one(item):
    f, email, tok = item
    try:
        r = requests.get(BASE + "/api/auth/user", headers=hdr(tok), timeout=25)
        j = r.json().get("data") or {}
        return (f, email, j.get("credits"), r.status_code)
    except Exception as e:
        return (f, email, None, f"ERR {type(e).__name__}")


def main():
    files = sys.argv[1:] or ["accounts.csv"]
    rows = []
    for f in files:
        try:
            for a in csv.DictReader(open(f, encoding="utf-8")):
                rows.append((f, a["email"], a["token"]))
        except FileNotFoundError:
            print(f"! 没有 {f}，跳过")
    if not rows:
        print("没有账号可查")
        return
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(one, rows))
    total = 0
    bad = 0
    for f, e, c, s in res:
        print(f"{f:18s} {e:26s} credits={c} http={s}")
        if isinstance(c, (int, float)):
            total += c
        else:
            bad += 1
    print(f"\n账号 {len(res)}  积分合计 {total}  失败 {bad}")


if __name__ == "__main__":
    main()
