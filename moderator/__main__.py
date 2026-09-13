"""Command-line notice for the library-only ARGUS moderator package."""

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "The moderator is an ARGUS library component. Construct "
            "moderator.Moderator and pass it to argus.controller.Controller."
        )
    )
    parser.add_argument(
        "--harness",
        action="store_true",
        help="report the removal of the obsolete subprocess harness",
    )
    args = parser.parse_args(argv)
    if args.harness:
        parser.error(
            "the standalone worker-dispatch harness was removed; use the ARGUS runtime"
        )
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
