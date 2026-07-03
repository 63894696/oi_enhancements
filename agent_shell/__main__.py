"""python -m agent_shell"""
from __future__ import annotations

from .app import AgentShellApp
from .config import ensure_config


def main() -> None:
    ensure_config()
    app = AgentShellApp()
    try:
        app.start()
    except KeyboardInterrupt:
        app.shutdown()


if __name__ == "__main__":
    main()
