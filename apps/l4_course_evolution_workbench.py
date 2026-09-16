#!/usr/bin/env python3
"""Run the local L4 course-evolution workbench."""

from __future__ import annotations

import argparse
from pathlib import Path

from l4_workbench.service import WorkbenchService
from l4_workbench.store import JsonStore
from l4_workbench.web import make_server


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "sample_data" / "l4_workbench" / "seed.json"
STATE_PATH = ROOT / "outputs" / "l4_workbench" / "state.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="L4 真题驱动的课程自进化平台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    args = parser.parse_args()

    service = WorkbenchService(JsonStore(args.state, SEED_PATH))
    server = make_server(service, args.host, args.port)
    print(f"L4 真题驱动的课程自进化平台：http://{args.host}:{server.server_port}")
    print(f"本地状态：{args.state}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
