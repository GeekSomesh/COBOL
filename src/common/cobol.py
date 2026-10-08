"""GnuCOBOL toolchain wrapper: compile and run programs natively or through WSL.

Backend selection (``COBOL_BACKEND`` env var, default ``auto``):
  native  ``cobc`` is on PATH (Linux, macOS, or a Windows GnuCOBOL install)
  wsl     ``cobc`` inside a WSL distro (``COBOL_WSL_DISTRO``, default Ubuntu-24.04)

Batch helpers send many compiles or runs through one shell invocation, because
starting ``wsl.exe`` costs a few hundred milliseconds per call.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_DISTRO = "Ubuntu-24.04"
# Ubuntu's gcc defines _FORTIFY_SOURCE and cobc defines it again: harmless.
_COMPILER_NOISE = ('"_FORTIFY_SOURCE" redefined', "this is the location of the previous definition")


@dataclass
class CompileJob:
    source: Path
    exe: Path
    copy_dirs: Sequence[Path] = ()


@dataclass
class CompileResult:
    source: Path
    exe: Path
    ok: bool
    output: str


class ToolchainError(RuntimeError):
    pass


def _to_wsl_path(path: Path) -> str:
    p = Path(path).resolve()
    drive = p.drive.rstrip(":").lower()
    rest = p.as_posix()[len(p.drive):]
    return f"/mnt/{drive}{rest}"


class Toolchain:
    def __init__(self, backend: str, distro: str = DEFAULT_DISTRO):
        if backend not in ("native", "wsl"):
            raise ValueError(f"unknown COBOL backend {backend!r}")
        self.backend = backend
        self.distro = distro

    # ---- discovery -------------------------------------------------------
    @classmethod
    def detect(cls) -> Toolchain:
        """Pick a backend from the environment; raise ToolchainError if none works."""
        wanted = os.environ.get("COBOL_BACKEND", "auto")
        distro = os.environ.get("COBOL_WSL_DISTRO", DEFAULT_DISTRO)
        candidates = ["native", "wsl"] if wanted == "auto" else [wanted]
        for backend in candidates:
            tc = cls(backend, distro)
            if tc.available():
                return tc
        raise ToolchainError(
            "GnuCOBOL not found. Install it (Ubuntu/WSL: `sudo apt install gnucobol`) "
            "or set COBOL_BACKEND / COBOL_WSL_DISTRO."
        )

    def available(self) -> bool:
        if self.backend == "native":
            return shutil.which("cobc") is not None
        if shutil.which("wsl.exe") is None and shutil.which("wsl") is None:
            return False
        try:
            return self._shell("command -v cobc >/dev/null && echo ok", timeout=60).strip() == "ok"
        except (ToolchainError, subprocess.TimeoutExpired):
            return False

    def version(self) -> str:
        return self._shell("cobc --version | head -1", timeout=60).strip()

    # ---- paths and shell -------------------------------------------------
    def path(self, p: Path) -> str:
        """Path as the toolchain's shell sees it."""
        return _to_wsl_path(p) if self.backend == "wsl" else Path(p).resolve().as_posix()

    def _shell(self, script: str, stdin: str | None = None, timeout: float = 600) -> str:
        """Run a bash script and return stdout. ``stdin`` is fed to the script's stdin."""
        if self.backend == "wsl":
            cmd = ["wsl.exe", "-d", self.distro, "-e", "bash", "-c", script]
        else:
            cmd = ["bash", "-c", script]
        proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise ToolchainError(f"toolchain shell failed ({proc.returncode}): {proc.stderr.strip()}")
        return proc.stdout

    # ---- compile ---------------------------------------------------------
    def compile_many(self, jobs: Iterable[CompileJob], free: bool = True) -> list[CompileResult]:
        jobs = list(jobs)
        if not jobs:
            return []
        fmt = "-free" if free else ""
        lines = ["set +e"]
        for i, job in enumerate(jobs):
            incs = " ".join(f"-I {shlex.quote(self.path(d))}" for d in job.copy_dirs)
            exe = self.path(job.exe)
            src = self.path(job.source)
            lines.append(f"mkdir -p {shlex.quote(str(Path(exe).parent.as_posix()))}")
            lines.append(
                f"out=$(cobc -x {fmt} {incs} {shlex.quote(src)} -o {shlex.quote(exe)} 2>&1); rc=$?; "
                f"printf '@@%d %d\\n' {i} $rc; printf '%s\\n' \"$out\" | sed 's/^/@>{i} /'"
            )
        stdout = self._shell("\n".join(lines), timeout=60 + 30 * len(jobs))
        rc: dict[int, int] = {}
        msgs: dict[int, list[str]] = {i: [] for i in range(len(jobs))}
        for line in stdout.splitlines():
            if line.startswith("@@"):
                idx, code = line[2:].split()
                rc[int(idx)] = int(code)
            elif line.startswith("@>"):
                head, _, msg = line[2:].partition(" ")
                if msg and not any(noise in msg for noise in _COMPILER_NOISE):
                    msgs[int(head)].append(msg)
        return [
            CompileResult(job.source, job.exe, rc.get(i, 1) == 0, "\n".join(msgs[i]))
            for i, job in enumerate(jobs)
        ]

    def compile(self, source: Path, exe: Path, copy_dirs: Sequence[Path] = (), free: bool = True) -> CompileResult:
        return self.compile_many([CompileJob(Path(source), Path(exe), copy_dirs)], free=free)[0]

    # ---- run -------------------------------------------------------------
    def run_many(self, exe: Path, records: Sequence[str], timeout_each: float = 5) -> list[str]:
        """Run ``exe`` once per input record; return each run's stdout.

        All runs happen inside one shell. Records must not contain newlines.
        """
        if not records:
            return []
        script = (
            f"exe={shlex.quote(self.path(exe))}; i=0; "
            "while IFS= read -r rec; do "
            f"printf '@@BEGIN %d\\n' $i; printf '%s\\n' \"$rec\" | timeout {timeout_each} \"$exe\" 2>&1; "
            "printf '\\n@@END %d %d\\n' $i $?; i=$((i+1)); done"
        )
        stdout = self._shell(script, stdin="\n".join(records) + "\n",
                             timeout=30 + (timeout_each + 1) * len(records))
        outputs: list[str] = []
        current: list[str] | None = None
        for line in stdout.splitlines():
            if line.startswith("@@BEGIN "):
                current = []
            elif line.startswith("@@END "):
                outputs.append("\n".join(current or []).strip("\n"))
                current = None
            elif current is not None:
                current.append(line)
        if len(outputs) != len(records):
            raise ToolchainError(f"expected {len(records)} outputs, got {len(outputs)}")
        return outputs

    def run(self, exe: Path, record: str, timeout: float = 5) -> str:
        return self.run_many(exe, [record], timeout_each=timeout)[0]


def parse_display(output: str) -> dict[str, str]:
    """Parse ``NAME=VALUE`` lines printed by generated programs."""
    result: dict[str, str] = {}
    for line in output.splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip():
            result[name.strip()] = value
    return result
