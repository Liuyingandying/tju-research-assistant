# 开发指南

## 如何新增一个数据库（例如 IEEE）

原则：**新增数据源不需要修改 GUI 主流程**。GUI 只与 `SearchService` 交互，
`SearchService` 根据请求选择对应 Adapter。

### 步骤

1. 在 `src/tju_info_retrieval/sources/` 新增 `ieee.py`：

```python
from tju_info_retrieval.browser.session import BrowserSession
from tju_info_retrieval.models.result import SearchResult
from tju_info_retrieval.sources.base import SearchError, SourceAdapter


class IEEEAdapter(SourceAdapter):
    database_name = "IEEE"

    def __init__(self, session: BrowserSession) -> None:
        self._session = session

    def search(self, keyword: str, count: int = 10) -> list[SearchResult]:
        # 1. 打开 IEEE 页面（复用 session.open_url）
        # 2. 定位搜索框/按钮（先通过真实页面做 DOM 审计，找到稳定 selector）
        # 3. 输入关键词、点击检索、等待结果页
        # 4. 解析当前第一页前 count 条，返回 list[SearchResult]
        raise NotImplementedError
```

2. 在 `services/search_service.py` 中按需实例化并路由：

```python
# 示例：根据 query.databases 选择 adapter
if "IEEE" in query.databases:
    adapter = IEEEAdapter(self._session)
```

3. 在 GUI 中启用对应数据源复选框（`main_window.py` 的"数据来源"区）。

### 关键约束

- 先做真实页面 DOM 审计，找到稳定 selector 再编码；不要堆几十个 selector 猜测。
- 遵守与 CNKI 相同的边界：单次检索、单页、低频、用户主动发起；不下载全文、
  不翻页、不绕过授权、不读取 Cookie/token。
- 解析逻辑放在 Adapter 内，不放进 GUI 或 Service。
- 每个新 Adapter 配对应的 `tests/` 测试（用 synthetic HTML fixture 测解析）。

## 跨语言检索链路（v0.8.x）

中文科研关键词到 IEEE Boolean Query 的纯逻辑链路：

```
输入（中文关键词）
  → concept_segmenter.ConceptSegmenter.segment()   最长匹配切分
  → terminology.TERMS                                中文概念 → 英文等价词
  → query_translator.QueryTranslator.translate_concepts()  概念分组
  → query_builder.QueryBuilder.build()              按数据库语法组装 query
  → Adapter.search(query)                            真实检索
```

- `terminology.TERMS` 是唯一的领域词典入口。新增概念时在
  `src/tju_info_retrieval/services/terminology.py` 的 `TERMS` 增加条目
  `{"中文词": {"en": ["英文1", "英文2", ...]}}`，并补对应单测
  （`tests/test_terminology.py`）。
- `ConceptSegmenter` 用 `TERMS` 的键做最长匹配切分；新增词条会自动被切分逻辑使用。
- `QueryBuilder` 只消费 `translate_concepts` 的概念分组，不感知词典细节；
  新增数据库语法时扩展 `_build_boolean` / `_build_chinese` / `_fallback`。
- 英文源在 `QueryTranslator.ENGLISH_SOURCES` 声明（当前 `IEEE Xplore`）；
  中文源（CNKI/万方）在 `QueryBuilder.CHINESE_SOURCES` 声明，透传原串。

## 新增导出格式

在 `services/export.py` 添加函数（参考 `export_csv`），并在
`main_window._on_export` 中按扩展名/过滤器分发。导出字段固定为文献元数据。

## 测试

```bash
python -m pytest -v
```

- 模型/导出/GUI 用纯单元测试。
- CNKI DOM 解析用极小 synthetic HTML fixture（模拟 `table.result-table-list`），
  不保存真实授权页面 HTML。
- 真实网络行为必须人工 dogfood，不用 mock 冒充。

## 提交规范

- 每个阶段测试真实通过后再 commit。
- 不 push；不接入流萤；不引入 AI。
