from typing import List, Optional
from google import genai

from src.friend_bot.core.config import GEMINI_API_KEY, FACTS_EMBEDDING_MODEL
from src.friend_bot.core.logger import get_logger

logger = get_logger("facts_embedding")


class FactsEmbeddingClient:
    """封裝 Gemini Embedding API，將事實文字轉為向量，供語意去重分群（見 MemoryManager.group_facts）使用"""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or GEMINI_API_KEY
        self.model = model or FACTS_EMBEDDING_MODEL
        self.client = genai.Client(api_key=self.api_key)

    async def embed_texts(self, texts: List[str]) -> List[Optional[List[float]]]:
        """
        批次計算多筆事實文字的 embedding，回傳順序與輸入一致。

        整批呼叫失敗時全部回傳 None，缺 embedding 的事實會在下次去重批次自然重試
        （沿用 `MemoryExtractor.sweep_unextracted()` 的「失敗就留到下次」精神），
        不會因為 embedding 暫時算不出來而中斷提煉或阻塞事實寫入。
        """
        if not texts:
            return []
        if not self.api_key:
            return [None] * len(texts)

        try:
            response = await self.client.aio.models.embed_content(
                model=self.model,
                contents=texts
            )
        except Exception as e:
            logger.warning(f"事實 embedding 批次計算失敗（{len(texts)} 筆），將於下次批次重試: {e}")
            return [None] * len(texts)

        embeddings = response.embeddings or []
        if len(embeddings) != len(texts):
            logger.warning(
                f"事實 embedding 回傳數量（{len(embeddings)}）與輸入數量（{len(texts)}）不符，捨棄本批結果"
            )
            return [None] * len(texts)

        return [e.values for e in embeddings]

    async def embed_text(self, text: str) -> Optional[List[float]]:
        """計算單一事實文字的 embedding"""
        results = await self.embed_texts([text])
        return results[0] if results else None
