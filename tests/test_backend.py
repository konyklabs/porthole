"""Backend-level checks against tests/fake-agentbox: process hygiene and argument safety."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from porthole.backend import BackendError, CliBackend, FixtureBackend, check_operand
from porthole.model import Box

from .conftest import FAKE, FIXTURES


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def wait_until(condition, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.05)


def recorded_pids(pidfile: Path) -> list[int]:
    if not pidfile.exists():
        return []
    return [int(line.split()[1]) for line in pidfile.read_text().splitlines()]


async def test_status_timeout_kills_the_child_and_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pidfile = tmp_path / "pids"
    monkeypatch.setenv("FAKE_AGENTBOX_PIDFILE", str(pidfile))
    monkeypatch.setenv("FAKE_AGENTBOX_STATUS_SLEEP", "5")
    backend = CliBackend(str(FAKE), timeout_s=0.3)
    with pytest.raises(BackendError, match="status timed out after 0.3s"):
        await backend.status()
    pids = recorded_pids(pidfile)
    assert pids
    await wait_until(lambda: not any(pid_alive(pid) for pid in pids))


async def test_cancelled_status_call_does_not_abandon_the_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pidfile = tmp_path / "pids"
    monkeypatch.setenv("FAKE_AGENTBOX_PIDFILE", str(pidfile))
    monkeypatch.setenv("FAKE_AGENTBOX_STATUS_SLEEP", "5")
    backend = CliBackend(str(FAKE), timeout_s=30)
    task = asyncio.ensure_future(backend.status())
    await wait_until(lambda: recorded_pids(pidfile) != [])
    pids = recorded_pids(pidfile)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await wait_until(lambda: not any(pid_alive(pid) for pid in pids))


async def test_follower_close_kills_the_grandchild(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pidfile = tmp_path / "pids"
    monkeypatch.setenv("FAKE_AGENTBOX_PIDFILE", str(pidfile))
    monkeypatch.setenv("FAKE_AGENTBOX_GRANDCHILD", "1")
    monkeypatch.setenv("FAKE_AGENTBOX_GAP", "5")
    backend = CliBackend(str(FAKE))
    follower = backend.follow("/home/me/dev/zeta-tests", "20260905-101500")
    events = []

    async def consume() -> None:
        async for event in follower:
            events.append(event)

    task = asyncio.ensure_future(consume())
    await wait_until(lambda: len(events) >= 1)
    pids = recorded_pids(pidfile)
    assert len(pids) == 2  # the fake and its sleep grandchild
    assert all(pid_alive(pid) for pid in pids)
    await follower.close()
    await task
    assert follower.reaped
    await wait_until(lambda: not any(pid_alive(pid) for pid in pids))


async def test_argv_shapes_and_operand_guard() -> None:
    backend = CliBackend("/x/agentbox")
    assert backend.follow("/r", None).argv == ["/x/agentbox", "logs", "-f", "--json", "--", "/r"]
    assert backend.follow("/r", "R1").argv == [
        "/x/agentbox",
        "logs",
        "-f",
        "--json",
        "--",
        "/r",
        "R1",
    ]
    assert backend.attach_argv("/r", None) == ["/x/agentbox", "attach", "--", "/r"]
    assert backend.attach_argv("/r", "run-R1") == ["/x/agentbox", "attach", "--", "/r", "run-R1"]
    assert backend.attach_argv("box-name", "claude") == [
        "/x/agentbox",
        "attach",
        "--",
        "box-name",
        "claude",
    ]
    for bad in ("-", "--json", "-f"):
        with pytest.raises(BackendError, match="starts with '-'"):
            backend.follow(bad, None)
        with pytest.raises(BackendError, match="starts with '-'"):
            backend.follow("/r", bad)
        with pytest.raises(BackendError, match="starts with '-'"):
            backend.attach_argv(bad, None)
        with pytest.raises(BackendError, match="starts with '-'"):
            await backend.stop_run("/r", bad)
        with pytest.raises(BackendError, match="starts with '-'"):
            await backend.runs(bad)
    with pytest.raises(BackendError, match="empty target"):
        check_operand("", "target")


def test_attach_session_choice() -> None:
    from porthole.app import attach_session

    status = json.loads((FIXTURES / "status.json").read_text())
    boxes = {b["name"]: Box.from_json(b) for b in status["boxes"]}
    assert attach_session(boxes["zeta-tests"]) == "run-20260905-101500"  # running run, no claude
    assert attach_session(boxes["mid-api"]) == "claude"  # claude session listed
    assert attach_session(boxes["alpha-docs"]) == "claude"  # nothing listed: the CLI default
    both = dict(status["boxes"][1])
    both["sessions"] = [{"name": "claude", "age_s": 1}, {"name": "run-20260905-101500", "age_s": 5}]
    assert attach_session(Box.from_json(both)) == "claude"


async def test_fixture_backend_is_keyed_by_target() -> None:
    backend = FixtureBackend(FIXTURES, gap_s=0)
    assert len(await backend.runs("/home/me/dev/zeta-tests")) == 5
    assert await backend.runs("/home/me/dev/mid-api") == []
    assert await backend.runs("unknown-box") == []
    assert len(await backend.runs("omega-web")) == 5  # lost newest run, looked up by name
    assert len(await backend.runs("/home/me/dev/omega-web")) == 5  # and by repo path
    # alpha-docs carries the not-running literal: `runs_total` is null, so nobody could say how
    # many runs it has and fixture mode offers none.
    assert await backend.runs("alpha-docs") == []
    zeta = [e async for e in backend.follow("/home/me/dev/zeta-tests", None)]
    assert len(zeta) == 12
    assert [e async for e in backend.follow("/home/me/dev/mid-api", None)] == []
    with pytest.raises(BackendError):
        await backend.runs("--json")
