"""Redis 缓存与滑动窗口限流（第5步）。REDIS_URL 未配置时全部降级为直通。"""
import time

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class RateLimitExceeded(Exception):
    pass


class RedisService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._redis = None

    @property
    def available(self) -> bool:
        return self._redis is not None

    async def startup(self) -> None:
        if not self.settings.redis_url:
            logger.info("redis_disabled")
            return
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                self.settings.redis_url, decode_responses=True
            )
            await self._redis.ping()
            logger.info("redis_connected")
        except Exception as e:  # noqa: BLE001
            logger.warning("redis_unavailable_degrade", error=str(e))
            self._redis = None

    async def shutdown(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()

    # ---------- 滑动窗口限流 ----------

    async def check_rate_limit(self, key: str) -> None:
        """按 key（用户/IP/会话）滑动窗口限流，超限抛 RateLimitExceeded。"""
        if self._redis is None:
            return
        limit = self.settings.rate_limit_per_minute
        now = time.time()
        window = 60
        rkey = f"rl:{key}"
        try:
            pipe = self._redis.pipeline()
            pipe.zremrangebyscore(rkey, 0, now - window)
            pipe.zcard(rkey)
            pipe.zadd(rkey, {f"{now}": now})
            pipe.expire(rkey, window)
            _, count, _, _ = await pipe.execute()
            if count >= limit:
                raise RateLimitExceeded(f"请求过于频繁，每分钟限 {limit} 次")
        except RateLimitExceeded:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("rate_limit_error_passthrough", error=str(e))

    # ---------- 答案缓存 ----------

    @staticmethod
    def cache_key(question: str, doc: str | None) -> str:
        import hashlib

        raw = f"{doc or ''}|{question.strip()}"
        return f"ans:{hashlib.md5(raw.encode()).hexdigest()}"

    async def get_cached_answer(self, question: str, doc: str | None) -> dict | None:
        if self._redis is None:
            return None
        try:
            import json

            data = await self._redis.get(self.cache_key(question, doc))
            return json.loads(data) if data else None
        except Exception:  # noqa: BLE001
            return None

    async def set_cached_answer(
        self, question: str, doc: str | None, payload: dict
    ) -> None:
        if self._redis is None:
            return
        try:
            import json

            await self._redis.setex(
                self.cache_key(question, doc),
                self.settings.cache_ttl_seconds,
                json.dumps(payload, ensure_ascii=False),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("cache_set_error", error=str(e))

    # ---------- 会话记忆文档内容缓存 ----------

    async def get_doc_content(self, doc_id: str) -> dict | None:
        """读取文档 md 内容缓存，返回 {"doc_name":..., "content":...} 或 None。"""
        if self._redis is None:
            return None
        try:
            import json

            data = await self._redis.get(f"docmd:{doc_id}")
            return json.loads(data) if data else None
        except Exception:  # noqa: BLE001
            return None

    async def set_doc_content(
        self, doc_id: str, doc_name: str, content: str, ttl: int | None = None
    ) -> None:
        """缓存文档 md 内容（短期 TTL，文档被重传/删除后自然失效）。"""
        if self._redis is None:
            return
        try:
            import json

            await self._redis.setex(
                f"docmd:{doc_id}",
                ttl or self.settings.doc_md_cache_ttl_seconds,
                json.dumps({"doc_name": doc_name, "content": content}, ensure_ascii=False),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("doc_cache_set_error", error=str(e))
