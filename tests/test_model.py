from __future__ import annotations

import json
from datetime import UTC, datetime

from porthole.model import (
    EGRESS_MODES,
    RUN_STATES,
    Box,
    Channel,
    Event,
    Leftovers,
    Session,
    Standing,
    Status,
    Toolchain,
    channel_style,
    egress_mode,
    egress_style,
    fmt_age,
    fmt_cost,
    fmt_elapsed,
    parse_iso,
    run_state_style,
    standing_style,
)

from .conftest import FIXTURES

BASE = {"name": "b", "instance": "agent-box-b", "repo": "/r", "state": "running"}


def fixture_status() -> Status:
    return Status.from_json(json.loads((FIXTURES / "status.json").read_text()))


def test_status_parses_fixture_and_sorts_running_runs_first() -> None:
    status = fixture_status()
    assert [b.name for b in status.sorted_boxes] == [
        "zeta-tests",
        "mid-api",
        "omega-web",
        "alpha-docs",
    ]
    assert status.running_runs == 1
    omega = status.sorted_boxes[2]
    assert omega.run is not None and omega.run.state == "lost"
    assert [b.firewall for b in status.sorted_boxes] == ["deny", "observe", "open", "unknown"]
    assert not omega.has_running_run and omega.is_running
    zeta = status.sorted_boxes[0]
    assert zeta.run is not None and zeta.run.id == "20260905-101500"
    assert zeta.target == "/home/me/dev/zeta-tests"


def test_the_not_running_literal_is_null_not_zero() -> None:
    """alpha-docs carries the host's fallback literal: every answer is null, none is absent."""
    alpha = next(b for b in fixture_status().boxes if b.name == "alpha-docs")
    assert alpha.runs_total == 0  # null coerced, not a claim that it has no runs
    assert alpha.sessions == ()
    assert alpha.standing is None
    assert alpha.channel is None
    assert alpha.leftovers is None
    assert alpha.toolchain is None
    assert alpha.pending == 0


def test_fixture_session_rows_carry_kind_runid_state_and_branch() -> None:
    boxes = {b.name: b for b in fixture_status().boxes}
    run_row = boxes["zeta-tests"].sessions[0]
    assert (run_row.kind, run_row.runid, run_row.state) == ("run", "20260905-101500", "running")
    assert run_row.produced_branch == "agent/cover-checkout-20260905-101500"
    session_row = boxes["mid-api"].sessions[0]
    assert (session_row.kind, session_row.runid, session_row.state) == ("session", None, "running")
    assert session_row.produced_branch is None


def test_session_from_json_tolerates_rubbish() -> None:
    assert Session.from_json("nope").name == ""  # a non-dict entry is a nameless row
    assert Session.from_json({"name": "s", "kind": "mystery"}).kind == "other"
    assert Session.from_json({"name": "s"}).state == "unknown"
    assert Session.from_json({"name": "s", "produced": "agent/x"}).produced_branch is None
    assert Box.from_json({**BASE, "sessions": "nope"}).sessions == ()  # not four char rows
    assert Box.from_json({**BASE, "sessions": ["x", 3]}).sessions == (Session(""), Session(""))
    assert Status.from_json({"boxes": "nope"}).boxes == ()
    assert Status.from_json({"boxes": ["nope", {"name": "b"}]}).boxes[0].name == "b"


def test_channel_counts_and_pending() -> None:
    loud = Channel.from_json(
        {
            "to_host_unread": "1",
            "to_host_open": 2,
            "to_host_newest": "2026-09-05T10:19:40Z",
            "to_box_queued": 2.9,
            "to_box_open": 1,
            "to_box_lost": None,
        }
    )
    assert loud.to_host_unread == 1 and loud.to_box_queued == 2  # a string and a float coerced
    assert loud.to_host_newest == "2026-09-05T10:19:40Z"  # a timestamp, not a count
    assert loud.to_box_lost == 0
    assert loud.pending == 3  # unread from the box plus queued for it, not the open ones
    assert Channel.from_json({}).pending == 0
    assert Box.from_json({**BASE, "channel": "nope"}).channel is None
    assert Box.from_json({**BASE, "channel": {"to_host_unread": 2}}).pending == 2


def test_standing_state_vocabulary_and_unseen_runs() -> None:
    for state in ("working", "idle", "waiting", "gone"):
        assert Standing.from_json({"state": state}).state == state
    assert Standing.from_json({"state": "pwned"}).state == "unknown"
    assert Standing.from_json({}).name == "claude"
    full = Standing.from_json(
        {"name": "claude", "state": "waiting", "since": " ", "task": "x", "runs_unseen": "2"}
    )
    assert full.since is None and full.task == "x" and full.runs_unseen == 2
    assert Box.from_json({**BASE, "standing": "nope"}).standing is None
    box = Box.from_json({**BASE, "standing": {"state": "idle", "runs_unseen": 2}})
    assert box.pending == 2  # unseen runs pend even with no channel object


def test_leftovers_total_and_summary() -> None:
    clean = Leftovers.from_json(
        {"procs": 0, "ports": [], "worktrees": [], "tmux": [], "runs": [], "truncated": False}
    )
    assert clean.total == 0 and clean.summary == ""
    loud = Leftovers.from_json(
        {
            "procs": 2,
            "ports": ["tcp:5173"],
            "worktrees": ["/work/.wt/spike"],
            "tmux": ["devserver"],
            "runs": ["20260905-081200"],
            "truncated": False,
        }
    )
    assert loud.total == 5  # runs names who left them; it is not a fifth resource
    assert loud.summary == "2 processes, 1 port, 1 worktree, 1 tmux session"
    one = Leftovers.from_json({"procs": 1, "ports": ["tcp:1", "tcp:2"]})
    assert one.summary == "1 process, 2 ports"
    capped = Leftovers.from_json({"ports": [f"tcp:{n}" for n in range(40)], "truncated": True})
    assert len(capped.ports) == 20 and capped.summary.endswith("scan truncated")
    wide = Leftovers.from_json({"worktrees": ["/work/" + "x" * 500]})
    assert len(wide.worktrees[0]) == 200
    assert Box.from_json({**BASE, "leftovers": "nope"}).leftovers is None
    assert Box.from_json({**BASE, "leftovers": {"ports": "tcp:1"}}).leftovers == Leftovers()


def test_toolchain_state_and_summary() -> None:
    ok = Toolchain.from_json({"state": "ok", "missing": 0, "off_pin": 0, "checked_at": "2026-09"})
    assert not ok.has_findings and ok.summary == "" and ok.checked_at == "2026-09"
    findings = Toolchain.from_json({"state": "findings", "missing": 1, "off_pin": "1"})
    assert findings.has_findings and findings.summary == "1 missing, 1 off pin"
    assert Toolchain.from_json({"state": "findings"}).summary == "findings"
    unreadable = Toolchain.from_json({"state": "unknown", "missing": None, "off_pin": None})
    assert not unreadable.has_findings and unreadable.summary == "not checked"
    assert Toolchain.from_json({"state": "project_mismatch"}).state == "unknown"
    assert Box.from_json({**BASE, "toolchain": "nope"}).toolchain is None


def test_waiting_run_outranks_a_running_box_and_is_counted_separately() -> None:
    waiting = Box.from_json({**BASE, "name": "w", "run": {"id": "R", "state": "waiting"}})
    idle = Box.from_json({**BASE, "name": "i"})
    running = Box.from_json({**BASE, "name": "r", "run": {"id": "R", "state": "running"}})
    stopped = Box.from_json({**BASE, "name": "s", "state": "stopped"})
    assert waiting.has_waiting_run and not waiting.has_running_run
    assert [b.name for b in Status(None, (idle, stopped, waiting, running)).sorted_boxes] == [
        "r",
        "w",
        "i",
        "s",
    ]
    status = Status(None, (idle, waiting, running))
    assert status.running_runs == 1 and status.waiting_runs == 1


def test_status_pending_sums_every_box() -> None:
    a = Box.from_json({**BASE, "name": "a", "channel": {"to_host_unread": 1, "to_box_queued": 2}})
    b = Box.from_json({**BASE, "name": "b", "standing": {"state": "idle", "runs_unseen": 3}})
    assert Status(None, (a, b)).pending == 6


def test_session_and_channel_styles() -> None:
    assert standing_style("working") == ""  # the theme's text, like a running run
    assert standing_style("waiting") == "yellow"
    assert standing_style("gone") == "red"
    assert standing_style("idle") == standing_style("unknown") == "dim"
    assert channel_style(1) == "yellow"
    assert channel_style(0) == "dim"


def test_count_coercion() -> None:
    from porthole.model import _count

    assert _count("3") == 3
    assert _count(3.7) == 3
    assert _count(None) == 0
    assert _count([]) == 0
    assert _count(["a", "b"]) == 2
    assert _count(-1) == 0  # a negative count is nonsense, never a negative cell
    assert _count("nope") == 0


def test_box_without_repo_targets_its_name() -> None:
    box = Box.from_json({"name": "orphan", "instance": "agent-box-orphan", "repo": None})
    assert box.target == "orphan"
    assert box.state == "stopped"
    assert box.run is None


def test_status_age_from_generated_at() -> None:
    status = Status(generated_at="2026-09-05T10:22:07Z")
    now = datetime(2026, 9, 5, 10, 22, 19, tzinfo=UTC)
    assert status.age_s(now) == 12.0
    assert Status(generated_at=None).age_s(now) is None
    assert Status(generated_at="not a date").age_s(now) is None


def test_event_unknown_kind_falls_back_to_text() -> None:
    event = Event.from_json({"kind": "mystery", "text": "x"})
    assert event.kind == "text"
    assert Event.from_json({"kind": "tool", "tool": "Edit", "text": "a.py"}).tool == "Edit"


def test_lost_and_unknown_runs_are_not_running() -> None:
    base = dict(BASE)
    for state in ("lost", "unknown", "done", "failed", "stopped"):
        box = Box.from_json({**base, "run": {"id": "R", "state": state}, "runs_total": 1})
        assert not box.has_running_run
        assert box.sort_key == (2, "b")  # a running box; rank 1 belongs to a waiting run
    running = Box.from_json({**base, "run": {"id": "R", "state": "running"}, "runs_total": 1})
    assert running.sort_key == (0, "b")
    assert run_state_style("lost") == run_state_style("failed") == "red"
    assert run_state_style("unknown") == "dim"
    assert run_state_style("running") == run_state_style("done") == ""
    assert "lost" in RUN_STATES and "unknown" in RUN_STATES
    assert "waiting" in RUN_STATES
    assert run_state_style("waiting") == "yellow"


def test_egress_mode_mapping_and_style() -> None:
    base = {"name": "b", "instance": "agent-box-b", "repo": "/r", "state": "running"}
    assert Box.from_json({**base, "firewall": "drop"}).firewall == "deny"  # the old name
    for mode in EGRESS_MODES:
        assert Box.from_json({**base, "firewall": mode}).firewall == mode
    assert Box.from_json(base).firewall == "unknown"
    assert Box.from_json({**base, "firewall": None}).firewall == "unknown"
    assert egress_style("deny") == "dim"
    assert egress_style("observe") == "yellow"
    assert egress_style("open") == "red"
    assert egress_style("unknown") == "dim"
    assert egress_mode("drop") == "deny"


def test_firewall_detail_and_disagreement() -> None:
    base = {"name": "b", "instance": "agent-box-b", "repo": "/r", "state": "running"}
    assert Box.from_json(base).firewall_detail is None
    assert Box.from_json({**base, "firewall_detail": "  "}).firewall_detail is None
    stopped = Box.from_json(
        {**base, "state": "stopped", "firewall": "unknown", "firewall_detail": "box is stopped"}
    )
    assert stopped.firewall_detail == "box is stopped"
    assert not stopped.egress_disagrees
    inactive = Box.from_json(
        {**base, "firewall": "unknown", "firewall_detail": "firewall unit is not active"}
    )
    assert not inactive.egress_disagrees
    by_wording = Box.from_json(
        {**base, "firewall": "unknown", "firewall_detail": "mode file and ruleset disagree"}
    )
    assert by_wording.egress_disagrees
    by_contract = Box.from_json(  # a detail on a known mode only happens when they differ
        {**base, "firewall": "deny", "firewall_detail": "ruleset has an extra ACCEPT rule"}
    )
    assert by_contract.egress_disagrees


def test_formatting() -> None:
    assert fmt_elapsed(427) == "07:07"
    assert fmt_elapsed(3725) == "62:05"
    assert fmt_elapsed(None) == ""
    assert fmt_cost(0.4312) == "$0.43"
    assert fmt_cost(None) == ""
    assert fmt_age(12) == "12s ago"
    assert fmt_age(600) == "10m ago"
    assert fmt_age(7200) == "2.0h ago"
    assert fmt_age(None) == "?"
    assert parse_iso("2026-09-05T10:15:00+00:00") == datetime(2026, 9, 5, 10, 15, tzinfo=UTC)


def test_render_event_shows_tool_argument_from_detail():
    from porthole.app import render_event
    from porthole.model import Event

    cli_shape = Event(
        ts="2026-09-05T20:32:38Z",
        run="r",
        kind="tool",
        text="Bash",
        tool="Bash",
        detail="git log --oneline -3",
    )
    assert "Bash  git log --oneline -3" in render_event(cli_shape).plain
    assert "Bash  Bash" not in render_event(cli_shape).plain
    old_shape = Event(
        ts="2026-09-05T20:32:38Z", run="r", kind="tool", text="src/x.py", tool="Read", detail=None
    )
    assert "Read  src/x.py" in render_event(old_shape).plain
