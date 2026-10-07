"""Static Stage 6 Docker/CI contract without printing interpolated secrets."""

import argparse
import json
import subprocess
from pathlib import Path

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]


def validate() -> dict[str, object]:
    compose_text = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    ci_text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    compose = yaml.safe_load(compose_text)
    ci = yaml.safe_load(ci_text)
    services = compose["services"]
    assert compose["name"] == "tracerisk"
    assert set(services) == {"postgres", "api", "worker", "web"}
    assert services["postgres"]["image"] == "pgvector/pgvector:0.8.6-pg17"
    assert services["postgres"]["volumes"] == ["postgres_data:/var/lib/postgresql/data"]
    assert "postgres_data" in compose["volumes"]
    assert services["postgres"]["environment"]["POSTGRES_DB"].startswith(
        "${POSTGRES_INIT_DB:"
    )
    assert services["api"]["environment"]["POSTGRES_DB"] == (
        "${APP_POSTGRES_DB:-tracerisk_demo}"
    )
    assert services["api"]["image"] == services["worker"]["image"]
    assert services["api"]["build"] == services["worker"]["build"]
    assert services["worker"]["command"] == [
        "python",
        "-m",
        "tracerisk.workflows.service",
    ]
    assert services["worker"]["stop_grace_period"] == "30s"
    for name in services:
        assert services[name]["healthcheck"]["test"]
    for name in ("api", "worker"):
        assert services[name]["depends_on"]["postgres"]["condition"] == (
            "service_healthy"
        )
        environment = services[name]["environment"]
        assert environment["MODEL_ADAPTER"] == "mock"
        assert environment["EMBEDDING_PROVIDER"] == "fake"
        assert environment["EMBEDDING_MODEL"] == "fake-sha256-v1"
        assert environment["LANGSMITH_TRACING"] == "false"
        assert environment["LANGCHAIN_TRACING_V2"] == "false"
    assert services["web"]["depends_on"]["api"]["condition"] == "service_healthy"
    jobs = ci["jobs"]
    assert set(jobs) == {"backend", "frontend"}
    backend_steps = "\n".join(step.get("run", "") for step in jobs["backend"]["steps"])
    frontend_steps = "\n".join(
        step.get("run", "") for step in jobs["frontend"]["steps"]
    )
    for command in (
        "uv sync --frozen",
        "uv run alembic upgrade head",
        "uv run pytest -v",
        "uv run ruff check .",
        "uv run ruff format --check .",
        "uv run mypy src",
    ):
        assert command in backend_steps
    for command in (
        "uv sync --frozen",
        "pnpm install --frozen-lockfile",
        "pnpm api:check",
        "pnpm test:auth",
        "pnpm test:business",
        "pnpm test:stage5",
        "pnpm lint",
        "pnpm typecheck",
        "pnpm build",
    ):
        assert command in frontend_steps
    for text in (compose_text, ci_text):
        lowered = text.lower()
        for forbidden in (
            "continue-on-error",
            "down -v",
            "volume rm",
            "sentence-transformers",
            "openai_compatible",
            "huggingface",
        ):
            assert forbidden not in lowered
    return {"services": sorted(services), "jobs": sorted(jobs), "status": "passed"}


def capture_projection(path: Path) -> dict[str, object]:
    command = ["docker", "compose", "config", "--format", "json"]
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError("docker compose config failed; raw config was not saved")
    config = json.loads(result.stdout)
    services = config["services"]
    projection = {
        "command": "docker compose config --format json",
        "exit_code": result.returncode,
        "project": config["name"],
        "services": {
            name: {
                "image": service["image"],
                "build_context": service.get("build", {}).get("context"),
                "depends_on": sorted(service.get("depends_on", {})),
                "has_healthcheck": "healthcheck" in service,
            }
            for name, service in sorted(services.items())
        },
        "volumes": sorted(config.get("volumes", {})),
    }
    path.write_text(json.dumps(projection, indent=2, sort_keys=True), encoding="utf-8")
    return {"path": str(path), "exit_code": result.returncode}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path)
    arguments = parser.parse_args()
    print(validate())
    if arguments.capture:
        print(capture_projection(arguments.capture))
