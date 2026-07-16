"""`python -m mewcode` entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from mewcode import __version__
from mewcode.app import MewCodeApp
from mewcode.client import AuthenticationError
from mewcode.config import ConfigurationError, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MewCode terminal AI assistant")
    parser.add_argument("--config", help="Path to a MewCode YAML configuration")
    parser.add_argument("--version", action="version", version=f"MewCode {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        app = MewCodeApp(config)
    except (ConfigurationError, AuthenticationError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
