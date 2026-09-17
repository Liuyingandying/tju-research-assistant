# 天津大学资源接入审计

日期：2026-09-01。方式：真实受控 Edge 会话（`runtime/edge_profile`，TJU 已授权），
通过图书馆门户逐资源点击"进入"探测。未修改任何源码。原始证据：
`runtime/audit_portal.json`、`runtime/audit_resources4.json`、`runtime/audit_weipu.json`（均 gitignored）。

## 门户结构与登录状态

- **登录状态**：`logged_in`（专用 Profile 复用，直接"已授权"，无需重复登录）。
- **门户 URL**：`https://p.lib.tju.edu.cn/`（AngularJS 单页）。
- **导航分类**：首页 / 中文资源（44 项）/ 外文资源（43 项）/ 免费资源（5 项）/ 资源检索 / 常用资源。
- **入口机制**：每个资源卡片带"进入"按钮（`span.intoitem2`，`ng-click=hrefDetail(item)`），
  点击后 **新标签** 打开 TJU 代理页：
  - 形式 A（CNKI）：`https://p.lib.tju.edu.cn/?__proto__=https&__host__=www.cnki.net`
  - 形式 B（其余）：`https://<固定随机前缀>-s.p.lib.tju.edu.cn/`（每资源一个随机子域）
- **关键技术发现（实现 SourceAdapter 必须知道）**：
  1. 代理"进入"打开新标签**很慢**（需建立隧道，约 10-30 秒），必须用 Playwright 官方
     `context.expect_page()` 等待；`time.sleep` 轮询拿不到新页面事件（sync API 事件泵机制）。
  2. 代理偶发 **502 Bad Gateway**（隧道建立失败），`reload` 一次通常可解。
  3. 代理子域随机 → **资源入口无法硬编码 URL**，必须从门户点击导航获得。

## 当前已支持

CNKI（`sources/cnki.py`，直连 `www.cnki.net` + 授权 Cookie；门户代理入口 `?__host__=www.cnki.net` 亦可达）。

## 逐资源探测结果

| 资源 | 门户分类 | 落地 URL | 授权 | 可见搜索框 | 评估 |
| --- | --- | --- | --- | --- | --- |
| 中国知网 CNKI | 中文+外文 | 代理 `?__host__=www.cnki.net` | 代理直入 | `textarea#txt_SearchText` | ✅ 已实现 |
| 万方数据知识服务平台 | 中文 | `poaktpgxsczssmtfkgahrnxssjtsvgqktpgxscff-s.p.lib.tju.edu.cn` | 代理直入 | `input#search-input`（"海量资源, 等你发现"） | ✅ 适合 |
| EI Engineering Village | 外文 | `poaktpgxschflpgwiuibxbmcosllalfsrccisd-s.p.lib.tju.edu.cn/app/search/quick/` | 代理直入 | Quick Search 输入框 | ✅ 适合 |
| IEEE Xplore | 外文 | `bwihxdcuvvklgwkgplesvnxssjtshjk-s.p.lib.tju.edu.cn/Xplore/home.jsp` | 代理直入 | `input[type=search]` | ✅ 适合 |
| Web of Science | 外文 | `pwfrfgtoiefwmauhvrlqcgvzysylalfsrccir-s.p.lib.tju.edu.cn/wos/woscc/smart-search` | 代理直入 | `input#composeQuerySmartSearch` | ✅ 适合（页面重，结果页结构需专项审计） |
| 维普期刊数据库 | 中文 | `jaodnvkhtirdhxoatktpgxscfgr-s.p.lib.tju.edu.cn` | 代理直入 | **40 秒后页面仍空白**（SPA 未渲染） | ⚠️ 待定 |

**"无需二次登录"的判定依据**：各资源落地页**直接是功能页/检索页**（搜索框可见），
而非认证页；`looks_like_login=True` 是页面导航栏含"登录"链接的文本误报，不代表需要重新认证。

## 可直接接入

**资源**：万方、EI Engineering Village、IEEE Xplore、Web of Science

**原因**：全部经 TJU 代理直入（无二次登录）；落地页即可见、可定位的搜索入口；
搜索框均为主流平台标准结构，DOM 稳定性预期良好。

**技术方案**（复用现有架构，不新增框架）：
1. 新增 `PortalNavigator`（或在 BrowserSession 上加方法）：打开 `p.lib.tju.edu.cn`
   → 切分类 tab → `expect_page()` 内点击目标资源"进入" → 返回落地页（Page）。
   资源名 → 门户卡片匹配用"包含匹配"（名称含 TALIS/学院订购后缀）。
2. 每资源一个 `SourceAdapter`（如 `WanfangAdapter`），构造时接收落地页所属会话，
   `search(keyword, count)` 内自行定位该资源搜索框 → 输入 → 检索 → 解析结果表
   （复用 CNKIAdapter 的模式与边界：单次、单页、低频、不下载）。
3. 502 处理：落地页 title 含 502 时 reload 一次。
4. 首个 Adapter 开发前，先做该资源结果页 DOM 专项审计（搜索后的结果列表结构）。

## 可作为跳转入口

**资源**：Web of Science（备选定位）

**原因**：代理直入可行，但 WOS 是重 SPA，检索页与结果页均动态渲染、加载慢；
Smart Search 输入框已确认，但结果列表结构与稳定性需要专项审计后才值得投入。

## 暂不考虑

**资源**：维普

**原因**：经代理进入后 40 秒页面仍空白（SPA 未渲染，无任何可见输入框），
疑似 TJU 代理对该站点的兼容性问题。稳定性无法确认，自动化风险高。
待代理渲染正常后重新评估。

**资源**：门户中其余 80+ 资源（北大法宝、SciFinder、ProQuest、EBSCO、超星系等）

**原因**：超出课程项目范围；按需求优先级（中文综合库 + 核心外文库）不纳入。

## 推荐下一阶段开发顺序

1. **万方**（中文综合库，与 CNKI 互补性最强；搜索框 `#search-input` 已确认；
   需先做结果页 DOM 审计）。
2. **IEEE Xplore**（外文，落地页/搜索框标准；需先审计结果页结构）。
3. **EI Engineering Village**（Quick Search 页面清晰；需审计结果页）。
4. **Web of Science**（价值高但 SPA 重，放在最后；先专项审计结果页）。
5. **PortalNavigator 公共组件**先行（所有非 CNKI 资源都依赖门户导航获取落地页）。

## 审计方法备注

- 门户"进入"新标签等待必须用 `context.expect_page()`（本次审计中
  `time.sleep` 轮询与"取最新非门户页"两种方式均产生系统性错误结论，已用
  audit2/3/4 三轮验证该机制）。
- 全程低频：每资源仅 1 次点击进入，无批量、无下载、无认证绕过。
