"""GnuCOBOL toolchain wrapper: compile and run programs natively, through WSL or in Docker.

Backend selection (``COBOL_BACKEND`` env var, default ``auto`` = first that works):
  native  ``cobc`` is on PATH (Linux, macOS, or a Windows GnuCOBOL install)
  wsl     ``cobc`` inside a WSL distro (``COBOL_WSL_DISTRO``, default Ubuntu-24.04)
  docker  ``cobc`` in a container (``COBOL_DOCKER_IMAGE``, default cobol-toolchain:3.1, built from
          docker/toolchain.Dockerfile); the project and temp folders are mounted at the same
          /mnt/<drive>/... paths WSL uses, the container has no network

Batch helpers send many compiles or runs through one shell invocation, because
starting ``wsl.exe`` or a container costs a few hundred milliseconds per call.
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
DOCKER_IMAGE = "cobol-toolchain:3.1"
ROOT = Path(__file__).resolve().parents[2]
# Ubuntu's gcc defines _FORTIFY_SOURCE and cobc defines it again: harmless.
_COMPILER_NOISE = ('"_FORTIFY_SOURCE" redefined', "this is the location of the previous definition")


@dataclass
class CompileJob:
    source: Path
    exe: Path
    copy_dirs: Sequence[Path] = ()
    extra: Sequence[Path] = ()          # further sources linked in (CALLed subprograms)


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
        if backend not in ("native", "wsl", "docker"):
            raise ValueError(f"unknown COBOL backend {backend!r}")
        self.backend = backend
        self.distro = distro
        self.image = os.environ.get("COBOL_DOCKER_IMAGE", DOCKER_IMAGE)

    # ---- discovery -------------------------------------------------------
    @classmethod
    def detect(cls) -> Toolchain:
        """Pick a backend from the environment; raise ToolchainError if none works."""
        wanted = os.environ.get("COBOL_BACKEND", "auto")
        distro = os.environ.get("COBOL_WSL_DISTRO", DEFAULT_DISTRO)
        candidates = ["native", "wsl", "docker"] if wanted == "auto" else [wanted]
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
        if self.backend == "docker":
            if shutil.which("docker") is None:
                return False
            try:
                return self._shell("command -v cobc >/dev/null && echo ok", timeout=120).strip() == "ok"
            except (ToolchainError, subprocess.TimeoutExpired):
                return False
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
        return _to_wsl_path(p) if self.backend in ("wsl", "docker") else Path(p).resolve().as_posix()

    def _docker_mounts(self) -> list[str]:
        """The project and the temp folder (plus COBOL_DOCKER_MOUNTS) at their /mnt/<drive> paths."""
        import tempfile
        dirs = [ROOT, Path(tempfile.gettempdir())]
        dirs += [Path(d) for d in os.environ.get("COBOL_DOCKER_MOUNTS", "").split(os.pathsep) if d]
        args: list[str] = []
        for d in dict.fromkeys(p.resolve() for p in dirs):
            args += ["-v", f"{d}:{_to_wsl_path(d)}"]
        return args

    def _shell(self, script: str, stdin: str | None = None, timeout: float = 600) -> str:
        """Run a bash script and return stdout. ``stdin`` is fed to the script's stdin.

        Without ``stdin`` the script itself goes through stdin (``bash -s``), so long
        batch scripts do not hit the Windows command-line length limit.
        """
        args = ["bash", "-s"] if stdin is None else ["bash", "-c", script]
        if self.backend == "wsl":
            cmd = ["wsl.exe", "-d", self.distro, "-e"] + args
        elif self.backend == "docker":                # one short-lived container per batch
            cmd = ["docker", "run", "--rm", "-i", "--network", "none", *self._docker_mounts(), self.image] + args
        else:
            cmd = args
        data = (script if stdin is None else stdin).encode("utf-8")   # bytes: no CRLF translation
        proc = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout)
        out = proc.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            raise ToolchainError(f"toolchain shell failed ({proc.returncode}): {err}")
        return out

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
            src = " ".join(shlex.quote(self.path(p)) for p in [job.source, *job.extra])
            static = " -fstatic-call" if job.extra else ""
            lines.append(f"mkdir -p {shlex.quote(str(Path(exe).parent.as_posix()))}")
            lines.append(
                f"out=$(cobc -x{static} {fmt} {incs} {src} -o {shlex.quote(exe)} 2>&1); rc=$?; "
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
    def run_batch(self, jobs: Sequence[tuple[Path, str]], timeout_each: float = 5) -> list[str]:
        """Run each (exe, input record) pair once, all inside one shell; return each stdout.

        Records must not contain newlines or tabs; a record may hold several input
        lines separated by \\x1f (one per ACCEPT).
        """
        if not jobs:
            return []
        script = (
            "i=0; while IFS=$'\\t' read -r exe rec; do "
            f"printf '@@BEGIN %d\\n' $i; printf '%s\\n' \"$rec\" | tr '\\037' '\\n' | timeout {timeout_each} \"$exe\" 2>&1; "
            "printf '\\n@@END %d %d\\n' $i $?; i=$((i+1)); done"
        )
        stdin = "".join(f"{self.path(exe)}\t{rec}\n" for exe, rec in jobs)
        stdout = self._shell(script, stdin=stdin, timeout=30 + (timeout_each + 1) * len(jobs))
        records = jobs
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

    def run_many(self, exe: Path, records: Sequence[str], timeout_each: float = 5) -> list[str]:
        """Run ``exe`` once per input record."""
        return self.run_batch([(exe, r) for r in records], timeout_each)

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
