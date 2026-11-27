"""Bootstrap an exercise using its selected Python interpreter."""

import os
import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    project = Path(__file__).resolve().parents[1]
    script = Path(sys.argv[1]).resolve()
    sys.path[:0] = [str(project / "compat"), str(project)]

    from rpi_sim_client import bridge

    # Keep GPIO messages on the original stdout pipe. Exercise output (including
    # output from native extensions) goes to the process's console channel.
    with os.fdopen(os.dup(sys.stdout.fileno()), "wb") as protocol:
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        connection = bridge.connect_pipes(sys.stdin.fileno(), protocol)
        sys.stdin = connection.console

        import RPi.GPIO

        sys.path.insert(0, str(script.parent))
        sys.argv = [str(script), *sys.argv[2:]]
        runpy.run_path(str(script), run_name="__main__")
