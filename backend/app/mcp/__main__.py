"""python -m app.mcp 入口：启动 Streamable HTTP MCP 服务。"""
import asyncio

from app.mcp.server import serve

if __name__ == "__main__":
    asyncio.run(serve())
