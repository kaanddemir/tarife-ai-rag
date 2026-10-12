"""Entrypoint: `python run.py` starts the API and the web UI. Ctrl+C stops both.

TarifeAI is two processes, so the thing that starts them is also the thing that
has to stop them. Keeping both under one parent is what removes the need for PID
files and a separate stop script: the parent knows its children, and a single
Ctrl+C is enough.
"""

import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV_PYTHON = BACKEND / ".venv" / "bin" / "python"

DEFAULT_API_PORT = 8000
DEFAULT_WEB_PORT = 3000
# How long a child gets to exit on SIGTERM before it is killed.
SHUTDOWN_GRACE = 5

# Routine `next dev` lines that repeat what the banner says or report every
# successful compile. Anything else (warnings, errors, stack traces) passes through.
NEXT_NOISE = re.compile(
    r"^(> |▲ Next\.js|- (Local|Network|Environments|Experiments):|✓ (Starting|Ready in|Compiled)|○ Compiling)"
)
ANSI = re.compile(r"\x1b\[[0-9;]*m")
# Output that bypasses Python logging (own handlers, progress bars, C++ logs),
# set through each library's supported switch. Warnings and errors still show;
# Python logging itself is governed by backend/dev_logging.json.
QUIET_LIBRARIES = {
    "TOKENIZERS_PARALLELISM": "false",
    "TQDM_DISABLE": "1",
    "HF_HUB_DISABLE_PROGRESS_BARS": "1",
    "HF_HUB_VERBOSITY": "warning",
    "HF_DATASETS_DISABLE_PROGRESS_BARS": "1",
    "DATASETS_VERBOSITY": "warning",
    "TRANSFORMERS_VERBOSITY": "warning",
    "TRANSFORMERS_NO_ADVISORY_WARNINGS": "1",
    "TORCH_CPP_LOG_LEVEL": "WARNING",
}


def _paint(tty):
    """Return a colouring function that is a no-op unless stdout is a terminal."""
    codes = {"bold": "1", "dim": "2", "green": "32", "yellow": "33"}

    def paint(text, *styles):
        if not tty:
            return text
        prefix = "".join(f"\033[{codes[style]}m" for style in styles)
        return f"{prefix}{text}\033[0m"

    return paint


def _problem(paint, title, *fix):
    """Format a setup problem as the one thing to read and the command to run."""
    lines = ["", f"  {paint(title, 'yellow')}"]
    lines += [f"  {paint(line, 'dim')}" for line in fix]
    lines.append("")
    return "\n".join(lines)


def _env_value(name):
    """Read a setting from the environment, falling back to backend/.env.

    Only used for the banner, so the file is parsed loosely rather than pulled in
    with a dependency the launcher would otherwise not need.
    """
    if os.environ.get(name):
        return os.environ[name]
    env_file = BACKEND / ".env"
    if not env_file.exists():
        return ""
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == name:
            return value.split("#")[0].strip().strip("'\"")
    return ""


def _setup_problem(paint):
    """Return the first missing piece of local setup, or None if all is present."""
    if not VENV_PYTHON.exists():
        return _problem(
            paint,
            "The backend virtual environment is missing.",
            "cd backend && python -m venv .venv",
            "source .venv/bin/activate && pip install -r requirements.txt",
        )
    if not (BACKEND / ".env").exists():
        return _problem(
            paint,
            "backend/.env is missing.",
            "cp backend/.env.example backend/.env",
            "Then set DATABASE_URL, PROVIDER_API_KEY and SUPABASE_URL in it.",
        )
    if not (FRONTEND / "node_modules").exists():
        return _problem(
            paint,
            "Frontend dependencies are not installed.",
            "cd frontend && npm install",
        )
    if not shutil.which("npm"):
        return _problem(
            paint,
            "npm was not found on PATH.",
            "Install Node.js 18.17 or newer: https://nodejs.org",
        )
    return None


def _port_is_free(port):
    """Whether the app can still take `port`, checked before the servers start.

    Both servers fail on a taken port with their own wording and their own exit
    path, one of which scrolls past under the other's output. Asking first is
    what keeps a single, actionable message.
    """
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _port_in_use(paint, name, port, variable):
    return "\n".join((
        "",
        f"  {paint(f'Port {port} ({name}) is already in use.', 'yellow')}",
        f"  {paint('Another copy is probably still running. Stop it there with', 'dim')}",
        f"  {paint('Ctrl+C, or start this one elsewhere:', 'dim')}",
        "",
        f"      {variable}={port + 1} python run.py",
        "",
    ))


def _banner(api_port, web_port, reload_enabled, paint):
    rows = [
        ("Open", paint(f"http://localhost:{web_port}", "bold")),
        ("API", f"http://localhost:{api_port}/docs"),
        ("Model", _env_value("MODEL") or "not set, see backend/.env"),
        ("Stop", "Ctrl+C"),
    ]
    lines = [
        "",
        f"  {paint('TarifeAI', 'bold', 'green')}",
        f"  {paint('─' * 44, 'dim')}",
    ]
    lines += [f"  {paint(label.ljust(10), 'dim')}{value}" for label, value in rows]
    if reload_enabled:
        lines += [
            "",
            f"  {paint('! Auto-reload on: the API restarts on every file change.', 'yellow')}",
        ]
    lines.append("")
    return "\n".join(lines)


def _forward_filtered(stream):
    """Copy a child's stdout to ours, minus NEXT_NOISE lines."""
    for line in iter(stream.readline, ""):
        plain = ANSI.sub("", line).strip()
        if plain and not NEXT_NOISE.match(plain):
            sys.stdout.write(line)
            sys.stdout.flush()


def _accepts(port):
    """Whether something is accepting connections on `port` yet."""
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _start(command, cwd, extra_env=None, filter_stdout=False):
    """Start a child in its own process group.

    Its own group is what keeps Ctrl+C from reaching the children directly: the
    terminal signals only this process, which then shuts both down in order
    instead of racing two independent handlers.
    """
    env = os.environ.copy()
    env.update(extra_env or {})
    if not filter_stdout:
        return subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)
    # stderr stays attached to the terminal, so errors are never filtered.
    process = subprocess.Popen(
        command, cwd=cwd, env=env, start_new_session=True,
        stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    threading.Thread(target=_forward_filtered, args=(process.stdout,), daemon=True).start()
    return process


def _terminate(signum, frame):
    """Turn SIGTERM into the interrupt the shutdown path already handles.

    Ctrl+C arrives as KeyboardInterrupt on its own. SIGTERM does not: its default
    action ends this process outright, which would leave both servers running with
    nothing left to stop them. Raising here puts `kill` on the same path as Ctrl+C.
    """
    raise KeyboardInterrupt


def _stop(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + SHUTDOWN_GRACE
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.1)
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def main():
    paint = _paint(sys.stdout.isatty())

    problem = _setup_problem(paint)
    if problem:
        print(problem, flush=True)
        return 1

    api_port = int(os.environ.get("API_PORT", DEFAULT_API_PORT))
    web_port = int(os.environ.get("WEB_PORT", DEFAULT_WEB_PORT))
    reload_enabled = os.environ.get("RELOAD", "").strip().lower() in {"1", "true", "yes", "on"}

    for name, port, variable in (("API", api_port, "API_PORT"), ("web", web_port, "WEB_PORT")):
        if not _port_is_free(port):
            print(_port_in_use(paint, name, port, variable), flush=True)
            return 1

    api_command = [
        str(VENV_PYTHON), "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1", "--port", str(api_port),
        # Forwarding headers are interpreted only by app/client_ip.py (TRUSTED_PROXIES).
        "--no-proxy-headers",
        # Dev-only: shorter lines, redacted access log, quiet libraries (see app/dev_logging.py).
        "--log-config", "dev_logging.json",
    ]
    if reload_enabled:
        api_command.append("--reload")

    processes = []
    try:
        processes.append(_start(
            api_command,
            cwd=BACKEND,
            # The embedding model warns about forking with parallel tokenizers and
            # then disables them anyway, so that warning is noise either way.
            extra_env=QUIET_LIBRARIES,
        ))
        processes.append(_start(
            ["npm", "run", "dev", "--", "--port", str(web_port)],
            cwd=FRONTEND,
            filter_stdout=True,
        ))
    except OSError as exc:
        for process in processes:
            _stop(process)
        print(_problem(paint, f"Could not start a server: {exc}"), flush=True)
        return 1

    # flush: a piped stdout is block-buffered, and a banner that only appears
    # once the servers are stopped is worse than none.
    print(_banner(api_port, web_port, reload_enabled, paint), flush=True)

    signal.signal(signal.SIGTERM, _terminate)

    code = 0
    died = None
    # Announced once each, when the port actually accepts connections.
    waiting = {"API": api_port, "Web": web_port}
    try:
        # Either child exiting on its own means a half-running app, which is
        # worse than a stopped one: the surviving half answers requests the
        # other half can no longer serve.
        while True:
            for name, process in zip(("API", "web"), processes):
                if process.poll() is not None:
                    code = process.returncode or 1
                    died = name
                    raise SystemExit(code)
            for name, port in list(waiting.items()):
                if _accepts(port):
                    del waiting[name]
                    print(f"  {paint('✓', 'green')} {name} ready", flush=True)
            time.sleep(0.2)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        for process in processes:
            _stop(process)

    if died:
        # The server has already printed why it failed, at whatever length it
        # chose. What is missing after that is which half went and that the
        # other was taken down with it, so that is all this adds.
        print(_problem(
            paint,
            f"The {died} server stopped, so the other one was stopped too.",
            "Its error is above." if died == "API"
            else "Check backend/.env and the frontend output above.",
        ), flush=True)
    else:
        print(f"\n  {paint('Stopped.', 'dim')}\n", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
