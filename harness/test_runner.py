"""Run task-specific tests and capture logs."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TestResult:
    command: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def passed(self) -> bool:
        return self.returncode == 0


def run_tests(command: str, cwd: str | Path, timeout: int = 600) -> TestResult:
    """Run a shell test command in a repository checkout."""
    completed = subprocess.run(
        command,
        cwd=Path(cwd),
        shell=True,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return TestResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
