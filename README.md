# dsh-browser-lab · 自建浏览器自动化工具箱

当 curl 拿不到东西的时候用它。目标站点是 SPA、接口要 JS 才发、验证码必须真人交互 —— 这三种情况 curl 全部失效，而一个**真的 Chromium** 全部能过。

本仓库是从一个真实项目里抽出来的可复用骨架：一个需要过阿里云验证码 2.0 的站点，批量注册 → 领 CLI 令牌 → 查余额，全流程跑通后整理成这套代码。

---

## 为什么必须是「真浏览器」

三条实测结论，省你几个小时：

**1. 无头 shell 会被阿里云识别。** `chromium_headless_shell` 点验证码勾选框**永远不会通过**，换了 `channel="chromium"` 的完整版才有反应。

**2. 点击太早等于没点。** 验证码组件是懒加载的，骨架屏（`aliyunCaptcha-loading` 可见）阶段点下去，那一次交互就作废了，之后怎么点都没反应 —— 而且**不会报错**，只是勾选框一直空着。组件渲染大约需要 10 秒。

**3. 通过后组件会从 DOM 消失。** 这是最可靠的判定信号，比读任何内部状态都准：

```python
gone = await pg.evaluate("() => !document.querySelector('#aliyunCaptcha-window-embed')")
```

---

## 组件

| 文件 | 干什么 |
|---|---|
| `src/mona_browser.py` | 浏览器侧核心：`new_page` 反检测上下文、`goto_register`、`solve_captcha`、`harvest` 采参数 |
| `src/mona_pipe.py` | 流水线：浏览器点「获取验证码」→ 抓参数 → HTTP 落号，**一参一号不复用** |
| `src/mona_reg.py` | 纯 HTTP 注册：邮箱池、验证码池、429 退避 |
| `src/mona_client.py` | 生成客户端：建项目 → 开会话 → 发消息 → 轮询产物 |
| `src/approve_relay.py` | 设备码批准：`userCode` → `approved`，支持 `序号:码` 批量 |
| `src/bal_all.py` | 并发查余额 |

---

## 快速开始

```bash
pip install -r requirements.txt
python -m playwright install chromium

# 有头模式必须有 X 服务
sudo apt-get install -y xvfb

# 采验证码参数
xvfb-run -a python src/mona_browser.py harvest --count 5

# 流水线注册（推荐，一参一号）
xvfb-run -a python src/mona_pipe.py --count 5 --out accounts.csv --headful --recycle 2
```

`--headful` 不是可选项。无头能过的时候是运气，过不去的时候是必然。

---

## 验证码参数池：为什么不推荐

老做法是 `harvest` 先攒一堆 `X-MONA-Captcha-Verify-Param` 存池子，注册时一个个取。**实测通过率会掉到 30%。**

原因：池子里的参数放着就开始衰老，越老越容易被判 `CAPTCHA_FAILED`，而且失败一个就白扔一个。首批 18 个号用池子方案是 75% 通过率，第二批 10 个参数只出了 3 个号。

`mona_pipe.py` 的做法是**不攒**：浏览器里点按钮 → 验证码一过，站点自己的请求带着参数发出去（**不要 abort**）→ 立刻用同一个参数走注册。一个参数只服务一个号。

---

## 邮箱：两个通道，一个会限流

`mail.tm` 免登录免 key，但**按 IP 限流**。一天建 40 多个箱之后，`/accounts` 开始返回 429（响应体是空的，`r.json()` 直接抛 `JSONDecodeError`）。

所以 `TLol`（tempmail.lol）作为备胎：

```python
class TLol:
    def create(self):        # POST /v2/inbox/create  -> {address, token}
    def wait_code(self, tok) # GET  /v2/inbox?token=  -> 正则抓 6 位码
```

建箱失败就自动降级。想要彻底不受限，自建 catch-all 域名最稳：一个域名配 Cloudflare Email Routing，`任意前缀@你的域名` 无限开。

---

## 设备码授权：整条链可以在本地跑完

不用等第三方页面配合，三个请求全部本地可控：

```python
# 1) 生成码（无需登录）
d = POST /api/cli-auth/device            -> {userCode: "XXXX-XXXX", deviceCode: "..."}
# 2) 用账号批准
POST /api/cli-account/device-approvals   {"userCode": "XXXX-XXXX"}  -> status: approved
# 3) 换令牌
POST /api/cli-auth/device/token          {"deviceCode": "..."}      -> mona_cli_xxx
```

但要清楚区别：你自己造的 `deviceCode` 换来的令牌只属于你那一次流程；让第三方客户端自己发起，令牌才和它的会话绑定。

---

## 踩过的坑

| 现象 | 原因 | 处理 |
|---|---|---|
| `OSError: [Errno 38] Function not implemented` | proot 下 asyncio 的 `_call_connection_lost` 不可用 | `setsid nohup` 跑，或直接前台跑；别指望优雅关闭 |
| 5 个 chromium 全部被杀 | 内存不够，不是 CPU 不够 | 2 GB 可用内存只够 1 个实例；并发上限由内存定 |
| `pkill -f "xxx.py"` 把自己 shell 也杀了 | 自己的命令行里含同样的字符串 | 用 PID：`ps -eo pid,args \| awk -v me=$$ '$1!=me && /pattern/{print $1}'`，把 `$$` 排除 |
| 批量建箱抛 `RuntimeError: 建箱失败` | `mail.tm` 429，6 次重试全空 | 降级到 temail.lol；建箱挪出关键路径（先批量建好） |
| 注册成功但余额是 0 | 用了收费模型 | 标准生成扣 3 分，免费模型 `gpt-free` 扣 0 |
| 进程活着但日志不动 | 卡在 `wait_code`（200 s）或浏览器已死 | 外层加 `timeout`，小批量跑；别用「进程存在」判断健康 |

---

## 反检测做了什么

```python
# 1) 完整版 chromium，不是 headless shell
p.chromium.launch(channel="chromium", headless=headless,
    args=["--no-sandbox", "--disable-dev-shm-usage",
          "--disable-blink-features=AutomationControlled"])

# 2) 移动端上下文 + 触摸
ctx = await b.new_context(locale="zh-CN", user_agent=UA,
    viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)

# 3) 注入补丁：webdriver / languages / plugins / WebGL vendor
await ctx.add_init_script(STEALTH)
```

够过阿里云的智能验证，过不了 Cloudflare Turnstile 那种吃完整指纹和行为轨迹的。后者要么上住宅代理 + 真实指纹库，要么人工过。

---

## 使用边界

这套代码是用来**管理自己的账号**的。批量注册、抓取、自动化操作前先确认目标站点的条款，以及你有权操作那些账号。仓库里所有凭据都是占位符，实际使用时从 `accounts.csv` 读。

---

## 许可

MIT，见 [LICENSE](LICENSE)。
