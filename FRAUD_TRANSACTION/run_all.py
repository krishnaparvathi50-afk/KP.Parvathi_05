from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def _start(name: str, *, cwd: Path, args: list[str]) -> subprocess.Popen:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    # Keep Flask from spawning extra reloader processes; our apps also set use_reloader=False.
    env.setdefault("WERKZEUG_RUN_MAIN", "true")

    return subprocess.Popen(
        args,
        cwd=str(cwd),
        env=env,
        stdout=None,
        stderr=None,
    )


def main() -> int:
    root_dir = Path(__file__).resolve().parent

    services = [
        ("web1", root_dir / "web1", [sys.executable, "app.py"], "http://127.0.0.1:5000"),
        ("web2", root_dir / "web 2", [sys.executable, "app.py"], "http://127.0.0.1:8000"),
        ("connector", root_dir, [sys.executable, "app.py"], "http://127.0.0.1:9000"),
    ]

    procs: list[tuple[str, subprocess.Popen]] = []
    try:
        for name, cwd, args, _url in services:
            if not cwd.exists():
                print(f"[run_all] Skipping {name}: missing folder {cwd}")
                continue
            print(f"[run_all] Starting {name} ({cwd}) ...")
            procs.append((name, _start(name, cwd=cwd, args=args)))
            time.sleep(0.4)

        print("")
        print("[run_all] Running:")
        for name, _cwd, _args, url in services:
            print(f"  - {name}: {url}")
        print("")
        print("[run_all] Press Ctrl+C to stop all services.")

        while True:
            # Exit if any process stops unexpectedly.
            for name, p in procs:
                code = p.poll()
                if code is not None:
                    print(f"[run_all] {name} exited with code {code}. Stopping others...")
                    return code if isinstance(code, int) else 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[run_all] Stopping...")
        return 0
    finally:
        for name, p in reversed(procs):
            try:
                if p.poll() is None:
                    p.terminate()
            except Exception:
                pass
        # give them a moment to exit
        end = time.time() + 5
        for _name, p in procs:
            while p.poll() is None and time.time() < end:
                time.sleep(0.1)
        for name, p in reversed(procs):
            try:
                if p.poll() is None:
                    p.kill()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())

