# TJU Research Assistant v0.18 RC

## 项目简介

天津大学信息自动检索整理系统——面向正常授权用户的 AI 辅助科研文献分析桌面应用。

## 主要功能

- 多源科研文献检索（CNKI / 万方 / IEEE Xplore）
- Evidence 链摘要（AI 分析逐条给出可追溯依据）
- AI 增强分析
- 科研画像匹配
- 论文库管理
- AI 科研调研报告生成
- Markdown / Word / PDF 导出
- 多模型 Provider 支持：
  - TJU LLM
  - DeepSeek
  - Custom OpenAI Compatible

## Windows 版本（Windows x64）

1. 下载附件 `TJU_Info_Retrieval_v0.18-rc_win64.zip`；
2. 解压到任意目录（不要直接在 ZIP 内运行）；
3. 运行 `信息自动检索整理系统.exe`。

无需安装 Python 或任何依赖。

## 注意事项

首次使用需要：

- 配置 LLM API（设置 → AI 服务，支持 TJU LLM / DeepSeek / OpenAI Compatible）；
- 本机已安装 Microsoft Edge（系统直接驱动本机 Edge，不下载浏览器）；
- 根据学校资源访问权限完成天津大学统一认证登录。

## 安全说明

- API Key 不包含在发布包（仅存本机系统凭据库或环境变量）；
- 用户数据（论文库、科研画像、配置）仅存储于本机；
- 不上传论文库和登录状态。

## 完整性

- 附件：`TJU_Info_Retrieval_v0.18-rc_win64.zip`（111 MB）
- SHA-256：`dcdbe77426a0cb936b05bd185fedbac59ede9c7742f086947ccb62d06bec81d1`
- 构建提交：`16a645da17f15330a427aa038e5c8d76182b6a4d`
- 构建时间：2026-09-17T17:23:21Z（UTC，PyInstaller 6.16.0）
