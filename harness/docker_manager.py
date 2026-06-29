"""Docker helpers used by the benchmark harness."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class DockerRunResult:
    returncode: int
    stdout: str
    stderr: str


def run_in_container(image: str, command: str, timeout: int = 1200) -> DockerRunResult:
    """Run a command in a disposable Docker container."""
    completed = subprocess.run(
        ["docker", "run", "--rm", image, "bash", "-lc", command],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return DockerRunResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
