# 架构说明

本项目采用"轻量分层 + 复用已验证 PoC 逻辑"的结构，避免过度抽象。

## 目录结构

```
src/tju_info_retrieval/
├─ models/        # 数据模型
├─ browser/       # 受控浏览器会话
├─ sources/       # 数据源适配器
├─ services/      # 检索编排 / 导出
└─ ui/            # 桌面界面（主线程 + 后台 worker）
```

## 数据模型（models）

- `SearchResult`：一条检索结果。字段 `rank / title / authors / source / year /
  detail_url / document_type / database`。预留字段（abstract / keywords / doi /
  core_technology / key_findings）本轮不实现，不进入详情页获取。
- `QueryRequest`：一次检索请求。`research_direction` 作为实际检索词；
  `expansion_direction / person_name / person_org` 进入模型但本轮不改变检索。

## BrowserSession（browser/session.py）

管理受控 Edge 生命周期，复用 PoC-0 已验证的启动配置：

- `start()`：用 `build_launch_kwargs`（本机 Edge + `runtime/edge_profile` +
  `chromium_sandbox=True`）启动 persistent context（真实窗口）。
- `open_tju()` / `open_cnki()` / `open_detail(url)`：在同一会话内导航。
- `check_tju_auth()`：返回 `logged_in / login_page / unknown`。
- `is_alive()`：检测浏览器是否被人工关闭，供 worker 重建会话。
- `shutdown()`：正常关闭并保留 Profile。

原则：只封装浏览器能力，不含任何检索/解析逻辑；不访问默认浏览器 Profile；
不输出 Cookie/token。

## 数据源适配器（sources/）

- `base.SourceAdapter`：接口，`search(keyword, count) -> list[SearchResult]`。
- `cnki.CNKIAdapter`：CNKI 实现。复用 PoC-1 已验证的 selector
  （`#txt_SearchText`、`div.search-btn`、`table.result-table-list`）与解析函数
  （`extract_row`、`wait_for_result_page`），补充 `document_type` 提取与
  `SearchResult` 转换。
- `ieee.IeeeAdapter`：IEEE Xplore 实现。经 `PortalNavigator` 落地页搜索，
  解析器 `_parse_results` 用结构化 DOM（`.description a`、`.publisher-info-container`）
  提取 year / venue / document_type，缺失时正则回退兜底（v0.8.2）。

## 服务层（services/）

- `search_service.SearchService`：编排"授权 gate → 词扩展 → 概念翻译 → 查询构建 →
  多源检索"，通过 `status` 回调上报进度。
- `query_expansion.QueryExpansionService`：同义词 + 方向限定（纯规则）。
- `terminology.TERMS`：内置领域词典，中文科研概念 → 英文等价词。
- `concept_segmenter.ConceptSegmenter`：按领域词典最长匹配切分中文连续科研词。
- `query_translator.QueryTranslator`：用 ConceptSegmenter 切分并把中文概念映射为
  英文概念组（未命中保持原样）。
- `query_builder.QueryBuilder`：按数据库语法把概念组组装为检索串（IEEE Boolean：
  组内 OR / 组间 AND / 多词短语加引号；CNKI/万方透传）。
- `export`：`export_json / export_csv / export_markdown`，字段固定为文献元数据。

## UI（ui/）

- `worker.BrowserWorker`：QObject，被移动到独立 QThread。**所有 Playwright 调用
  都在 worker 线程内**，避免阻塞主线程。通过 Qt 信号上报状态/结果/错误。
- `main_window.MainWindow`：主窗口持有请求信号（主线程），连接到 worker 的槽
  （worker 线程），Qt 自动使用 QueuedConnection 跨线程投递。

## 线程模型

```
主线程 (MainWindow)                worker 线程 (BrowserWorker)
   │  check_auth_requested ────────►  check_auth()
   │  search_requested ────────────►  search(query)
   │  open_detail_requested ───────►  open_detail(url)
   │  shutdown_requested ──────────►  shutdown()
   ◄── status_changed ────────────────│
   ◄── search_done / search_failed ───│
```

主线程只做 UI；浏览器操作全部在 worker 线程顺序执行。
