#!/usr/bin/env python3
"""
watch_rerun.py

Auto-opens new Rerun recordings produced by the Docker container.

Run this once on your Mac while using the Data Explorer:

    python watch_rerun.py

Any .rrd file written to data/rerun_outputs/ by the container is opened
automatically in the native Rerun Viewer (--new flag forces a fresh window
even if one is already open).

Press Ctrl+C to stop.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

WATCH_DIR = Path(__file__).parent / "data" / "rerun_outputs"
POLL_SECS = 1.0


def _find_rerun() -> str | None:
    import shutil
    if cmd := shutil.which("rerun"):
        return cmd
    venv = Path(__file__).parent / ".venvvv" / "bin" / "rerun"
    if venv.exists():
        return str(venv)
    return None


def main() -> None:
    WATCH_DIR.mkdir(parents=True, exist_ok=True)

    rerun_cmd = _find_rerun()
    if not rerun_cmd:
        sys.exit(
            "Error: 'rerun' command not found.\n"
            "Install it with:  pip install rerun-sdk\n"
            "or activate the project venv first."
        )

    print(f"Watching  {WATCH_DIR}")
    print(f"Command   {rerun_cmd} --new <file>")
    print("Ready — click 'Open in Rerun Viewer' in the Data Explorer.\n")
    print("Press Ctrl+C to stop.\n")

    # Seed with files already on disk so we don't re-open old recordings.
    seen: set[Path] = set(WATCH_DIR.glob("*.rrd"))

    while True:
        try:
            current = set(WATCH_DIR.glob("*.rrd"))
            new_files = current - seen
            for f in sorted(new_files, key=lambda p: p.stat().st_mtime):
                print(f"[NEW]  {f.name}")
                subprocess.Popen([rerun_cmd, "--new", str(f)])
            seen = current
            time.sleep(POLL_SECS)
        except KeyboardInterrupt:
            print("\nStopped.")
            break


if __name__ == "__main__":
    main()
