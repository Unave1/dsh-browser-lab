# dsh-browser-lab · 自建浏览器自动化工具箱

curl 拿不到东西的时候用它。目标站是 SPA、接口要 JS 才发、验证码必须真人交互 —— 这三种情况 curl 全部失效，一个**真的 Chromium** 全部能过。

本仓库从真实项目抽出来的可复用骨架：一个需要过阿里云验证码 2.0 的站点，批量注册 → 领 CLI 令牌 → 查余额，全流程跑通后整理成这套代码。

---

## 五个脚本怎么串

```
        ┌─ mona_browser.py ──────────┐
        │  真 Chromium 上下文        │  过验证码、抓参数
        └────────────┬───────────────┘
                     │ captchaVerifyParam
                     ▼
        ┌─ mona_pipe.py ─────────────┐
        │  浏览器取码 + HTTP 落号    │  ← 主力，一参一号
        └────────────┬───────────────┘
                     │ accounts.csv
        ┌────────────┼────────────┐
        ▼            ▼            ▼
  bal_all.py   approve_relay.py  mona_client.py
   查余额        领 CLI 令牌      跑生成
```

`mona_reg.py` 是 `mona_pipe.py` 的上一代（纯 HTTP + 参数池），保留是为了对照，**不推荐**。

---

## 文件详解

### `src/mona_browser.py` — 浏览器侧核心

整个项目的地基。所有需要"真浏览器"的活都从这里起。

**`new_page(p, headless=True)`**
建上下文，三个反检测手段一次到位：

```python
b = await p.chromium.launch(channel="chromium", headless=headless, args=[
    "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
    "--disable-blink-features=AutomationControlled"])
ctx = await b.new_context(locale="zh-CN", user_agent=UA,
    viewport={"width":412,"height":915}, is_mobile=True, has_touch=True)
await ctx.add_init_script(STEALTH)     # webdriver / languages / plugins / WebGL vendor
```

`channel="chromium"` 是硬要求 —— 用默认的 headless shell，验证码永远点不过。

**`goto_register(pg)`**
打开注册页 → 等 3.5 秒 → 顺手点掉 cookie 横幅（`仅使用必要项` / `全部接受`）。横幅不关会挡住下面的元素点击。

**`solve_captcha(pg, timeout=70)`** ← 踩坑最多的一段

顺序不能变：

1. **等渲染完成**，判定条件是三个都成立：`#aliyunCaptcha-window-embed` 存在、`#aliyunCaptcha-checkbox-text` 文本含「不是机器人」、`#aliyunCaptcha-loading` 不可见。组件渲染大约 **10 秒**
2. 再稳 2.5 秒（躲过渡动画）
3. 鼠标分两段移过去（`steps=10` + `steps=14`），制造轨迹
4. 点击，然后**最多重试 3 次**（每次间隔 1.2 秒）—— 第一下没点中是常态
5. 通过信号：`#aliyunCaptcha-window-embed` 从 DOM 消失

失败时返回 `False`，不会抛异常。**提前点一下就会让整次交互作废**，而且不报错，这是最容易卡住的地方。

**`harvest(count, delay)`** — 采参数模式

```bash
xvfb-run -a python src/mona_browser.py harvest --count 10 --delay 5
```

每次循环：打开注册页 → 填随机邮箱 → 点「获取验证码」→ 过验证码 → 从 `POST /api/auth/email-code` 的请求头里抓 `X-MONA-Captcha-Verify-Param` → 写进 `captcha_pool.jsonl`。

关键一行是 `pg.route("**/api/auth/email-code", abort)` —— **把请求拦掉**，参数拿到了但邮件不发，不消耗额度。

产出：`captcha_pool.jsonl`，一行一个 `{param, ts, ttl}`。

---

### `src/mona_pipe.py` — 主力流水线

**推荐用这个。** 一个参数只服务一个号，不走池子。

```
预建邮箱池（并行，挪出关键路径）
  ↓
打开注册页 → 填这个号的邮箱 → 点「获取验证码」
  ↓
过验证码（真点）→ 站点自己的请求带参数发出去（不 abort，邮件真发）
  ↓
从请求头顺手抓一份参数留档
  ↓
轮询邮箱拿 6 位码 → POST /auth/register → 写 accounts.csv
```

参数：`--count` 数量、`--out` 输出 CSV、`--headful`（xvfb 下有头）、`--recycle N`（每 N 个号换一次浏览器）、`--retries` 每个号重试次数。

两个设计决定：

- **邮箱先批量建好**。`mail.tm` 建箱会 429 卡 40 秒以上，放关键路径里会让每个号都慢一拍
- **`--recycle 2`** 控制内存。浏览器复用久了风险分会累加，但每分钟换一次又会 OOM，2 是这台机器的平衡点

产出：`accounts.csv`（`email,password,mail_token,token,user_id,status,ts`）。

---

### `src/mona_reg.py` — 上一代（HTTP + 参数池）

不推荐，但里面两个类很有用，`mona_pipe.py` 直接复用它：

**`MailTM`** — 临时邮箱
- `create()` → `(address, token)`，6 次重试，429 时睡 8 秒
- `wait_code(token, timeout=180, poll=4)` → 轮询收件箱，正则 `(?<!\d)(\d{6})(?!\d)` 抓 6 位码

**`CaptchaPool`** — 参数池
- `_fresh()` 过滤掉过期（TTL 900 秒）和已用的
- `take(wait)` 取一个并标记已用

**`register_one(...)`** 完整注册一轮：建箱 → 取参数 → 发码 → 收码 → 注册 → 写 CSV。

为什么废掉池子方案：参数放着就开始衰老，越老越容易 `CAPTCHA_FAILED`，失败一个白扔一个。实测首批 18 个号 75% 通过，第二批 10 个参数只出 3 个号。

参数：`--count --workers --pool --out --proxy --proxy-file --invite --delay`

---

### `src/mona_client.py` — 生成客户端

不碰浏览器，纯 HTTP 调接口跑生成。

```
POST /api/projects                    建项目
POST /api/v2/agent/sessions           开会话
POST /api/v2/agent/sessions/{id}/messages   发提示词（扣积分）
轮询 → OSS 签名直链 → 下载
```

```bash
python src/mona_client.py --balance              # 查余额
python src/mona_client.py "画一只猫" --account 2  # 用第 2 个号生成
```

内部函数：`accounts()` 读 CSV、`hdr()` 拼头、`api()` 通用请求、`new_session()`、`send()`、`poll()`、`collect_urls()`（从返回 blob 里正则抠签名 URL）、`gen()` 串起来。

注意积分：标准生成扣 3，免费模型扣 0，2K/4K 更贵。跑之前先 `--balance`。

---

### `src/approve_relay.py` — 设备码批准

设备码授权的中间那一步。第三方客户端（CLI / Agent 连接器）发起登录显示 `XXXX-XXXX`，这里用对应账号批准。

```bash
python3 approve_relay.py 1:9VHB-FHPJ 2:T2LX-EK6U     # 指定序号
python3 approve_relay.py 9VHB-FHPJ T2LX-EK6U          # 按顺序对号入座
```

读 `accounts.csv` 取 token，`POST /api/cli-account/device-approvals` `{"userCode": "..."}`，打印 `approved` 或失败原因。

设备码一般 **10 分钟**过期，拿到就批，别攒。

---

### `src/bal_all.py` — 并发查余额

```bash
python3 bal_all.py                       # 默认 accounts.csv
python3 bal_all.py a1.csv a2.csv a3.csv  # 多个文件
```

6 线程并发 `GET /api/auth/user`，输出每行的 `credits` 和汇总。

---

### `requirements.txt` / `.gitignore`

`requests` + `playwright`。`.gitignore` 挡死 `accounts*.csv`、`captcha_pool.jsonl*`、`cookies.json`、`profile/`、`*.log` —— 防止账号表和登录态被提交。

---

## 三条实测结论（省你几小时）

**1. 无头 shell 会被阿里云识别。** `chromium_headless_shell` 点验证码勾选框**永远不会通过**，必须 `channel="chromium"` 的完整版。

**2. 点击太早等于没点。** 组件是懒加载的，骨架屏阶段点下去那一次交互就作废，之后怎么点都没反应，**而且不报错**。

**3. 通过后组件从 DOM 消失。** 这是最可靠的判定信号，比读任何内部状态都准。

---

## 环境准备

```bash
pip install -r requirements.txt
python -m playwright install chromium
sudo apt-get install -y xvfb        # 有头模式必须有 X 服务
```

---

## 踩过的坑

| 现象 | 原因 | 处理 |
|---|---|---|
| `OSError: [Errno 38] Function not implemented` | proot 下 asyncio 的 `_call_connection_lost` 不可用 | `setsid nohup` 跑或直接前台；别指望优雅关闭 |
| 5 个 chromium 全被杀 | 内存不够，不是 CPU 不够 | 2 GB 可用内存只够 1 个实例；并发上限由内存定 |
| `pkill -f "xxx.py"` 把自己 shell 也杀了 | 自己的命令行含同样字符串 | 按 PID 杀：`ps -eo pid,args \| awk -v me=$$ '$1!=me && /pattern/{print $1}'` |
| 批量建箱抛 `建箱失败` | `mail.tm` 429，6 次重试全空 | 降级 tempmail.lol；建箱挪出关键路径 |
| 注册成功但余额 0 | 用了收费模型 | 标准生成扣 3 分，`gpt-free` 扣 0 |
| 进程活着但日志不动 | 卡在 `wait_code`（200 秒）或浏览器已死 | 外层加 `timeout`，小批量跑；别用「进程存在」判断健康 |

---

## 邮箱：两个通道，一个会限流

`mail.tm` 免登录免 key，但**按 IP 限流**。一天建 40 多个箱之后 `/accounts` 开始返回 429，响应体是空的，`r.json()` 直接抛 `JSONDecodeError`。

`TLol`（tempmail.lol）做备胎，建箱即时、无 key：

```python
class TLol:
    def create(self):        # POST /v2/inbox/create  -> {address, token}
    def wait_code(self, tok) # GET  /v2/inbox?token=  -> 正则抓 6 位码
```

彻底不受限的做法是自建 catch-all 域名：一个域名配 Cloudflare Email Routing，`任意前缀@你的域名` 无限开。

---

## 设备码授权三条请求

不用等第三方页面配合，整条链本地可跑：

```python
d  = POST /api/cli-auth/device                      # 无需登录 -> userCode + deviceCode
ap = POST /api/cli-account/device-approvals  {userCode}   # 用账号批准 -> approved
tk = POST /api/cli-auth/device/token  {deviceCode}        # -> mona_cli_xxx
```

但要清楚区别：自己造的 `deviceCode` 换来的令牌只属于你这一次流程；让第三方客户端自己发起，令牌才和它的会话绑定。

---

## 反检测能做到哪

上面 `new_page` 那套（完整 chromium + 移动端上下文 + STEALTH 注入）够过阿里云智能验证。

过不了 Cloudflare Turnstile、reCAPTCHA v3 那种吃完整指纹和行为轨迹的 —— 那要么住宅代理 + 真实指纹库，要么人工。

---

## 使用边界

这套代码用来**管理自己的账号**。批量操作前先确认目标站点条款，以及你有权操作那些账号。仓库里没有凭据，实际使用时从 `accounts.csv` 读。

---

## 许可

MIT，见 [LICENSE](LICENSE)。
