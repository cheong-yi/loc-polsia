"""Command-line entry point for ``loc-polsia``."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import sys

from .check import project
from .filesystem import check_root
from .protocol import error_result, json_bytes, text_bytes


_INTERNAL_ERROR = error_result("internal_error")
_INTERNAL_ERROR_BYTES = {
    "json": json_bytes(_INTERNAL_ERROR),
    "text": text_bytes(_INTERNAL_ERROR),
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loc-polsia")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def _internal_error_bytes(output_format: str) -> bytes:
    serializer = json_bytes if output_format == "json" else text_bytes
    try:
        return serializer(_INTERNAL_ERROR)
    except Exception:
        return _INTERNAL_ERROR_BYTES[output_format]


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        checked = check_root(".")
        output = project(checked.result, arguments.format)
        exit_code = checked.exit_code
    except Exception:
        output = _internal_error_bytes(arguments.format)
        exit_code = 2
    sys.stdout.buffer.write(output)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
