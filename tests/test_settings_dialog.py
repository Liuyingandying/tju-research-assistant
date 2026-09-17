#!/usr/bin/env python3
"""正式测试：v0.18 Phase 2.8-B 设置界面与主窗口入口。

- 主窗口：设置按钮存在、点击打开 SettingsDialog、位于“关于”附近；
- SettingsDialog：输入框隐藏、已保存凭据不回显、保存成功、清除 Key、
  未保存也可测试连接（表单凭据直通）；
- 凭据经 CredentialStore（keyring mock），不触碰真实凭据库。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from PySide6.QtWidgets import QApplication, QLineEdit

from tju_info_retrieval.services import credential_store as cs
from tju_info_retrieval.services.api_config_manager import APIConfigManager
from tju_info_retrieval.services.credential_store import CredentialStore


class _FakeKeyring:
    def __init__(self):
        self._store = {}

    def get_password(self, service, username):
        return self._store.get((service, username))

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def delete_password(self, service, username):
        self._store.pop((service, username), None)


class TestSettingsUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["TEST_USER_DATA_ROOT"] = self._tmp.name
        self._keyring = _FakeKeyring()
        kr = mock.patch.object(cs, "_keyring_available", return_value=True)
        kr.start()
        self.addCleanup(kr.stop)
        kg = mock.patch.object(cs, "_get_keyring", return_value=self._keyring)
        kg.start()
        self.addCleanup(kg.stop)
        self.addCleanup(self._tmp.cleanup)

    def tearDown(self):
        os.environ.pop("TEST_USER_DATA_ROOT", None)

    def _make_manager(self):
        return APIConfigManager(credential_store=CredentialStore())

    def _make_dialog(self):
        from tju_info_retrieval.ui.settings_dialog import SettingsDialog
        return SettingsDialog(manager=self._make_manager())

    # ---------- 主窗口入口 ----------

    def test_settings_button_exists_near_about(self):
        from tju_info_retrieval.ui.main_window import MainWindow
        w = MainWindow()
        w.show()
        self.assertTrue(hasattr(w, "btn_settings"))
        self.assertTrue(w.btn_settings.isVisible())
        bar = w._action_bar
        names = [b.text() for b in bar._buttons]
        self.assertIn("设置", names)
        self.assertIn("关于", names)
        self.assertLess(names.index("设置"), names.index("关于"))
        w.close()
        w.deleteLater()

    def test_settings_button_opens_dialog(self):
        from tju_info_retrieval.ui.main_window import MainWindow
        w = MainWindow()
        w.show()
        with mock.patch(
                "tju_info_retrieval.ui.settings_dialog.SettingsDialog") as Dlg:
            Dlg.return_value.exec.return_value = None
            w.btn_settings.click()
            Dlg.assert_called_once()
        w.close()
        w.deleteLater()

    # ---------- SettingsDialog ----------

    def test_dialog_title_and_fields(self):
        dlg = self._make_dialog()
        self.assertEqual(dlg.windowTitle(), "AI增强分析设置")
        for attr in ("combo_service", "input_base_url", "input_model",
                     "input_api_key", "btn_test", "btn_save", "btn_clear"):
            self.assertTrue(hasattr(dlg, attr), attr)
        dlg.deleteLater()

    def test_api_key_input_hidden_by_default(self):
        dlg = self._make_dialog()
        self.assertEqual(dlg.input_api_key.echoMode(),
                         QLineEdit.EchoMode.Password)
        dlg.btn_toggle_key.setChecked(True)
        dlg._toggle_key_visible(True)
        self.assertEqual(dlg.input_api_key.echoMode(),
                         QLineEdit.EchoMode.Normal)
        dlg.deleteLater()

    def test_saved_key_not_echoed_back(self):
        """已保存凭据：输入框为空，仅显示“已保存凭据”状态（2.8-B）。"""
        mgr = self._make_manager()
        mgr.set_from_settings("tju", "https://x/v1", "tju-llm", "hidden-key-1")
        from tju_info_retrieval.ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(manager=mgr)
        self.assertEqual(dlg.input_api_key.text(), "")  # 不回显真实 Key
        self.assertIn("已配置", dlg.label_status.text())
        self.assertNotIn("hidden-key-1", dlg.label_status.text())
        dlg.deleteLater()

    def test_save_config_success(self):
        dlg = self._make_dialog()
        dlg.input_base_url.setText("https://example.invalid/v1")
        dlg.input_model.setText("tju-llm")
        dlg.input_api_key.setText("saved-key-123")
        dlg._on_save()
        mgr = self._make_manager()
        self.assertTrue(mgr.is_configured())
        self.assertEqual(mgr.get_api_key(), "saved-key-123")
        # 2.8-B 文案：AI增强分析已配置
        self.assertIn("已配置", dlg.label_status.text())
        dlg.deleteLater()

    def test_memory_only_save_shows_temporary_notice(self):
        """无安全 backend → memory-only 保存后提示“本次仅临时使用”。"""
        kr = mock.patch.object(cs, "_keyring_available", return_value=False)
        dp = mock.patch.object(cs, "_dpapi_available", return_value=False)
        kr.start(); dp.start()
        self.addCleanup(kr.stop); self.addCleanup(dp.stop)
        dlg = self._make_dialog()
        dlg.input_api_key.setText("temp-key-1")
        dlg._on_save()
        self.assertIn("临时使用", dlg.label_status.text())
        dlg.deleteLater()

    def test_clear_key(self):
        dlg = self._make_dialog()
        mgr = self._make_manager()
        mgr.save_api_key("key-to-clear")
        with mock.patch("tju_info_retrieval.ui.settings_dialog.QMessageBox"
                        ".information"):
            dlg._on_clear_key()
        self.assertEqual(mgr.get_api_key(), "")
        self.assertIn("已清除", dlg.label_status.text())
        dlg.deleteLater()

    def test_unconfigured_status_message(self):
        dlg = self._make_dialog()
        self.assertIn("未配置", dlg.label_status.text())
        dlg.deleteLater()

    def test_test_connection_uses_form_values_before_save(self):
        """未保存配置也可测试连接：表单 base_url/model/key 直通（2.8-B）。"""
        dlg = self._make_dialog()
        dlg.input_base_url.setText("https://form-only.invalid/v1")
        dlg.input_model.setText("form-model")
        dlg.input_api_key.setText("form-key-1")
        cfg = dlg._provider_cfg_from_form(require_key=True)
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["base_url"], "https://form-only.invalid/v1")
        self.assertEqual(cfg["model"], "form-model")
        self.assertEqual(cfg["api_key"], "form-key-1")
        dlg.deleteLater()

    def test_test_connection_blocked_without_key(self):
        dlg = self._make_dialog()
        dlg.input_api_key.clear()
        with mock.patch(
                "tju_info_retrieval.ui.settings_dialog.QMessageBox"
                ".information") as info:
            cfg = dlg._provider_cfg_from_form(require_key=True)
        self.assertIsNone(cfg)
        info.assert_called_once()
        dlg.deleteLater()

    # ---------- 增强模式联动 ----------

    def test_enhanced_not_configured_prompts_settings(self):
        from tju_info_retrieval.ui.main_window import MainWindow
        from tju_info_retrieval.models.result import SearchResult
        w = MainWindow()
        w.show()
        w._on_load_demo()
        result = SearchResult.from_dict(w._results[0])
        w.combo_summary_mode.setCurrentIndex(1)  # AI增强分析
        with mock.patch(
                "tju_info_retrieval.ui.main_window.QMessageBox") as MB:
            box = MB.return_value
            open_btn = object()
            cancel_btn = object()
            box.addButton.side_effect = [open_btn, cancel_btn]
            box.exec.return_value = None
            box.clickedButton.return_value = open_btn
            with mock.patch(
                    "tju_info_retrieval.ui.settings_dialog.SettingsDialog") as Dlg:
                Dlg.return_value.exec.return_value = None
                w._show_summary_for(result)
                Dlg.assert_called_once()
        w.close()
        w.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)