#!/usr/bin/env python3
"""正式测试：AdapterRegistry（注册 / 获取 / 创建 / 未知来源）。"""
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest

from tju_info_retrieval.services.adapter_registry import AdapterRegistry


class DummyAdapter:
    database_name = "Dummy"
    def __init__(self, session):
        self._session = session


class TestAdapterRegistry:
    def test_register_and_get(self):
        r = AdapterRegistry()
        r.register("Dummy", DummyAdapter)
        assert r.get("Dummy") is DummyAdapter

    def test_available_sources(self):
        r = AdapterRegistry()
        r.register("A", DummyAdapter)
        r.register("B", DummyAdapter)
        assert "A" in r.available_sources()
        assert "B" in r.available_sources()

    def test_create(self):
        r = AdapterRegistry()
        r.register("Dummy", DummyAdapter)
        session = mock.Mock()
        instance = r.create("Dummy", session)
        assert isinstance(instance, DummyAdapter)
        assert instance._session is session

    def test_unknown_source_raises(self):
        r = AdapterRegistry()
        with pytest.raises(ValueError, match="未知数据源"):
            r.get("NonExistent")

    def test_duplicate_register_raises(self):
        r = AdapterRegistry()
        r.register("Dup", DummyAdapter)
        with pytest.raises(ValueError, match="已注册"):
            r.register("Dup", DummyAdapter)