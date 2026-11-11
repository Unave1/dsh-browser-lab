# dsh-browser-lab · 自建一个能用的浏览器

没有 GUI 的 Android proot 环境里，从零装出一个**能截图、能取文、能保持登录、不被当场认成机器人**的浏览器。

不依赖任何云服务、不需要 root、不需要图形界面。装完就有个命令行 `nb`。

---

## 为什么需要自建

三件事 curl 全干不了：

| 情况 | curl 的结果 |
|---|---|
| 目标站是 SPA，内容靠 XHR 拉 | 拿到空壳 HTML |
| 接口要带页面里算出来的签名 | 401 / 403 |
| 有验证码要真人交互 | 直接没过这一关 |

而一个**真的 Chromium** 全部能过。关键在「真的」两个字 —— 下面这条是硬结论：

> `chromium_headless_shell` 点阿里云验证码的勾选框**永远不会通过**。必须 `channel="chromium"` 的完整版。

---

## 装

```bash
bash install.sh
```

干四件事：

1. `apt-get install xvfb x11-utils` —— 有头模式的 X 服务
2. `pip install requests playwright`
3. `playwright install chromium` —— 完整版
4. 自检：打印 Xvfb 状态和 chromium 可执行文件路径

装完验证：

```bash
xvfb-run -a python3 src/nb.py text https://example.com --headful
```

大约 5 秒出结果。实测输出：

```
该域名仅用于文档示例，无需获得许可。这并非一项服务……
```

---

## 文件详解

### `src/launcher.py` — 起一个「像人」的上下文

三个函数，是整个项目的地基。

**`STEALTH`** —— 一段注入脚本，六个补丁：

```js
navigator.webdriver          -> undefined
navigator.languages          -> ['zh-CN','zh','en']
navigator.plugins            -> [1,2,3,4,5]          // 无头环境插件列表是空的
window.chrome                -> {runtime:{}, app:{}} // 无头环境没有这个对象
permissions.query            -> notifications 返回 denied
WebGLRenderingContext.getParameter -> Qualcomm / Adreno 740   // 真实 GPU 字符串
```

无头环境这几项全是「一眼假」，不补的话设备指纹直接掉分。

**`new_page(p, headless, profile, mobile, viewport, ua, stealth, locale)`**

一次性把启动参数配好：

```python
launch(channel="chromium", headless=headless, args=[
    "--no-sandbox",                      # proot 里必须
    "--disable-dev-shm-usage",           # /dev/shm 太小
    "--disable-gpu",
    "--disable-blink-features=AutomationControlled"])   # 去掉自动化标记
```

`profile` 传了目录就走 `launch_persistent_context`，**登录态存在目录里**，下次打开还是登录状态。返回值是 `(owner, page)`，`owner.close()` 收尾 —— profile 模式下 owner 是 Context，否则是 Browser。

**`settle(pg, ms)`** —— 等 `networkidle`，超时就忽略。SPA 里 `domcontentloaded` 之后还有一堆 XHR，不等就是抓到半成品。

---

### `src/nb.py` — 命令行

五个动作：

| 命令 | 干什么 | 输出 |
|---|---|---|
| `shot URL -o a.png` | 截图，`--full` 整页，`--sel` 只截某个元素 | PNG |
| `text URL` | 取可见文本（`body.innerText`），`--sel` 取单元素 | stdout |
| `html URL -o a.html` | 取渲染后的 DOM | 文件或 stdout |
| `pdf URL -o a.pdf` | 存 PDF，A4，带背景 | PDF |
| `cookies URL -o c.json` | 导出 cookie | JSON |

通用参数：`--headful`（推荐）、`--profile DIR`（持久化）、`--wait MS`（稳定后额外等）、`--desktop`（换桌面 UA，默认移动端）、`--timeout`。

没 X 显示又加了 `--headful` 会直接提示你用 `xvfb-run` 包一层，不会静默失败。

---

### `examples/login_keep.py` — 登录一次，长期复用

```bash
# 第一次：弹出页面手动登录
xvfb-run -a python3 examples/login_keep.py --profile ./profile --url https://site.com

# 之后：直接检查还在不在登录态
python3 examples/login_keep.py --profile ./profile --check
```

原理就是 `launch_persistent_context` —— cookie、localStorage、IndexedDB 全在 `./profile` 里。

---

### `examples/intercept.py` — 看接口 / 改参数 / 掐请求

监听所有 `/api/` 请求，打印方法、URL、`x-` 开头的头、POST body。

最有用的一条：**把请求 abort 掉，参数照样拿得到**。

```python
pg.on("request", lambda r: print(dict(r.headers)))   # 先拿
await pg.route("**/*email-code*", lambda r: r.abort())  # 再掐
```

取 token、取签名、取 `captchaVerifyParam` 这类东西全靠这招 —— 数据到手，副作用不发生。

---

### `install.sh` / `requirements.txt` / `LICENSE`

装环境、两个依赖（`requests` + `playwright`）、MIT。

---

## 原理与边界

### 有头比无头强在哪

无头模式少了三样东西：GPU 渲染上下文、真实的插件列表、完整的事件时序。指纹检测一看就知道。

`xvfb-run` 给一个虚拟 X 服务，Chromium 以为自己有屏幕，这三样全都回来了。代价是内存 —— 有头比无头多吃 200–300 MB。

### 内存是硬上限

这台机器 7.2 GB 总量，可用常年 1.8–2 GB。**一个 chromium 实例吃 500–700 MB**，所以并发上限就是 **1**。

5 个并行不是变慢，是直接被 OOM 杀掉。想并行先解决内存，或者分机器跑。

### proot 的老毛病

```
OSError: [Errno 38] Function not implemented
  in asyncio/unix_events.py: _call_connection_lost
```

proot 里 asyncio 的 socket 析构走不通。表现是脚本干完活之后崩在收尾。处理：`setsid nohup` 跑、或前台跑、或者干脆让进程被 `timeout` 杀掉 —— 别指望它优雅退出。

### 能过什么，过不了什么

| 类型 | 结果 |
|---|---|
| 阿里云验证码 2.0（智能验证） | 能过，要点是等组件渲染完（约 10 秒）再点 |
| 普通滑块 / 点选 | 要自己实现轨迹和识别 |
| Cloudflare Turnstile | 过不了，吃完整指纹和行为轨迹 |
| reCAPTCHA v3 | 过不了，同上 |

后两个要么住宅代理 + 真实指纹库，要么人工。

---

## 踩过的坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 点验证码永远不过 | 用了 headless shell | `channel="chromium"` |
| 点了没反应，也不报错 | 组件还在骨架屏就点了，交互作废 | 等 `#aliyunCaptcha-loading` 不可见再点，并且重试 3 次 |
| `pkill -f "xxx.py"` 把自己 shell 也杀了 | 自己的命令行里含同样字符串 | 按 PID 杀，排除 `$$` |
| 日志不动但进程活着 | 卡在网络等待 | 外层 `timeout`，别用「进程存在」判断健康 |

---

## 用法边界

这是给你自己的机器做工具。抓取和自动化之前先确认目标站点条款。

---

## 许可

MIT，见 [LICENSE](LICENSE)。
