# DESIGN.md —— 信息自动检索整理系统（TJU Research Assistant）设计说明

- 版本：v0.18 RC（Release tag：`v0.18-rc`）
- 形态：Windows 桌面应用（PySide6），PyInstaller 打包，双击运行
- 作者：个人独立项目

## 1. 系统目标

面向正常授权用户的科研文献检索与分析工具：输入研究方向，系统在 CNKI、万方、
IEEE Xplore（另含专利与校内新闻）上执行多源检索，自动完成查询翻译、融合去重、
排序，并可选生成带证据链的 AI 结构化摘要与调研报告（Markdown / Word / PDF 导出）。

合规边界（设计底线）：只通过受控 Edge 浏览器复用学校统一认证会话；不绕过
验证码与访问控制；不批量抓取；不保存账号密码；API Key 不进入代码仓库。

## 2. 总体架构

五层单向依赖，models 横切共享：

```
UI 层（MainWindow / Dashboard / 论文库窗口 / 设置对话框；检索与分析跑 QThread Worker）
  ↓ 信号槽（QueuedConnection）
检索编排层（SearchService：查询扩展 → 概念切分 → 跨语言翻译 → 按库布尔查询
           → 多源并行检索 → ResultMerger 融合去重 → RankingService 排序）
  ↓ 统一 Adapter 接口
数据源适配层（CNKI / 万方 / IEEE Xplore / 专利 / 校内新闻；DOM 解析 + 正则回退）
  ↓
受控会话层（Playwright persistent context + 本机 Edge 专用 Profile）
```

AI 层（SummaryService / ReportService / Evidence 证据抽取）只依赖 models 与
配置，与浏览器层完全解耦；知识库层（LibraryStore / ResearchProfile）为本地
JSON 存储，与代码严格分离。

## 3. 关键设计决策

1. **适配器模式多源接入**：三库页面结构完全不同，Adapter 统一接口后新增数据源
   不改主流程；所有 DOM 解析带正则回退分支，应对页面改版。
2. **跨语言查询管线**：中文研究词经 ConceptSegmenter（领域词典最长匹配切分）
   → Terminology（中英术语映射）→ QueryBuilder（按库语法生成布尔串），解决
   中文关键词在英文库零结果问题。
3. **三重去重**：DOI → URL → 标题标准化，逐级合并多源结果。
4. **Evidence 证据链**：AI 摘要的每条结论必须挂检索证据；证据不足回退纯本地
   规则摘要（offline_summary），保证无 API Key 时功能不中断。
5. **凭据安全**：API Key 存系统凭据库（keyring），回退 DPAPI 加密文件；配置
   JSON 保存时显式剥离 api_key/token/authorization 字段；密钥只在内存注入
   Authorization 头，不落盘、不进日志。
6. **用户数据隔离**：开发态数据在仓库 runtime/、data/（gitignored）；打包态
   写 `%LOCALAPPDATA%\TJU_Info_Retrieval`；测试经 `TEST_USER_DATA_ROOT` 隔离，
   永不触碰真实用户数据。
7. **发布工程**：PyInstaller onedir 打包 + 冻结态冒烟自检（真实构造主窗口、
   demo 检索、响应式几何断言、干净关闭）+ Release Guard 发布安全扫描（六类
   敏感数据、dev/release 双作用域）。

## 4. 比赛专属 API 集成（agent2026）

### 4.1 接入方式

智能体大赛为每个参赛项目分配专属 OpenAI-compatible endpoint。本项目以
**注册表条目**方式接入（`services/ai_provider_registry.py`），不新增第二套
调用链：

| 字段 | 值 |
|---|---|
| Provider id | `agent2026` |
| default_base_url | `https://ai.tju.edu.cn/api/agent2026/gitlab-102-agent2026-qwen-agent` |
| default_model | `tju-llm` |
| 最终请求路径 | `{base_url}/chat/completions`（与其他 Provider 共用同一 endpoint 模板） |
| 凭据 | 与既有机制一致：环境变量 / keyring / 用户配置，不写入源码 |

说明：endpoint 路径中的 `gitlab-102-agent2026-qwen-agent` 是比赛平台生成
专属地址时的原始项目标识，**与仓库当前名称无关**，必须原样使用。

### 4.2 行为

- 设置页「服务类型」下拉选择「天津大学 LLM（智能体大赛专属）」后，配置层
  （APIConfigManager）按注册表默认值装配 base_url / model，实际请求由既有
  `OpenAICompatibleProvider` 发出，最终 URL 为
  `https://ai.tju.edu.cn/api/agent2026/gitlab-102-agent2026-qwen-agent/chat/completions`；
- 普通 Provider（tju_llm=`/api/v3`、DeepSeek、自定义）行为不变，回归测试覆盖；
- 认证仍为 Bearer 形式的 Authorization 认证头（密钥仅内存注入），key 经既有 credential_store 链路
  （keyring → DPAPI → 内存），日志与错误信息中不出现明文。

### 4.3 验证

- 单元测试（`tests/test_agent2026_provider.py`）：注册表路由、base_url 不含
  `/api/v3`、最终 chat completions URL、model=tju-llm、API Key 不进日志/错误
  信息、普通 Provider 回归；
- 真实连通性：经比赛 endpoint 发送最小消息（"你好"），HTTP 200 且模型正常
  应答（记录于验收记录，不记录 Authorization 内容）。

## 5. 质量保障

- 测试 1,657+ 项（tests/ 25,005 行，超过源码行数），用户数据目录隔离；
- 冻结态打包冒烟：真实构造主窗口、demo 检索、响应式几何断言、干净关闭；
- Release Guard：发布物六类敏感数据扫描（登录态/凭据/个人数据/开发机路径），
  dev 与 release 双作用域，严重度分层；
- 文档：docs/ 131 份设计与验收记录，关键攻坚（授权时序、万方 DOM、GUI 零结果
  插桩）均有可追溯报告。
