from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from porthole.model import (
    COST_MAX,
    COUNT_MAX,
    EGRESS_MODES,
    EXIT_MAX,
    RUN_STATES,
    SECONDS_MAX,
    TOOLCHAIN_STATES,
    Box,
    Channel,
    Event,
    Leftovers,
    Run,
    Session,
    Standing,
    Status,
    Toolchain,
    channel_style,
    egress_mode,
    egress_style,
    fmt_age,
    fmt_cost,
    fmt_count,
    fmt_elapsed,
    fmt_exit,
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
    # The display list is clipped; the count is the box's own, or the number is a lie.
    assert len(capped.ports) == 20 and capped.summary == "40 ports, scan truncated"
    assert capped.total == 40
    unmarked = Leftovers.from_json({"worktrees": [f"/home/me/dev/app/wt-{n}" for n in range(25)]})
    assert unmarked.summary == "25 worktrees" and unmarked.total == 25
    assert len(unmarked.worktrees) == 20  # still only 20 kept, because nothing renders them
    assert Leftovers(worktrees=("/a", "/b")).total == 2  # built by hand: what it was given
    wide = Leftovers.from_json({"worktrees": ["/work/" + "x" * 500]})
    assert len(wide.worktrees[0]) == 200
    assert Box.from_json({**BASE, "leftovers": "nope"}).leftovers is None
    assert Box.from_json({**BASE, "leftovers": {"ports": "tcp:1"}}).leftovers == Leftovers()


def test_toolchain_accepts_exactly_the_three_contract_words() -> None:
    """2.1 says `ok | findings | unknown`. Anything else is `unknown`, and nothing raises."""
    assert TOOLCHAIN_STATES == ("ok", "findings", "unknown")
    for state in TOOLCHAIN_STATES:
        assert Toolchain.from_json({"state": state}).state == state
        assert Box.from_json({**BASE, "toolchain": {"state": state}}).toolchain == Toolchain(state)
    for other in ("project_mismatch", "OK", " ok", "ok ", "", "pwned", None, 5, [], {}, ["ok"]):
        assert Toolchain.from_json({"state": other}).state == "unknown"
        box = Box.from_json({**BASE, "toolchain": {"state": other, "missing": 1}})
        assert box.toolchain is not None
        assert box.toolchain.state == "unknown" and not box.toolchain.has_findings
        assert box.toolchain.summary == "1 missing"  # an unreadable verdict, a readable count
    assert Toolchain.from_json({}).state == "unknown"  # the key absent is nobody answering


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
    # `1e999` is valid JSON; Python reads it as inf, and int(inf) raises OverflowError, which
    # is neither TypeError nor ValueError. One box's guest must not black out the whole poll.
    assert _count(float("inf")) == 0
    assert _count(float("-inf")) == 0
    assert _count(float("nan")) == 0
    assert _count(10**4000) == 99999  # a count is clamped, never a 4001-character cell


def a_run(**fields: Any) -> Run:
    """A run whose fields are whatever the test hands it, id and state aside."""
    return Run.from_json({"id": "R", "state": "done", **fields})


def test_run_numbers_go_through_the_same_clamp_as_the_counts() -> None:
    """A run's numbers are guest-supplied like every count, and every one of them could raise.

    `int("nope")`, `int(inf)` and `f"{['x']:.2f}"` all raise, all inside the render of the whole
    table, so a single mis-shaped run blacked out every box's row.
    """
    spelled = a_run(exit="0", elapsed_s="427", turns=12.9, cost_usd="0.4312")
    assert (spelled.exit, spelled.elapsed_s, spelled.turns) == (0, 427.0, 12)
    assert spelled.cost_usd == 0.4312
    for junk in ("nope", "", [], {}, ["1"], None, float("inf"), float("-inf"), float("nan")):
        run = a_run(exit=junk, elapsed_s=junk, turns=junk, cost_usd=junk)
        assert (run.exit, run.elapsed_s, run.turns, run.cost_usd) == (None, None, None, None), junk
        # No number to show is an empty cell, never a fabricated zero.
        cells = (fmt_elapsed(run.elapsed_s), fmt_count(run.turns), fmt_cost(run.cost_usd))
        assert cells == ("", "", ""), junk
    huge = a_run(exit=10**4000, elapsed_s=10**4000, turns=10**4000, cost_usd=10**4000)
    assert (huge.elapsed_s, huge.turns, huge.cost_usd, huge.exit) == (
        float(SECONDS_MAX),
        COUNT_MAX,
        COST_MAX,
        EXIT_MAX,
    )
    assert fmt_elapsed(huge.elapsed_s) == "99999:59"  # clamped, never a 4001-character cell
    assert fmt_cost(huge.cost_usd) == "$99999.99"
    negative = a_run(exit=-9, elapsed_s=-5, turns=-1, cost_usd=-1.5)
    assert negative.exit == -9  # a signal keeps its sign: 0 is the one value meaning success
    assert (negative.elapsed_s, negative.turns, negative.cost_usd) == (0.0, 0, 0.0)


def test_run_strings_are_strings_whatever_the_cli_sent() -> None:
    """The other half of the same door: a mis-shaped *string* raises in the row builder.

    `Text()` calls `str.translate` on what it is given, so `Text({})` raises AttributeError
    inside `box_row`, which is the render of every box's row — the same blackout as a number
    that cannot be coerced. `Standing.last_tool`, the identically named sibling field, went
    through `_optional_str` already; `Run.last_tool` is the one that reaches the row builder.
    """
    kept = a_run(model="sonnet", branch="agent/x", last_tool="Bash  ls", last_text="done")
    assert (kept.model, kept.branch, kept.last_tool, kept.last_text) == (
        "sonnet",
        "agent/x",
        "Bash  ls",
        "done",
    )
    for junk in ({"$": 1}, ["ls"], 7, True, 0.5, object()):
        run = a_run(model=junk, branch=junk, started_at=junk, last_tool=junk, last_text=junk)
        for field in (run.model, run.branch, run.started_at, run.last_tool, run.last_text):
            assert isinstance(field, str), (junk, field)
    for empty in (None, "", "   "):
        run = a_run(model=empty, branch=empty, last_tool=empty, last_text=empty)
        assert (run.model, run.branch, run.last_tool, run.last_text) == (None, None, None, None)


def test_the_formatters_take_whatever_the_cli_sent() -> None:
    """The runs modal formats raw JSON values, so the clamp lives in the formatter too."""
    assert fmt_elapsed("427") == "07:07" and fmt_elapsed("nope") == ""
    assert fmt_elapsed(float("inf")) == "" and fmt_elapsed([]) == ""
    assert fmt_count("12") == "12" and fmt_count({}) == "" and fmt_count(float("nan")) == ""
    assert fmt_cost("0.4312") == "$0.43" and fmt_cost(["0.43"]) == ""
    assert fmt_age("600") == "10m ago" and fmt_age("nope") == "?" and fmt_age(float("inf")) == "?"


def test_every_numeric_cell_of_the_runs_modal_is_bounded_in_width() -> None:
    """`exit` and `files_changed` took `str()` raw: two of the modal's ten columns unbounded.

    A column that renders 4003 cells wide inside an 80-cell viewport pushes the columns after
    it off the screen for every run in the list, not only the mis-shaped one. `fmt_exit` is
    `fmt_count` with the sign kept, because `0` is the one exit status that means success.
    """
    assert (fmt_exit(0), fmt_exit(1), fmt_exit(-9)) == ("0", "1", "-9")
    assert fmt_exit(10**4000) == "99999" and fmt_exit(-(10**4000)) == "-99999"
    assert fmt_count(10**4000) == "99999"
    for junk in ("nope", "", [], {}, ["1"], None, float("inf"), float("nan")):
        assert (fmt_exit(junk), fmt_count(junk)) == ("", ""), junk
    widest = max(len(f(10**4000)) for f in (fmt_exit, fmt_count, fmt_elapsed, fmt_cost))
    assert widest <= 9, widest  # the widest a number cell can be: `$99999.99`


def test_a_hostile_session_age_is_a_number_or_nothing() -> None:
    assert Session.from_json({"name": "s", "age_s": "427"}).age_s == 427.0
    for junk in ("nope", [], {}, float("inf"), None):
        assert Session.from_json({"name": "s", "age_s": junk}).age_s is None


def test_a_hostile_count_does_not_raise_anywhere_in_from_json() -> None:
    """Every count a box can reach, fed `1e999` and then a 4001-digit integer."""
    hostile = (
        '{{"boxes":[{{"name":"b","runs_total":{n},'
        '"standing":{{"state":"idle","runs_unseen":{n}}},'
        '"channel":{{"to_host_unread":{n}}},'
        '"leftovers":{{"procs":{n}}},'
        '"toolchain":{{"state":"findings","missing":{n}}}}}]}}'
    )
    infinite = Status.from_json(json.loads(hostile.format(n="1e999"))).boxes[0]
    assert infinite.runs_total == 0 and infinite.pending == 0  # unreadable, so not a count
    assert infinite.leftovers is not None and infinite.leftovers.procs == 0
    assert infinite.toolchain is not None and infinite.toolchain.summary == "findings"
    huge = Status.from_json(json.loads(hostile.format(n=10**4000))).boxes[0]
    assert huge.runs_total == 99999 and huge.pending == 99999 * 2  # clamped, not 4001 wide
    assert huge.toolchain is not None and huge.toolchain.summary == "99999 missing"
    # `runs_total` used a bare int() until this round: a string there raised ValueError.
    assert Box.from_json({**BASE, "runs_total": "x"}).runs_total == 0


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
