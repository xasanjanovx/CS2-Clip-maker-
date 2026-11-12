"""Process helpers built on psutil: find, kill and await Windows processes."""

from __future__ import annotations

import time

import psutil


def find_processes(name: str) -> list[psutil.Process]:
    procs = []
    for proc in psutil.process_iter(["name"]):
        try:
            if proc.info["name"] and proc.info["name"].lower() == name.lower():
                procs.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return procs


def is_process_running(name: str) -> bool:
    return len(find_processes(name)) > 0


def kill_processes(name: str) -> bool:
    """Terminate all processes with the given name. Returns True if any were killed."""
    procs = find_processes(name)
    for proc in procs:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    psutil.wait_procs(procs, timeout=10)
    return len(procs) > 0


def wait_for_process_start(name: str, timeout: float, exclude_pids: set[int] | None = None) -> psutil.Process | None:
    exclude = exclude_pids or set()
    deadline = time.monotonic() + timeout
    best_candidate: psutil.Process | None = None
    while time.monotonic() < deadline:
        procs = [p for p in find_processes(name) if p.pid not in exclude]
        for p in procs:
            try:
                # Real CS2 game engine process quickly exceeds 250 MB of memory
                if p.memory_info().rss > 250 * 1024 * 1024:
                    return p
                best_candidate = p
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        time.sleep(0.5)
    return best_candidate


def wait_for_all_processes_exit(name: str, timeout: float, exclude_pids: set[int] | None = None) -> bool:
    exclude = exclude_pids or set()
    deadline = time.monotonic() + timeout
    # Initial grace period for child processes to spawn
    time.sleep(4.0)
    while time.monotonic() < deadline:
        active = [p for p in find_processes(name) if p.pid not in exclude and p.is_running()]
        if not active:
            return True
        time.sleep(1.0)
    return False


def wait_for_process_exit(proc: psutil.Process, timeout: float) -> bool:
    """Wait for the process to exit. Returns True on clean exit, False on timeout."""
    try:
        proc.wait(timeout=timeout)
        return True
    except psutil.TimeoutExpired:
        return False
    except psutil.NoSuchProcess:
        return True
