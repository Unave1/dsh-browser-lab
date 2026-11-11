#!/usr/bin/env bash
# 在这台机器上装一个能用的浏览器
set -euo pipefail

echo "· 装 X 服务（有头模式必需）"
apt-get update -qq
apt-get install -y xvfb x11-utils

echo "· 装 python 依赖"
pip install -r requirements.txt

echo "· 装 chromium（完整版，不是 headless shell）"
python -m playwright install chromium

echo "✓ 自检"
Xvfb -help >/dev/null 2>&1 && echo "  Xvfb ok"
python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    print("  chromium:", p.chromium.executable_path)
PY
echo
echo "跑一下："
echo "  xvfb-run -a python3 src/nb.py text https://example.com --headful"
