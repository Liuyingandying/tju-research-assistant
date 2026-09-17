#!/usr/bin/env python3
"""正式测试：PortalNavigator（门户打开 / 资源列表 / 资源进入 / 不存在处理）。

需要真实天津大学授权环境（专用 Profile 中 TJU 登录有效）。

v0.18 Phase 2.9-B.1：本文件属**人工验收测试**，必须使用真实 Edge Profile
与 TJU 登录态，因此显式标记 `real_user_data` 以跳过测试隔离与真实数据护栏
（隔离后没有登录态，门户会返回未授权页面）。
默认快速套件请用 `-m "not real_user_data"` 排除本文件。
"""
from unittest import mock

import pytest

pytestmark = pytest.mark.real_user_data

from tju_info_retrieval.browser.portal import PortalNavigator
from tju_info_retrieval.browser.session import BrowserSession


@pytest.fixture(scope="class")
def portal_nv():
    session = BrowserSession()
    session.start()
    navigator = PortalNavigator(session)
    navigator.open_portal()
    yield navigator
    session.shutdown()


class TestPortalNavigator:
    def test_open_portal(self, portal_nv):
        """门户打开后 URL 应为天津大学电子资源平台。"""
        assert "p.lib.tju.edu.cn" in portal_nv._portal.url

    def test_list_resources_chinese_and_foreign(self, portal_nv):
        """中文资源应包含万方，外文资源应包含 IEEE。"""
        chinese_names = portal_nv.list_resources("中文资源")
        foreign_names = portal_nv.list_resources("外文资源")
        assert any("万方" in n for n in chinese_names)
        assert any("IEEE" in n for n in foreign_names)

    def test_open_resource_wanfang_and_ieee(self, portal_nv):
        """万方和 IEEE 的代理落地页标题应符合预期。"""
        wanfang = portal_nv.open_resource("万方数据知识服务平台", "中文资源")
        try:
            assert "万方" in wanfang.title()
        finally:
            wanfang.close()

        ieee = portal_nv.open_resource("IEEE/IET Electronic Library", "外文资源")
        try:
            assert "IEEE Xplore" in ieee.title()
        finally:
            ieee.close()

    def test_resource_not_found(self, portal_nv):
        """不存在的资源名应抛出 ValueError。"""
        with pytest.raises(ValueError, match="不存在的资源"):
            portal_nv.open_resource("不存在的资源", "中文资源")


class TestPortalReadyWait:
    @staticmethod
    def _make_navigator(page):
        session = mock.Mock()
        session.page.return_value = page
        return PortalNavigator(session), session

    def test_open_portal_waits_for_login_redirect(self):
        """仍在登录页时，应等待回跳到 p.lib.tju.edu.cn 后再定位分类。"""
        page = mock.Mock(url="https://authserver.tju.edu.cn/login")
        navigator, session = self._make_navigator(page)

        navigator.open_portal()

        session.open_tju.assert_called_once_with()
        page.wait_for_url.assert_called_once()
        url_pattern = page.wait_for_url.call_args.args[0]
        assert url_pattern.search("https://p.lib.tju.edu.cn/")
        assert not url_pattern.search("https://authserver.tju.edu.cn/login")
        page.get_by_text.assert_any_call("中文资源", exact=True)
        page.get_by_text.assert_any_call("外文资源", exact=True)

    def test_open_portal_waits_for_slow_angular_initialization(self):
        """分类出现后仍应等待资源节点挂载，不能依赖固定 sleep。"""
        page = mock.Mock(url="https://p.lib.tju.edu.cn/")
        navigator, _ = self._make_navigator(page)

        navigator.open_portal()

        page.wait_for_selector.assert_called_once_with(
            PortalNavigator._RESOURCE_NAME_SELECTOR,
            state="attached",
            timeout=PortalNavigator.PORTAL_READY_TIMEOUT_MS,
        )
        page.wait_for_timeout.assert_not_called()
