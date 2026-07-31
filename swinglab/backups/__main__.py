"""Run the backup CLI as ``python -m swinglab.backups``."""

from __future__ import annotations

import sys

from swinglab.cli import main as swinglab_main


def main() -> int:
    return swinglab_main(["backup", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
