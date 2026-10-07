"""Run the local API with a psycopg-compatible Windows event loop."""

import asyncio
import sys

import uvicorn


def main() -> None:
    server = uvicorn.Server(
        uvicorn.Config("tracerisk.main:app", host="127.0.0.1", port=8000)
    )
    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
    ) as runner:
        runner.run(server.serve())


if __name__ == "__main__":
    main()
