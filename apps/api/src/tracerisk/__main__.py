import asyncio
import sys

import uvicorn


def main() -> None:
    server = uvicorn.Server(uvicorn.Config("tracerisk.main:app", host="127.0.0.1"))
    # Psycopg async connections require a selector event loop on Windows.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(server.serve())


if __name__ == "__main__":
    main()
