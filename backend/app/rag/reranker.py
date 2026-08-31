"""SiliconFlow Rerank 客户端：Qwen3-Reranker-8B 重排（POST /v1/rerank）。

原生异步 httpx，独立单例挂 app.state（lifespan 创建、ChatService 构造注入）；
retriever 不持有它（保持检索层无 HTTP 依赖）。超时/非 2xx/解析失败统一抛
RerankError，不做内部降级——降级策略收口在 retriever（融合序取前 N）。
"""
import httpx

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class RerankError(Exception):
    """rerank 调用失败（超时/非 2xx/响应解析失败）。"""


class SiliconFlowReranker:
    """SiliconFlow rerank API 客户端（OpenAI 兼容生态，Bearer 认证）。"""

    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self._transport = transport  # 仅供测试注入 httpx.MockTransport
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return self.settings.rerank_configured

    async def startup(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self.settings.rerank_base_url,
            timeout=self.settings.rerank_timeout,
            transport=self._transport,
        )
        logger.info(
            "reranker_started",
            model=self.settings.rerank_model,
            configured=self.configured,
        )

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[tuple[int, float]]:
        """对 documents 按 query 相关度重排，返回 [(原始索引, relevance_score)] 降序。"""
        if self._client is None:
            raise RerankError("reranker 未初始化")
        if not documents:
            return []
        body = {
            "model": self.settings.rerank_model,
            "query": query,
            "documents": documents,
            "top_n": top_n or len(documents),
            "return_documents": False,
        }
        logger.info(
            "rerank_request",
            model=self.settings.rerank_model,
            query_len=len(query),
            num_docs=len(documents),
            top_n=top_n,
        )
        try:
            resp = await self._client.post(
                "/rerank",
                json=body,
                headers={"Authorization": f"Bearer {self.settings.rerank_api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException as e:
            raise RerankError(f"rerank 超时: {e}") from e
        except httpx.HTTPStatusError as e:
            body_text = ""
            try:
                body_text = e.response.text[:500]
            except Exception:  # noqa: BLE001
                body_text = ""
            logger.warning(
                "rerank_http_error",
                status=e.response.status_code,
                body=body_text,
            )
            raise RerankError(
                f"rerank 非 2xx: {e.response.status_code} body={body_text}"
            ) from e
        except (httpx.HTTPError, ValueError) as e:
            raise RerankError(f"rerank 请求/解析失败: {type(e).__name__}: {e}") from e

        try:
            results = data["results"]
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("rerank_bad_response", data=str(data)[:500])
            raise RerankError(f"rerank 响应结构异常: {e}") from e
        try:
            return [
                (int(item["index"]), float(item["relevance_score"]))
                for item in results
            ]
        except (KeyError, TypeError, ValueError) as e:
            raise RerankError(f"rerank 结果项异常: {e}") from e
