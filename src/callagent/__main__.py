"""`python -m callagent [--env-file .env]` - run the server."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from aiohttp import web

from .config import ConfigError, Settings
from .server import create_app


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="callagent", description=__doc__)
    parser.add_argument("--env-file", type=Path, help="KEY=VALUE file to load before startup")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    if args.env_file:
        env = {**read_env_file(args.env_file), **env}

    logging.basicConfig(level=env.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = Settings.from_env(env)
    except ConfigError as err:
        print(f"Configuration error: {err}", file=sys.stderr)
        sys.exit(2)
    web.run_app(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
