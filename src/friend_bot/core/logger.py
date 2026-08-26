import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional

# ANSI 顏色代碼
COLORS = {
    "DEBUG": "\033[36m",     # 青色
    "INFO": "\033[32m",      # 綠色
    "WARNING": "\033[33m",   # 黃色
    "ERROR": "\033[31m",     # 紅色
    "CRITICAL": "\033[35m",  # 紫色
    "RESET": "\033[0m"
}

class ColoredFormatter(logging.Formatter):
    """自訂控制台彩色日誌格式化器"""

    def __init__(self, fmt: Optional[str] = None, datefmt: Optional[str] = None):
        super().__init__(fmt=fmt, datefmt=datefmt)

    def format(self, record: logging.LogRecord) -> str:
        color = COLORS.get(record.levelname, COLORS["RESET"])
        reset = COLORS["RESET"]

        # 替換 levelname 為帶顏色的字串
        orig_levelname = record.levelname
        record.levelname = f"{color}{orig_levelname:<8}{reset}"

        # 格式化
        result = super().format(record)
        record.levelname = orig_levelname  # 還原避免影響其他 handler
        return result

def setup_logger(log_level: int = logging.INFO, log_dir: str = "logs") -> logging.Logger:
    """初始化全域日誌系統，包含控制台彩色輸出與輪轉檔案記錄"""
    root_logger = logging.getLogger("friend_bot")
    root_logger.setLevel(log_level)

    # 避免重複添加 Handler
    if root_logger.handlers:
        return root_logger

    date_format = "%Y-%m-%d %H:%M:%S"
    console_format = "%(asctime)s | %(levelname)s | %(name)s : %(message)s"
    file_format = "%(asctime)s | %(levelname)-8s | %(name)s [%(filename)s:%(lineno)d] : %(message)s"

    # 1. Console Handler (彩色標準輸出)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(ColoredFormatter(fmt=console_format, datefmt=date_format))
    root_logger.addHandler(console_handler)

    # 2. File Handler (自動輪轉記錄檔，最大 10MB，保留 5 份備份)
    try:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=str(log_path / "friend_bot.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8"
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter(fmt=file_format, datefmt=date_format))
        root_logger.addHandler(file_handler)
    except Exception as e:
        root_logger.warning(f"無法建立檔案日誌處理器: {e}")

    # 防止日誌向根 logger 傳遞造成重複
    root_logger.propagate = False

    return root_logger

def get_logger(name: str) -> logging.Logger:
    """取得具名子模組日誌器"""
    return logging.getLogger(f"friend_bot.{name}")
