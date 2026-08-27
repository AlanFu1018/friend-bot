import re
import urllib.parse
import aiohttp
from typing import List, Dict, Any, Optional
from src.friend_bot.core.config import SEARCH_TOP_K, MAX_CONTENT_LENGTH_PER_PAGE
from src.friend_bot.core.logger import get_logger

logger = get_logger("web_search")

async def search_duckduckgo(query: str, top_k: int = SEARCH_TOP_K) -> List[Dict[str, str]]:
    """
    使用 DuckDuckGo 進行非同步即時搜尋，回傳包含 title, url, snippet 的結果列表
    """
    results: List[Dict[str, str]] = []
    if not query.strip():
        return results

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    html = await resp.text()

                    # 解析 DuckDuckGo HTML 結果
                    link_pattern = r'<a[^>]+class="[^"]*result__url[^"]*"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>'
                    snippet_pattern = r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>'

                    found_snippets = re.findall(snippet_pattern, html)
                    
                    for raw_href, raw_snip in found_snippets:
                        actual_url = raw_href
                        if "uddg=" in raw_href:
                            match = re.search(r'uddg=([^&]+)', raw_href)
                            if match:
                                actual_url = urllib.parse.unquote(match.group(1))
                        elif raw_href.startswith("//"):
                            actual_url = "https:" + raw_href

                        clean_snip = re.sub(r'<[^>]+>', '', raw_snip).strip()

                        if actual_url.startswith("http") and not any(r["url"] == actual_url for r in results):
                            results.append({
                                "title": clean_snip[:60],
                                "url": actual_url,
                                "snippet": clean_snip
                            })
                            if len(results) >= top_k:
                                break

                    # 若 snippet 解析不足 top_k，嘗試通用解析
                    if len(results) < top_k:
                        found_links = re.findall(link_pattern, html)
                        for raw_href, raw_text in found_links:
                            actual_url = raw_href
                            if "uddg=" in raw_href:
                                match = re.search(r'uddg=([^&]+)', raw_href)
                                if match:
                                    actual_url = urllib.parse.unquote(match.group(1))
                            elif raw_href.startswith("//"):
                                actual_url = "https:" + raw_href

                            clean_text = re.sub(r'<[^>]+>', '', raw_text).strip()
                            if actual_url.startswith("http") and not any(r["url"] == actual_url for r in results):
                                results.append({
                                    "title": clean_text,
                                    "url": actual_url,
                                    "snippet": clean_text
                                })
                                if len(results) >= top_k:
                                    break

        logger.info(f"🔎 [DuckDuckGo] 搜尋完成 (關鍵字: 「{query}」) -> 取得 {len(results)} 個目標網址")
        for i, r in enumerate(results, 1):
            logger.info(f"   ├─ [{i}] {r['url']}")
    except Exception as e:
        logger.error(f"❌ [DuckDuckGo] 搜尋失敗 (關鍵字: {query}): {e}")

    return results

async def fetch_page_with_jina(url: str, max_chars: int = MAX_CONTENT_LENGTH_PER_PAGE) -> str:
    """
    將網址傳遞給 Jina AI Reader (https://r.jina.ai/)，快速獲取網頁純文字 Markdown
    """
    jina_endpoint = f"https://r.jina.ai/{url}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "X-No-Cache": "true",
        "Accept": "text/plain",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(jina_endpoint, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    cleaned_text = text.strip()
                    if len(cleaned_text) > max_chars:
                        cleaned_text = cleaned_text[:max_chars] + "\n...(以下內容過長已省略)..."
                    logger.info(f"📄 [Jina AI Reader] 成功獲取內文 ({len(cleaned_text)} 字元) <- {url}")
                    return cleaned_text
                else:
                    logger.warning(f"⚠️ [Jina AI Reader] 讀取失敗 (HTTP {resp.status}) <- {url}")
    except Exception as e:
        logger.error(f"❌ [Jina AI Reader] 連線失敗 ({url}): {e}")

    return ""

async def perform_web_search(query: str, top_k: int = SEARCH_TOP_K) -> str:
    """
    整合函式（供 Tool / Function Calling 使用）：
    1. DuckDuckGo 檢索前 top_k 個網址
    2. Jina AI Reader 抓取網頁 Markdown
    3. 組裝為結構化文字 Context 回傳給 Gemini
    """
    logger.info(f"🌐 [Web Search Tool] 開始聯網檢索最新資訊: 「{query}」 (目標前 {top_k} 筆)")
    search_results = await search_duckduckgo(query, top_k=top_k)

    if not search_results:
        logger.warning(f"⚠️ [Web Search Tool] 搜尋「{query}」未找到任何結果")
        return f"（搜尋「{query}」未找到相關即時資訊）"

    assembled_sections = []
    assembled_sections.append(f"【即時聯網搜尋結果：{query}】")

    for idx, item in enumerate(search_results, 1):
        target_url = item["url"]
        snippet = item["snippet"]
        content = await fetch_page_with_jina(target_url)
        if not content:
            content = snippet or "（無法讀取內文，僅提供搜尋摘要）"

        assembled_sections.append(
            f"\n--- [來源 {idx}] ---\n網址: {target_url}\n摘要與內容:\n{content}"
        )

    full_context = "\n".join(assembled_sections)
    logger.info(f"✅ [Web Search Tool] 檢索與內文組裝完畢 (共 {len(search_results)} 筆來源，總 Context 長度: {len(full_context)} 字元)")
    return full_context
