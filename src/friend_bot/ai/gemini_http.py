from google.genai import types

from src.friend_bot.core.config import (
    GEMINI_RETRY_ATTEMPTS,
    GEMINI_RETRY_INITIAL_DELAY,
    GEMINI_RETRY_MAX_DELAY,
)


def build_gemini_http_options() -> types.HttpOptions:
    """
    建立 genai.Client 共用的 HttpOptions，開啟 SDK 內建的指數退避重試（預設為關閉）。
    未指定 http_status_codes 時沿用 SDK 預設的暫時性錯誤碼（408/429/500/502/503/504），
    httpx 連線類暫時性例外也會一併重試；重試用盡後原例外照常拋給呼叫端。
    """
    return types.HttpOptions(retry_options=types.HttpRetryOptions(
        attempts=GEMINI_RETRY_ATTEMPTS,
        initial_delay=GEMINI_RETRY_INITIAL_DELAY,
        max_delay=GEMINI_RETRY_MAX_DELAY,
    ))
