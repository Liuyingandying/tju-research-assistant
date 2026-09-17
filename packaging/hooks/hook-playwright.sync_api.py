"""Playwright 官方 hook 的最小变体：保留 driver，排除无关 CLI skills 文档。"""
from PyInstaller.utils.hooks import collect_data_files

datas = []
for source, destination in collect_data_files("playwright"):
    normalized = source.replace("\\", "/").casefold()
    if "/driver/package/lib/tools/skills/" in normalized:
        continue
    datas.append((source, destination))
