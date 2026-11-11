#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按顺序批准 Muse 发来的设备码
用法:  python3 approve_relay.py 39QP-RK7L 2EUS-AQ5B ...
       python3 approve_relay.py 1:39QP-RK7L 6:2EUS-AQ5B ...   # 指定第几个号
"""
import csv, sys, time, requests

B = "https://monastudiopro.com/api"
H = {"Content-Type": "application/json", "Accept": "application/json",
     "User-Agent": "mona-cli/1.0.0", "Origin": "https://monastudiopro.com"}
ACCS = list(csv.DictReader(open("accounts_batch4.csv")))
CLI = {}
try:
    for r in csv.DictReader(open("accounts_cli.csv")):
        CLI[r["email"]] = r["cli_token"]
except Exception:
    pass


def approve(code, acc):
    h = dict(H); h["Authorization"] = "Bearer " + acc["token"]
    a = requests.post(B + "/cli-account/device-approvals", headers=h,
                      json={"userCode": code}, timeout=20).json()
    st = (a.get("data") or {}).get("status")
    return a.get("code"), st, a.get("message"), a.get("reasonCode")


def main():
    args = sys.argv[1:]
    if not args:
        print("给码呀"); return
    queue = []
    for i, a in enumerate(args):
        if ":" in a:
            idx, code = a.split(":", 1)
            queue.append((int(idx) - 1, code.strip().upper()))
        else:
            queue.append((i, a.strip().upper()))
    for idx, code in queue:
        if idx >= len(ACCS):
            print(f"  #{idx+1} 没有这个号，跳过 {code}"); continue
        acc = ACCS[idx]
        try:
            c, st, msg, rc = approve(code, acc)
        except Exception as e:
            print(f"  #{idx+1} {code} 网络错误 {e}"); continue
        mark = "OK" if st == "approved" else f"{rc or msg}"
        print(f"  #{idx+1} {code}  {acc['email']:26s} -> {mark}")
        time.sleep(1)


if __name__ == "__main__":
    main()
