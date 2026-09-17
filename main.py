#!/usr/bin/env python3
"""信息自动检索整理系统 GUI / frozen 启动入口。

运行：python main.py
"""
import logging
import platform
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from tju_info_retrieval.app_paths import (  # noqa: E402
    PACKAGE_VERSION,
    ensure_user_dirs,
    is_frozen,
    logs_dir,
    user_data_root,
)
from tju_info_retrieval.ui.main_window import MainWindow  # noqa: E402


def configure_logging() -> Path:
    ensure_user_dirs()
    log_path = logs_dir() / "tju_info_retrieval.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(handler, RotatingFileHandler) for handler in root.handlers):
        handler = RotatingFileHandler(
            log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        ))
        root.addHandler(handler)
    logging.getLogger(__name__).info(
        "app start version=%s frozen=%s python=%s windows=%s user_data=%s",
        PACKAGE_VERSION,
        is_frozen(),
        platform.python_version(),
        platform.platform(),
        user_data_root(),
    )
    return log_path


def _show_startup_error(log_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    QMessageBox.critical(
        None,
        "程序启动失败",
        f"程序启动失败，日志已保存至：\n{log_path}",
    )


def main() -> int:
    log_path = configure_logging()
    smoke_args = [arg.split("=", 1)[1] for arg in sys.argv[1:]
                  if arg.startswith("--packaging-smoke=")]
    if smoke_args:
        from tju_info_retrieval.packaging_smoke import run_smoke
        return run_smoke(smoke_args[-1])
    app = QApplication(sys.argv)
    app.setApplicationName("信息自动检索整理系统")
    app.setFont(QFont("Microsoft YaHei", 10))

    def exception_hook(exc_type, exc_value, exc_traceback) -> None:
        logging.getLogger(__name__).critical(
            "uncaught exception", exc_info=(exc_type, exc_value, exc_traceback)
        )
        QMessageBox.critical(
            None,
            "程序发生错误",
            f"程序发生错误，日志已保存至：\n{log_path}",
        )

    sys.excepthook = exception_hook
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - frozen GUI 必须留下可诊断日志
        fallback_log = logs_dir() / "tju_info_retrieval.log"
        try:
            fallback_log = configure_logging()
            logging.getLogger(__name__).exception("startup exception")
        finally:
            _show_startup_error(fallback_log)
        sys.exit(1)
