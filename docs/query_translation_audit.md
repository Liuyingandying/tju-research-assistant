# v0.8.0 跨语言检索层（Query Translation Layer）接入审计

> 只读审计 · 未修改任何代码 / 未新增源码文件 / 未运行改变项目状态的操作。
> 日期：2026-09-02。基线：`cb52009`（v0.7.1 release，工作树干净，210 passed）。
> 目标：为“中英文跨语言检索层”确定最佳接入位置（只设计，不实现）。

---

## 1. 当前检索链路

### 1.1 完整调用链（含文件与函数）

```
GUI 输入（QLineEdit input_direction，如“太赫兹”）
  ↓
main_window.py:187  _on_search()
  │  读 input_direction.text()、combo_expansion.currentText()、勾选数据源
  │  构建 QueryRequest(research_direction, expansion_direction, databases,
  │                    result_count, sources)          models/query.py:21-27
  │  query.validate()                                   models/query.py:29-36
  ↓
main_window.py:228  search_requested.emit(query)         [Signal(object)]
  ↓  Qt QueuedConnection 跨线程投递
ui/worker.py:97     @Slot(object) search(query)          （worker 线程）
  ↓
ui/worker.py:102    service.search(query, status=self.status_changed.emit)
  ↓
services/search_service.py:43  SearchService.search(query)
  │  1) check_tju_auth()                                search_service.py:39-41
  │  2) open_cnki()
  │  3) expanded = self._expansion.expand(direction, expansion_direction)
  │        services/query_expansion.py:49  QueryExpansionService.expand
  │        → ExpandedQuery（含 query_string 布尔串）     query_expansion.py:34-43
  │  4) for source_name in sources:
  │        adapter_cls = self._registry.get(source_name)
  │        adapter = adapter_cls(self._session)
  │        partial = adapter.search(expanded.query_string, query.result_count)
  │        ★ 同一个 expanded.query_string 被喂给所有数据源（search_service.py:80）
  │  5) ResultMerger.merge + RankingService.rank
  ↓
返回 list[SearchResult] → worker → search_done → main_window _on_search_done → 表格
```

### 1.2 各 Adapter 实际发送给数据库的 query

| Adapter | 文件 | 检索入口 | 收到并发送的 keyword |
| --- | --- | --- | --- |
| CNKIAdapter | `sources/cnki.py:29` | 填充 `SEARCH_INPUT_SELECTORS` 输入框 → 点击 | `expanded.query_string`（中文布尔串，匹配 CNKI 语法）✅ 合适 |
| WanfangAdapter | `sources/wanfang.py:35` | `PortalNavigator` → 万方 `#search-input` → 点“检索” | 同一中文布尔串（万方对 CNKI 布尔语法兼容性存疑）⚠️ 待验证 |
| IeeeAdapter | `sources/ieee.py:34` | `PortalNavigator` → IEEE `input[type=search]` → Enter | **同一中文布尔串（英文库收到中文）** ❌ 效果差，是本层要解决的痛点 |

> 关键事实：**三个 Adapter 收到完全相同的 `expanded.query_string`**
> （`search_service.py:80`）。目前不存在“按数据源定制 query”的能力。

### 1.3 每一环节的数据结构

| 环节 | 数据结构 | 定义位置 |
| --- | --- | --- |
| GUI → worker | `QueryRequest`（dataclass: research_direction / expansion_direction / person_name / person_org / databases / result_count / sources） | `models/query.py:10-27` |
| 扩展 | `ExpandedQuery`（original / expansion_direction / terms / filters / query_string / notes） | `services/query_expansion.py:34-43` |
| Adapter 接口 | `SourceAdapter.search(keyword: str, count: int) -> list[SearchResult]` | `sources/base.py:16` |
| 结果 | `SearchResult`（dataclass） | `models/result.py` |

---

## 2. 已有查询扩展能力

### 2.1 已存在能力

- **查询扩展模块**：`QueryExpansionService`（`services/query_expansion.py`，v0.3）。
- **关键词扩展逻辑**：水平扩展（同义词）+ 垂直扩展（理论/应用/综述方向限定词）。
- **中英文映射（雏形）**：`SYNONYM_THESAURUS`（query_expansion.py:16-21）已含少量中→英映射，如
  `太赫兹 → [THz, 太赫兹波, Terahertz]`、`深度学习 → [DNN, 神经网络]`、`知识图谱 → [Knowledge Graph]`。
- **布尔组装器**：`_build_query_string`（query_expansion.py:91-98），产出 `(A OR B) AND (C OR D)`。
- **AI 接口预留**：**无**。全仓（`src/`、`scripts/`）grep 无 `llm/openai/translate/翻译` 等代码；
  现有纯逻辑层刻意保持“规则式、确定性、本地、无 AI、无网络”（query_expansion.py 模块注释）。

### 2.2 可复用组件

| 组件 | 复用方式 |
| --- | --- |
| `SYNONYM_THESAURUS` | 作为中英**概念词典的种子**（扩展成 `CONCEPT_DICT`） |
| `_build_query_string` | 复用作**英文布尔组装器**（或抽取为公共组装函数） |
| `ExpandedQuery.terms` | 翻译层可直接消费已拆出的同义词项（不必重新切分已命中词） |
| `ExpandedQuery.query_string` | 中文库（CNKI/万方）默认透传，作为翻译的 fallback |
| `AdapterRegistry` | 按源名分发查询策略的天然挂载点 |

### 2.3 不建议重复实现

- **布尔组装**（OR/AND 逻辑）——直接复用 `_build_query_string`。
- **同义词去重/透传哲学**（未命中保持原词、不猜测）——延续 QueryExpansion 的确定性风格。
- **不做大而全的机器翻译**——规则词典先行，与现有架构一致。

---

## 3. 翻译层接入方案比较

目标示例（用户输入“太赫兹通感一体化”）：

```
CNKI    ：太赫兹 通感一体化
IEEE    ：terahertz  /  THz  /  integrated sensing and communication  /  ISAC
```

| 维度 | 方案 A：GUI 层翻译 | 方案 B：SearchService 前加 QueryTranslator | 方案 C：每个 Adapter 自己翻译 |
| --- | --- | --- | --- |
| **修改范围** | 改 `main_window.py:_on_search`（构建 QueryRequest 前翻译） | 新增 `services/query_translator.py`；`SearchService.search` 在 expand 之后、adapter 循环之前插入翻译，按源传参 | 改 `cnki.py` / `wanfang.py` / `ieee.py` 三个 Adapter 的 `search` |
| **架构影响** | 违反分层（GUI 只做 UI、业务在 worker 线程，见 `docs/architecture.md`）；翻译逻辑（词典/切分）混入主线程；阻塞 UI 或需跨线程传参 | 最小、符合分层；翻译是纯逻辑 service，可单测；Adapter 签名 `search(keyword, count)` **不变**，只是 keyword 变成“该源的检索串” | 翻译逻辑分散三处、重复；Adapter 与词典强耦合；破坏 Adapter 作为稳定边界（此前约定“不修改 Adapter”） |
| **测试影响** | GUI offscreen 测试需新增翻译断言；业务逻辑难覆盖 | 现有 210 测试**保持绿色**（见第 6 节）：单源测试断言 adapter 收到 `expanded.query_string`，若翻译层默认对中文库/未知源**透传** query_string，则断言不变；新增 translator 纯逻辑测试 | 需改 3 个 Adapter 测试（test_cnki/wanfang/ieee 断言 `search` 输入）；改动面最大 |
| **长期扩展性** | 差：每加一个源改 GUI；翻译与 UI 耦合 | **好**：per-source 查询策略集中管理；新源只需注册策略；可平滑升级为 LLM（替换策略实现） | 差：每新源重写翻译；无统一词典/策略 |

**结论**：方案 B 是唯一在“最小修改 + 分层正确 + 测试不破坏 + 可演进”四者上同时成立的方案。

---

## 4. 推荐方案（方案 B）

### 4.1 新增模块（仅设计，不实现）

```
src/tju_info_retrieval/
    services/
        query_translator.py        # 新增：QueryTranslator（纯逻辑、可单测）
```

### 4.2 输入 / 输出定义

- **输入**：`ExpandedQuery`（`original` / `terms`）+ 本次 `sources` 列表。
- **输出**：`dict[str, str]`——数据源名 → 该源检索串。

```
translate(expanded, sources) -> {
    "CNKI"       : "太赫兹 通感一体化",                          # 中文（默认透传/中文串）
    "万方"       : "太赫兹 通感一体化",
    "IEEE Xplore": '("terahertz" OR "THz") AND '
                   '("integrated sensing and communication" OR "ISAC")'
}
```

### 4.3 核心设计

- **概念词典**：`CONCEPT_DICT: dict[str, list[str]]`（中→英同义词），
  以 `SYNONYM_THESAURUS` 为种子扩充，如 `"通感一体化": ["integrated sensing and communication", "ISAC"]`。
- **中文短语切分**：对无空格中文输入用概念词典做**最长匹配切分**（`太赫兹 | 通感一体化`）；
  未命中片段原样透传（作为中文短语透传给中文库；对英文库以 note 标注“未翻译”）。
- **英文布尔组装**：复用 `_build_query_string` 逻辑生成 `("A" OR "B") AND ("C" OR "D")`。
- **策略分发**：`QueryTranslator` 内置 per-source 策略表（默认=透传 query_string；
  `IEEE Xplore`=英文串；未知源=透传，不猜测）。
- **SearchService 最小接入**（仅改一处循环内传参）：

```python
expanded = self._expansion.expand(direction, query.expansion_direction)
queries  = self._translator.translate(expanded, sources)          # 新增
...
partial = adapter.search(queries.get(source_name, expanded.query_string),
                         query.result_count)                       # 按源传参
```

---

## 5. v0.8.0 实施计划（只设计）

| 里程碑 | 内容 | 验收 |
| --- | --- | --- |
| M1 骨架 | 新增 `QueryTranslator`（默认全部透传）+ 接入 SearchService；无行为变化 | 现有 210 测试保持绿色 |
| M2 词典与组装 | `CONCEPT_DICT`（先覆盖示例“太赫兹通感一体化”与现有同义词表）+ 英文布尔组装器 + 切分函数 | 纯逻辑单测（离线、确定性） |
| M3 IEEE 策略 | 对 `IEEE Xplore` 应用英文串；真实验证（TJU 授权环境，`p.lib.tju.edu.cn`） | 真实检索空结果率下降；落地页可解析 |
| M4（可选）LLM 预留 | 定义 `TranslatorStrategy` 抽象；LLM 作为**可选策略**（需网络/密钥/确定性评估），默认仍规则 | 接口存在、默认路径不变 |
| M5 文档/验收 | README 更新 + v0.8 设计文档 + 验收报告（复用 `scripts/run_acceptance_check.py`） | 验收通过 |

---

## 6. 风险分析

| 风险 | 等级 | 说明 |
| --- | --- | --- |
| **影响现有 210 测试** | 低 | 方案 B 默认透传 `expanded.query_string`，`test_search_service.py:56/61/71` 的单源断言（adapter 收到 `Q`/`量子计算`）不变；多源测试（S1/S2）不断言具体 keyword。仅当未来改变 CNKI/万方默认串时才需同步这 3 处断言。 |
| **影响 Adapter** | 无 | 三 Adapter 签名与内部逻辑零改动；只改变传入的 keyword 内容。 |
| **数据库专用词典** | 中（必需资产） | IEEE 需英文词、CNKI/万方需中文词。中英概念词典是核心资产，需持续人工维护；最小集先覆盖目标场景，未命中透传 + note。 |
| **LLM API** | 不需要（v0.8） | 规则词典先行，与 `QueryExpansionService` 同哲学（确定性、可测、无网络）。LLM 后置为可选策略，避免网络/密钥/结果不确定性。 |
| **规则词典先行** | 推荐 | 先做规则（离线、单测、可回滚），再评估是否引入 LLM。 |
| **中文切分** | 中（主要难点） | 无空格中文长词需词典最长匹配切分；未命中词对英文库效果差 → 以 note 提示并持续扩词典。 |
| **跨语言去重** | 中 | IEEE 英文标题与 CNKI 中文标题不重复，`ResultMerger` 按 title 去重会**失效**。v0.8 需决定：按 DOI/其他稳定标识跨语言去重，或不做跨语言去重（两库结果各自保留）。 |
| **PortalNavigator 开销** | 低（不改变） | IEEE/万方每次 search 重新 `open_portal`+`open_resource`（隧道 10-30s），翻译层不改变此行为；多语言高频检索的会话内复用留待后续。 |

---

*审计完成，未修改任何代码。下一步建议：按第 5 节 M1→M3 实施方案 B。*
