# TJU Research Assistant（天津大学信息自动检索整理系统）

AI 辅助科研文献分析系统 —— 面向**正常授权用户**的桌面应用（PySide6）。
系统复用天津大学图书馆授权会话（受控 Microsoft Edge + 专用持久 Profile），
在授权环境下对多个学术数据库执行**正常、低频、用户主动发起**的检索，
并提供 AI 增强论文分析、科研画像、论文库管理与 AI 调研报告生成能力。

> 本项目**不是**批量下载器，**不绕过**任何授权或访问控制。

- 当前版本：**v0.18 RC**（见 `VERSION`；`src/tju_info_retrieval/version.py` 统一管理）
- 运行平台：Windows 10/11 x64，需本机已安装 Microsoft Edge
- 许可证：见 `LICENSE`

## 1. 核心功能

- **多源论文检索**：CNKI / 万方 / IEEE Xplore 多选并发；查询扩展（同义词 + 方向限定）、
  中文概念切分、跨语言查询翻译、按库语法生成 Boolean Query；结果三重去重（DOI → URL → 标题）与融合排序
- **AI 论文分析**：结构化增强摘要，基于检索证据（Evidence）而非模型记忆，逐条给出依据
- **科研画像**：研究领域 / 子方向 / 关键词 / 科研阶段，驱动相关度判定与阅读决策（默认空画像，用户自行填写）
- **论文库管理**：本地论文库（收藏 / 标签 / 备注 / 状态），数据与代码严格隔离
- **AI 调研报告生成**：基于论文库与检索证据生成调研报告，支持 Markdown / Word / PDF 导出
- **Dashboard 展示模式**：检索结果可视化面板
- **多模型支持**：TJU LLM / DeepSeek / OpenAI Compatible 多 Provider，可切换

## 2. 架构说明

```
检索层        sources/ 适配器（CNKI / 万方 / IEEE）+ SearchService 编排
              （查询扩展 → 概念切分 → 跨语言翻译 → Boolean Query 构建 → 多源融合排序）
   ↓
Evidence 层   论文证据抽取（cnki/ieee_paper_evidence），为 AI 分析提供可追溯依据
   ↓
AI 分析层     SummaryService / 增强摘要 Provider / LLM Provider（多模型路由）
   ↓
知识库层      论文库（LibraryStore）+ 科研画像（ResearchProfile），本机存储
   ↓
报告生成层    ReportService + 报告 Provider + 导出器（Markdown / Word / PDF）
```

GUI 为 PySide6 主窗口 + QThread 后台 Worker（检索 / 详情 / 证据 / 报告），
界面不冻结；浏览器操作运行在受控 Edge（专用持久 Profile，登录状态仅存本机）。

## 3. 安装方式

环境要求：Windows 10/11，Python **3.10+**，本机已安装 Microsoft Edge。

```bash
# 1) 安装依赖（playwright 直接驱动本机 Edge，无需 playwright install）
python -m pip install -r requirements.txt

# 2) 可选：启用 Word / PDF 导出
python -m pip install python-docx reportlab

# 3) 启动
python main.py
```

首次使用：

1. 启动后点击「检查授权状态」；首次会打开受控 Edge，请使用您自己的
   天津大学统一认证账号完成登录（系统不保存账号密码，登录状态仅存于本机专用 Profile）。
2. 在设置中配置 AI Provider（见下节），不配置也可使用全部无 AI 链路功能。

## 4. API 配置说明

设置界面支持三种 Provider：

| Provider | 用途 | 密钥环境变量 |
|---|---|---|
| TJU LLM | 天津大学校内部署 LLM 服务 | `TJU_INFO_LLM_API_KEY` |
| DeepSeek | DeepSeek 开放平台 | `DEEPSEEK_API_KEY` |
| OpenAI Compatible | 任意 OpenAI 兼容接口（自定义 base_url / model） | `TJU_INFO_LLM_API_KEY` |

凭据安全设计（`services/api_config_manager.py` / `services/credential_store.py`）：

- 配置 JSON 只保存**非敏感字段**（provider / base_url / model / api_key_env /
  credential_backend），保存时显式剥离 api_key / token / authorization 明文；
- API Key 本体存放在**操作系统凭据库（keyring）**，回退 DPAPI 加密的
  `credential.dat`，均位于用户数据目录、被 `.gitignore` 排除；
- 可通过环境变量直接注入密钥；
- **API Key 绝不进入仓库**：仓库与发布包中不存在任何真实密钥、登录态或个人数据
  （发布前由 `tools/check_release_data.py` 强制扫描把关）。

## 5. 开发说明

```bash
# 安装开发依赖
python -m pip install -r requirements.txt -r requirements-dev.txt

# 运行测试（用户数据隔离到临时目录，不触碰开发机真实数据）
python -m pytest tests/
```

目录结构：

```
src/tju_info_retrieval/   主包（models / sources / services / browser / ui）
tests/                    正式测试套件（TEST_USER_DATA_ROOT 隔离用户数据）
docs/                     设计文档、审计与验收报告
packaging/                PyInstaller 打包脚本与 spec
tools/                    发布守护（check_release_data.py）、验收与清理工具
scripts/                  验收检查脚本
```

约定与边界：

- 不保存账号密码；认证信息只存在于本机专用浏览器 Profile（已 gitignore）。
- 不读取 / 打印 Cookie、token、认证 header；导出与报告仅含文献元数据。
- 不绕过验证码、不绕过天津大学或数据库的访问控制；不批量抓取、不并发检索。
- 运行时数据（`runtime/`、`data/`）全部 gitignore，仓库中不含用户运行数据。
- 架构详情见 `docs/architecture.md`；多源扩展指南见 `docs/development.md`。
