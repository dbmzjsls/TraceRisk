"""Fail-closed E2E runtime guard and local worker attestation."""

import argparse
import ctypes
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from database_lifecycle import verify_target
from seed_e2e import owned_from_record
from tracerisk.config import Settings


def process_is_running(pid: int) -> bool:
    """Check liveness without relying on unsupported signal 0 on Windows."""
    if sys.platform == "win32":
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            inspected = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            return bool(inspected) and exit_code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def guard(record: Path, database: str, settings: Settings | None = None) -> str:
    owned = owned_from_record(record)
    config = settings or Settings()
    if database != owned.name or config.postgres_db != owned.name:
        raise ValueError("E2E runtime target differs from the ownership record")
    if config.model_adapter != "mock" or config.embedding_provider != "fake":
        raise ValueError("E2E runtime requires Mock model and Fake embeddings")
    if any(
        os.environ.get(name, "").lower() not in {"", "0", "false", "off", "no"}
        for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2")
    ):
        raise ValueError("E2E runtime tracing must be disabled")
    verify_target(config, owned.name)
    return owned.run_id


def attest_worker(record: Path, database: str, phase: str, proof: Path) -> None:
    if phase not in {"normal", "invalid"}:
        raise ValueError("Unknown E2E worker phase")
    run_id = guard(record, database)
    proof.parent.mkdir(parents=True, exist_ok=True)
    with proof.open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "database": database,
                "run_id": run_id,
                "phase": phase,
                "pid": os.getpid(),
                "started_at": datetime.now(UTC).isoformat(),
            },
            stream,
        )


def check_worker(record: Path, database: str, phase: str, proof: Path) -> None:
    run_id = guard(record, database)
    data = json.loads(proof.read_text(encoding="utf-8"))
    if (
        data.get("database") != database
        or data.get("run_id") != run_id
        or data.get("phase") != phase
        or not isinstance(data.get("pid"), int)
    ):
        raise ValueError("Worker attestation differs from the owned E2E runtime")
    if not process_is_running(data["pid"]):
        raise ValueError("Attested E2E worker is not running")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "worker"))
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--phase", choices=("normal", "invalid"), required=True)
    parser.add_argument("--proof", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "check":
        check_worker(args.record, args.database, args.phase, args.proof)
        return
    attest_worker(args.record, args.database, args.phase, args.proof)
    try:
        import asyncio
        import sys

        from tracerisk.workflows.service import serve

        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
        ) as runner:
            runner.run(serve())
    finally:
        args.proof.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
