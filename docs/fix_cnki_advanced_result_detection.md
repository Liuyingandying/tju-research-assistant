# 修复报告 — CNKI 高级检索结果页检测失败（Phase 8.6）

- 日期：2026-09-04
- 问题：GUI 运行时报"CNKI 高级检索失败，回退普通搜索：未检测到 CNKI 高级检索结果页"
- 范围：仅 CNKI Advanced → result detection；普通搜索/QueryRequest/FilterService/MetadataPipeline/Ranking/UI 均未改动

---

## 1. 根因

`poc_cnki_search.py::wait_for_result_page()`（普通搜索判定，**未改动**）只认
`page.url` 含 `defaultresult`：

```python
if "defaultresult" in page.url:   # poc_cnki_search.py:160
    return page
```

`cnki.py::_advanced_search` 复用了该函数判定高级检索成功。但 kns8s 高级检索
提交成功后 **URL 不跳转**（停留 `https://kns.cnki.net/kns8s/AdvSearch`，结果在
SPA 内渲染 `table.result-table-list`）→ 30s 轮询必然超时返回 None →
抛 `SearchError("未检测到 CNKI 高级检索结果页。")` → GUI 观察到的回退普通搜索。

真实会话复核（修复后执行，两次独立会话）：

```
final_url: https://kns.cnki.net/kns8s/AdvSearch   ← 提交成功但 URL 不变
result_count: 20 / table_exists: true             ← 结果实际已渲染
```

## 2. 修改文件

| 文件 | 改动 |
|---|---|
| `src/tju_info_retrieval/sources/cnki_advanced.py` | 新增 `wait_for_advanced_result_page()`（A/B/C 优先级判定）+ 辅助 `_has_result_rows()` / `_has_result_state_change()`；复用 poc 已验证的 `RESULT_TABLE_SELECTOR` 与 `is_cnki_page` |
| `src/tju_info_retrieval/sources/cnki.py` | `_advanced_search` 中 `wait_for_result_page` → `wait_for_advanced_result_page`（import + docstring）；错误信息保持原文 |
| `tests/test_cnki_advanced_search.py` | 5 处高级路径用例的 patch 目标同步改为 `wait_for_advanced_result_page`（普通路径/fallback 用例不变） |
| `tests/test_affiliation_source_verified.py` | `_run_advanced` 的 patch 目标同步更新 |
| `tests/test_cnki_advanced_result.py`（新增） | 本次修复的专项测试（13 个用例） |
| `poc_verify_advanced_wait.py`（新增） | 真实会话复验脚本（复用 E2E 会话组装，直接调用修复后的生产函数） |

## 3. 新检测逻辑（不依赖 URL，优先级 A/B/C）

```python
def wait_for_advanced_result_page(context, timeout_s=30.0, poll_interval=0.5):
    # 仅检查 cnki.net 页面，任一命中即返回承载结果的 page：
    # A. table.result-table-list 出现          （主判定，已验证容器）
    # B. 结果区域出现有效数据行 tr td.name a   （容器 class 变化时的宽松兜底）
    # C. 结果统计条/分页条出现                  （高级页状态变化兜底）
    # 超时返回 None（调用方抛 SearchError，语义不变）
```

普通搜索 `_existing_search` 与 `poc_cnki_search.py` **原样保留**。

## 4. pytest 结果

```
专项 + 受影响文件：  57 passed, 4 subtests passed
全量回归：          555 passed, 20 subtests passed (60.41s)
```

新增用例覆盖（`tests/test_cnki_advanced_result.py`）：
- URL 不变（AdvSearch）但 table 出现 → 成功（A）
- defaultresult URL → 普通 `wait_for_result_page` 原逻辑成功 + 新判定兼容命中
- 有效数据行兜底（B）/ 统计条兜底（C）→ 成功
- 超时无结果 → `None` → `_advanced_search` 抛 `SearchError`
- 高级失败（超时）→ fallback 普通搜索行为保持
- 非 CNKI 页面（含同名 table）不误判
- 端到端：`_advanced_search` 在 SPA 渲染 table 后成功返回结果

## 5. 真实 E2E 重新验证：**已执行，通过**

| 验证 | 方式 | 结果 |
|---|---|---|
| 全链路闭环 | `poc_cnki_advanced_e2e.py`（真实 Edge + TJU 授权 + 真实 CNKI） | ✅ entered/fill/submit 全成功，结果 20 条，`final_url` 停留 AdvSearch，`table_exists=true`，失败分类"OK：全链路可用" |
| 修复函数直接验证 | `poc_verify_advanced_wait.py`（真实会话中调用生产函数 `wait_for_advanced_result_page`） | ✅ 提交后 URL 停留 AdvSearch（无 defaultresult）的情况下，**0.02s 命中**，`table=True`，行数 21（报告：`runtime/cnki_advanced_wait_verify.json`） |

结论：根因（URL 不跳转导致旧判定超时）被真实会话实证；修复后的判定在真实页面
0.02s 命中，高级检索不再误回退普通搜索。
