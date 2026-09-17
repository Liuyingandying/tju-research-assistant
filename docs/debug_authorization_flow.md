# 授权检查流程调试审计：Edge“闪退”与“无法确定授权状态”

> 阶段一 · 只读审计（未修改任何代码）。日期：2026-09-02。
> 目标：定位真实运行中点击“检查授权状态”后 Edge 弹出即关闭、GUI 显示
> “无法确定天津大学授权状态”的根因。本文件只做分析与方案，不改代码。

---

## 1. 当前调用链（“检查授权状态”按钮 → 页面关闭）

```
主线程 (MainWindow)                          worker 线程 (BrowserWorker)
─────────────────────                       ────────────────────────────
main.py:16  QApplication
  └─ MainWindow.__init__  main_window.py:51
      ├─ _build_ui  : btn_check_auth = QPushButton("检查授权状态")  :113
      └─ _connect_worker :113,161
          btn_check_auth.clicked → _on_check_auth
              └─ main_window.py:182 _on_check_auth()
                  ├─ 状态“正在检查天津大学授权...” / 禁用按钮
                  └─ check_auth_requested.emit()
                      └─ :169 check_auth_requested → BrowserWorker.check_auth
                          （Qt QueuedConnection 跨线程投递到 worker 线程）

worker.py:74  @Slot() check_auth()
  ├─ status_changed.emit("正在启动浏览器...")
  ├─ _, service = _new_session()              worker.py:37
  │   ├─ 若已有旧会话：shutdown() + time.sleep(5.0)   ← “等待一段时间”来源之一
  │   ├─ session = BrowserSession(); session.start()  worker.py:50
  │   │     session.py:36 start()
  │   │       ├─ find_edge_executable() → 本机 Edge
  │   │       ├─ launch_persistent_context(build_launch_kwargs(edge_path))
  │   │       │     poc_browser.py:54  user_data_dir=runtime/edge_profile
  │   │       │                       headless=False, chromium_sandbox=True
  │   │       │                       executable_path=msedge.exe
  │   │       └─ page.goto(START_URL)  ← 失败仅 logger.warning，被吞  :52-54
  │   └─ self._service = SearchService(session)
  ├─ state = service.check_tju_auth()         search_service.py:39
  │     ├─ session.open_tju()                 session.py:69
  │     │     ├─ open_url(DEFAULT_URL)        session.py:65
  │     │     │     → self._page.goto("https://p.lib.tju.edu.cn/",
  │     │     │                 wait_until="domcontentloaded", timeout=60000)
  │     │     └─ self._page.wait_for_timeout(2500)     ← 固定 2.5s，不等待跳转稳定
  │     └─ session.check_tju_auth()           session.py:77
  │           ├─ url  = self._page.url
  │           ├─ body = self._page.inner_text("body")
  │           │        （两行任一异常 → 静默 return "unknown"，无日志  :82-84）
  │           ├─ is_login_url(url)              → "login_page"
  │           ├─ "已授权" in body               → "logged_in"
  │           ├─ "登录" in body[:150]           → "login_page"
  │           └─ 否则                            → "unknown"
  ├─ auth_result.emit(state)                  worker.py:81
  └─ finally: self._release()                 worker.py:83-84, 63-71
        └─ session.shutdown()                 session.py:108
              ├─ context.close()   ← ★ Edge 在此立即关闭（用户看到的“闪退”）
              └─ playwright.stop()

main_window.py:285  _on_auth_result(state)
  ├─ "logged_in"  → “天津大学授权正常”
  ├─ "login_page" → “需要重新登录……请在受控浏览器中完成官方登录后再次检查”
  └─ else(unknown)→ “无法确定天津大学授权状态”   ← 本次命中此分支
```

**结论（调用链层面）**：Edge“弹出即关闭”不是崩溃，而是 `worker.check_auth`
的 `finally: self._release()` **无条件**在检查结束后立即 `context.close()`。
这是设计如此（`worker.py:7-9` 注释：每个任务独立会话、任务结束释放），与
`open_detail` 成功后“保持 Edge 运行”的策略不同（`worker.py:108-109`）。

---

## 2. 用户要求的六项逐项核查

| # | 问题 | 结论 |
|---|------|------|
| 2.1 | BrowserSession 是否在授权检查结束后自动 release | **是**。`check_auth` 的 `finally` 无条件调用 `_release()` → `shutdown()` → `context.close()`。无论状态是 logged_in / login_page / unknown，Edge 都会立刻关闭。 |
| 2.2 | 异常是否被吞掉 | **有多处**。最相关：`check_tju_auth` 读 `url`/`body` 的 `except Exception: return "unknown"` **完全静默**（无日志，`session.py:82-84`）。另有 `start()` 入口加载失败仅 warning（:52-54）、`shutdown()` 吞异常（:112-118）、`_new_session` 吞旧会话 shutdown 异常（`worker.py:41-44`）、`portal.py` 多处。 |
| 2.3 | page.close / context.close 是否提前执行 | 代码里**没有 page.close()**。`context.close()` 只出现在 `shutdown()`（被 `_release` / `_new_session` 调用）。但 `check_auth` 在检查返回后**立即** `context.close()`，属“提前执行”，用户没有机会在受控 Edge 里完成登录。 |
| 2.4 | 登录失败与授权失败是否区分 | **语义上有三态**（logged_in / login_page / unknown），但：① `unknown` 是兜底，既覆盖“页面未就绪”也覆盖“读取异常被吞”，无法区分；② `check_auth` 流程不抛 `AuthError`（`AuthError` 只在 `search` 流程用，`search_service.py:62-63`）；③ GUI 对 `login_page` 的提示“请在受控浏览器中完成官方登录”**不可能执行**——此时 Edge 已被 2.1 关掉。 |
| 2.5 | persistent_context 是否正确保持 | **正确**。`build_launch_kwargs` 固定 `user_data_dir=runtime/edge_profile`、`headless=False`、`chromium_sandbox=True`、本机 Edge 路径（`poc_browser.py:54-70`；`runtime/edge_profile` 存在 Default/Cookies/Local State）。Profile 未被误指向系统默认（测试 `test_poc_browser.py`、`tests/test_browser_session.py` 均覆盖）。 |
| 2.6 | 真实登录流程行为 | **会话已过期**，见第 3 节。 |

---

## 3. 真实运行行为（依据 Edge History / Crashpad 证据）

### 3.1 程序确实打开 `runtime/edge_profile`（已确认）

- `poc_browser.py:EDGE_PROFILE_DIR = runtime/edge_profile`，`build_launch_kwargs`
  始终使用该目录；`Local State`、`Default/History`、`Network/Cookies` 今天
  （2026-09-02 12:57 本地 = 04:57 UTC）均被更新，证明今天 Edge 用该 Profile 启动过。
- `Last Browser` 指向 `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`
  （与代码候选路径一致）。

### 3.2 访问 `p.lib.tju.edu.cn` 的真实行为（决定性证据）

Edge History（`Default/History.urls`，只读、非凭证）最近两条：

```
2026-09-02 04:56:51 UTC  https://p.lib.tju.edu.cn/
2026-09-02 04:56:53 UTC  https://p.lib.tju.edu.cn/login
```

即：今天启动后先加载 `p.lib.tju.edu.cn/`，**约 2 秒后门户 Angular SPA
客户端重定向到 `p.lib.tju.edu.cn/login`** —— 说明天津大学会话已过期。

历史（9/1，会话仍有效时）SSO 链路为：
`p.lib.tju.edu.cn/login?url=…` → `user.lib.tju.edu.cn/#/login?client_id=…`
→ `user.lib.tju.edu.cn/#/authorize` → 回 `p.lib.tju.edu.cn`。故
`user.lib.tju.edu.cn` 是真实 SSO 认证页，URL 含 `login`，`is_login_url` 能命中。

### 3.3 为什么仍返回 “unknown” 而不是 “login_page”

`check_tju_auth` 只**采样一个瞬间**：

- `goto(wait_until="domcontentloaded")` 在**初始文档**的 DOMContentLoaded 就返回，
  不会等 Angular 启动、也不会等客户端重定向；
- 之后固定 `wait_for_timeout(2500)`（2.5s）；
- 从 History 看重定向发生在 +2s 左右，与 2.5s 等待**赛跑**。

若读取 `page.url` / `inner_text("body")` 的时刻早于重定向完成：
- `url` 仍是 `https://p.lib.tju.edu.cn/` → `is_login_url` = False；
- `body` 是未就绪的 SPA 壳（无 “已授权”，且 “登录” 不在 `body[:150]`）→ **“unknown”**；
- 若读取瞬间页面正在导航/执行上下文被销毁，Playwright 抛错 → 被 2.2 的
  `except` **静默吞掉** → 同样是 **“unknown”**。

而若读取发生在重定向之后，`p.lib.tju.edu.cn/login` 含 `login` → 本应正确返回
“login_page”。**结论：主因是检查时序过早/等待不充分，与 SPA 重定向赛跑。**

---

## 4. 根因候选（按可信度排序）

### RC-1（主因，确认）· 授权检查与 SPA 重定向存在时序竞态
- `goto(domcontentloaded)` + 固定 2.5s 不等“跳转稳定”；
- `check_tju_auth` 单点采样，在 `p.lib.tju.edu.cn/ → /login` 重定向（约 +2s）完成前
  读取 → URL 无 login 特征、body 未含授权文本 → `unknown`。
- 证据：History 的 `/`（04:56:51）→ `/login`（04:56:53）时序。

### RC-2（确认，设计缺陷）· 检查结束立即无条件释放 Edge
- `worker.check_auth` 的 `finally: _release()` → `context.close()`；
- 用户看到的“Edge 页面立即关闭”即源于此，与是否崩溃无关；
- 与 PoC-0“浏览器保持运行供手动登录”的设计相悖，也使 GUI 的
  “请在受控浏览器中完成官方登录”提示无法执行。

### RC-3（候选，放大因素）· 读取异常被静默吞成 `unknown`
- `check_tju_auth` 的 `except: return "unknown"` 无任何日志；
- 重定向瞬间的执行上下文销毁/页面导航，会被误判为“无法确定”。

### RC-4（候选，历史真实崩溃，但**今天非主因**）· Edge 进程 ACCESS_VIOLATION
- Crashpad 记录 2026-09-01 21:47 与 09-01 02:22 多次崩溃：
  - `msedge.dll` `0xc0000005`（ACCESS_VIOLATION）— renderer / utility / gpu-process；
  - `msedge_elf.dll` `SubCode=0x517a7ed` — browser 进程。
- **但今天 09-02 12:57 的运行未产生新转储**（`Crashpad/reports` 最新仍为 09-01 21:47），
  且 History 显示页面正常加载并重定向 → 今天“立即关闭”不是崩溃，是 RC-2。
- 该崩溃历史仍需在后续复现时排查（可能与多标签/GPU 有关），但不应作为今天现象的解释。

---

## 5. 已验证事实汇总

1. `persistent_context` 正确指向 `runtime/edge_profile`（唯一 Profile，非系统默认）。
2. Edge 可执行文件为本机 `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`（152.0.4191.53），启动参数含 `chromium_sandbox=True`、`headless=False`。
3. 今天 TJU 会话已过期：门户 `p.lib.tju.edu.cn/` 客户端重定向到 `p.lib.tju.edu.cn/login`（History 证实，间隔约 2s）。
4. `check_tju_auth` 存在单点采样 + 静默吞异常，返回 `unknown` 路径无日志、无 traceback。
5. `check_auth` 在 `finally` 无条件 `_release()`，Edge 检查后必关（与 open_detail 的“成功后保持运行”策略不一致）。
6. 无 `page.close()`；`context.close()` 仅经 `shutdown()` 触发。
7. 测试覆盖缺口：`worker.check_auth`、`check_tju_auth` 启发式本身、`is_login_url("p.lib.tju.edu.cn/login")` 均无直接测试。
8. 200 passed 均为 mock/启发式/结构测试，不覆盖真实浏览器时序。

---

## 6. 临时调试方案（不要提交，定位“闪退瞬间”）

在 `session.check_tju_auth` 临时增加（改完即可跑，勿 commit）：

```python
def check_tju_auth(self) -> str:
    self._require_started()
    try:
        url = self._page.url
        body = self._page.inner_text("body") or ""
    except Exception:
        # 关键改动：不再静默，打印 traceback 以区分“崩溃”vs“导航中”
        logger.exception(
            "check_tju_auth 读取页面失败 url=%r pages=%d",
            self._page.url if self._page else None,
            len(self._context.pages) if self._context else -1,
        )
        return "unknown"
    logger.info(
        "auth probe url=%r title=%r pages=%d body_head=%r",
        url,
        self._page.title(),
        len(self._context.pages),
        body[:150],
    )
    if is_login_url(url):
        return "login_page"
    ...
```

并在 `worker.check_auth` 的 `finally` 之前临时打印释放前状态：

```python
try:
    ...
    state = service.check_tju_auth()
    self.status_changed.emit(f"检查返回: {state}")
    self.auth_result.emit(state)
except Exception as exc:
    import traceback
    logger.exception("check_auth 异常")          # 或 traceback.print_exc()
    self.error.emit(f"检查授权失败：{exc}")
finally:
    self._release()
```

**判定规则**：
- 若日志显示 `url=p.lib.tju.edu.cn/`、`body_head` 无 “已授权/登录” → **RC-1（时序竞态）**；
- 若日志显示 `url=p.lib.tju.edu.cn/login` 却仍返回 unknown（不可能，仅为完整性）→ 检查启发式；
- 若 `except` 分支打印出 `Target page/context/browser has been closed` → 叠加 **RC-4（崩溃）**，此时再查 `runtime/edge_profile/Crashpad/reports/` 最新转储与时间戳；
- `pages` 计数始终为 1 且 URL 正常 → 可排除多页/隧道干扰。

---

## 7. 下一步最小修复方案（阶段二执行，本阶段未改动）

按“改动最小、不碰 Adapter/搜索/GUI 结构”原则：

1. **修时序竞态（RC-1，最高优先）**：把 `open_tju` 的固定 `wait_for_timeout(2500)`
   改为“等待门户就绪/跳转稳定”，例如在 `check_tju_auth` 前轮询若干秒：
   - 等待 `page.wait_for_url(r"/login")` 与“已授权/登录”文本出现二选一，超时再 unknown；
   - 或 `page.wait_for_load_state("networkidle")`（加超时上限）后再读 body。
   （`portal.py` 已有 `wait_for_url` + 文本等待的先例，可复用思路。）

2. **修释放逻辑（RC-2）**：`check_auth` 在状态为 `login_page` 时**不** `_release()`
   （保持 Edge 供用户完成官方登录，与 `open_detail` 成功后保持运行一致），
   或改为检查后保持运行、由下一次任务/关闭时统一释放。

3. **补日志（必须，配合第 6 节）**：`check_tju_auth` 的 except 分支改为
   `logger.exception`；返回 `unknown` 前记录 `url/title/pages/body 前 150 字符`。

4. **区分登录失败与授权失败**：为 `unknown` 增加可诊断信息（如异常类型），
   或让 `check_auth` 对读取异常单独上报，不再全部折叠成 “无法确定”。

5. **补测试**：为 `is_login_url("https://p.lib.tju.edu.cn/login")`、
   `check_tju_auth` 各分支、`worker.check_auth` 生命周期补单元测试。

6. **（可选，与今天现象无关但建议）** 跟踪 Crashpad 转储：若复现时再次出现
   `0xc0000005` 新转储，再单独排查 Edge 进程崩溃。

---

*审计完成。本文件为只读审计产物；所有代码改动留待阶段二。*
