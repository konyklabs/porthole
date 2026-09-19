"""The agentbox --json contract, as plain data, plus the formatting the table needs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

RUN_STATES = ("running", "done", "failed", "stopped", "waiting", "lost", "unknown")
# `lost`: the run's process vanished without recording an exit (the CLI reconciles it).
# `unknown`: a run directory with no status file. Neither counts as running.
# `waiting`: the run stopped to ask the operator a question; `agentbox resume` answers it.
# The box's egress mode. `drop` was the old name for `deny` and is mapped to it.
EGRESS_MODES = ("deny", "observe", "open", "unknown")
EVENT_KINDS = ("text", "tool", "tool_result", "hook", "result", "status")
# A session row's kind: the run that did the work, the standing session, or something
# that only shares the disk.
SESSION_KINDS = ("run", "session", "other")
# The standing in-box session's state. The CLI's vocabulary is the first four; `unknown` is
# porthole's own fallback for a word the contract does not list.
STANDING_STATES = ("working", "idle", "waiting", "gone", "unknown")
# The box-side toolchain snapshot's verdict. `findings` is the one worth a header line;
# `unknown` means nobody could read the snapshot, which is not a finding.
TOOLCHAIN_STATES = ("ok", "findings", "unknown")
# Caps on the lists inside `leftovers`. The CLI caps them too; a renderer that trusts that
# is a renderer that hangs the day the CLI does not.
LEFTOVER_ENTRIES = 20
LEFTOVER_WIDTH = 200


@dataclass(frozen=True)
class Run:
    id: str
    state: str
    exit: int | None = None
    model: str | None = None
    branch: str | None = None
    started_at: str | None = None
    elapsed_s: float | None = None
    turns: int | None = None
    cost_usd: float | None = None
    last_tool: str | None = None
    last_text: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Run:
        return cls(
            id=str(data.get("id", "")),
            state=str(data.get("state", "")),
            exit=data.get("exit"),
            model=data.get("model"),
            branch=data.get("branch"),
            started_at=data.get("started_at"),
            elapsed_s=data.get("elapsed_s"),
            turns=data.get("turns"),
            cost_usd=data.get("cost_usd"),
            last_tool=data.get("last_tool"),
            last_text=data.get("last_text"),
        )


@dataclass(frozen=True)
class Session:
    """One tmux session the box reports: a run's own, the standing one, or a bystander."""

    name: str
    age_s: float | None = None
    kind: str = "other"
    runid: str | None = None
    state: str = "unknown"
    produced_branch: str | None = None  # a run row's branch, read from its `produced` object

    @classmethod
    def from_json(cls, data: Any) -> Session:
        if not isinstance(data, dict):  # a non-dict entry is a nameless row, never a crash
            return cls(name="")
        kind = str(data.get("kind") or "other")
        produced = data.get("produced")
        return cls(
            name=str(data.get("name", "")),
            age_s=data.get("age_s"),
            kind=kind if kind in SESSION_KINDS else "other",
            runid=_optional_str(data.get("runid")),
            state=str(data.get("state") or "unknown"),
            produced_branch=(
                _optional_str(produced.get("branch")) if isinstance(produced, dict) else None
            ),
        )


@dataclass(frozen=True)
class Channel:
    """The message channel's two directions, counted by the host from its own record.

    Present for a stopped box, which is why it is the host's number and not the box's.
    ``to_host_newest`` is a timestamp, not a count: the newest unread message's time.
    """

    to_host_unread: int = 0
    to_host_open: int = 0
    to_host_newest: str | None = None
    to_box_queued: int = 0
    to_box_open: int = 0
    to_box_lost: int = 0

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Channel:
        return cls(
            to_host_unread=_count(data.get("to_host_unread")),
            to_host_open=_count(data.get("to_host_open")),
            to_host_newest=_optional_str(data.get("to_host_newest")),
            to_box_queued=_count(data.get("to_box_queued")),
            to_box_open=_count(data.get("to_box_open")),
            to_box_lost=_count(data.get("to_box_lost")),
        )

    @property
    def pending(self) -> int:
        """What somebody still has to act on: unread from the box, queued for the box."""
        return self.to_host_unread + self.to_box_queued

    @property
    def lost_summary(self) -> str:
        """``1 queued request`` — what the box removed instead of answering."""
        return _plural(self.to_box_lost, "queued request")


@dataclass(frozen=True)
class Standing:
    """The tracked standing in-box session, or nothing when the box runs none."""

    name: str = "claude"
    state: str = "unknown"
    since: str | None = None
    task: str | None = None
    last_tool: str | None = None
    last_text: str | None = None
    runs_unseen: int = 0  # finished runs the session has not been told about

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Standing:
        state = str(data.get("state") or "unknown")
        return cls(
            name=str(data.get("name") or "claude"),
            state=state if state in STANDING_STATES else "unknown",
            since=_optional_str(data.get("since")),
            task=_optional_str(data.get("task")),
            last_tool=_optional_str(data.get("last_tool")),
            last_text=_optional_str(data.get("last_text")),
            runs_unseen=_count(data.get("runs_unseen")),
        )


@dataclass(frozen=True)
class Leftovers:
    """What earlier runs left behind in the box: reported, never cleaned from here."""

    procs: int = 0
    ports: tuple[str, ...] = ()
    worktrees: tuple[str, ...] = ()
    tmux: tuple[str, ...] = ()
    runs: tuple[str, ...] = ()  # the runids that own at least one of the rows above
    truncated: bool = False

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Leftovers:
        return cls(
            procs=_count(data.get("procs")),
            ports=_strings(data.get("ports")),
            worktrees=_strings(data.get("worktrees")),
            tmux=_strings(data.get("tmux")),
            runs=_strings(data.get("runs")),
            truncated=bool(data.get("truncated")),
        )

    @property
    def total(self) -> int:
        """Resources, not attribution: `runs` names who left them, it is not a fifth kind."""
        return self.procs + len(self.ports) + len(self.worktrees) + len(self.tmux)

    @property
    def summary(self) -> str:
        """``2 processes, 1 port`` — pluralised, zeros omitted, empty when nothing survived."""
        if not self.total:
            return ""
        parts = [
            _plural(self.procs, "process", "processes"),
            _plural(len(self.ports), "port"),
            _plural(len(self.worktrees), "worktree"),
            _plural(len(self.tmux), "tmux session"),
        ]
        if self.truncated:
            parts.append("scan truncated")
        return ", ".join(p for p in parts if p)


@dataclass(frozen=True)
class Toolchain:
    """The box's boot-time toolchain snapshot: whether every baseline tool is at its pin."""

    state: str = "unknown"
    missing: int = 0
    off_pin: int = 0
    checked_at: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Toolchain:
        state = str(data.get("state") or "unknown")
        return cls(
            state=state if state in TOOLCHAIN_STATES else "unknown",
            missing=_count(data.get("missing")),
            off_pin=_count(data.get("off_pin")),
            checked_at=_optional_str(data.get("checked_at")),
        )

    @property
    def has_findings(self) -> bool:
        """A finding, not an absent answer: `unknown` means nobody could read the snapshot."""
        return self.state == "findings"

    @property
    def summary(self) -> str:
        """``1 missing, 1 off pin`` — the counts when there are any, else the state's word."""
        counts = ((self.missing, "missing"), (self.off_pin, "off pin"))
        counted = ", ".join(f"{n} {label}" for n, label in counts if n)
        if counted:
            return counted
        if self.state == "findings":
            return "findings"
        return "" if self.state == "ok" else "not checked"


@dataclass(frozen=True)
class Box:
    name: str
    instance: str
    repo: str | None
    state: str
    claude_version: str | None = None
    firewall: str = "unknown"
    firewall_detail: str | None = None  # why the mode is unknown, or how file and ruleset differ
    run: Run | None = None
    runs_total: int = 0
    sessions: tuple[Session, ...] = ()
    # The four nullable objects of the status contract. `None` is "nobody could answer" —
    # a stopped box, an old box, a scan that could not run — never "there is none".
    standing: Standing | None = None
    channel: Channel | None = None
    leftovers: Leftovers | None = None
    toolchain: Toolchain | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Box:
        run = data.get("run")
        standing = data.get("standing")
        channel = data.get("channel")
        leftovers = data.get("leftovers")
        toolchain = data.get("toolchain")
        return cls(
            name=str(data.get("name", "")),
            instance=str(data.get("instance", "")),
            repo=data.get("repo"),
            state=str(data.get("state", "stopped")),
            claude_version=data.get("claude_version"),
            firewall=egress_mode(data.get("firewall")),
            firewall_detail=_optional_str(data.get("firewall_detail")),
            run=Run.from_json(run) if isinstance(run, dict) else None,
            runs_total=int(data.get("runs_total") or 0),
            sessions=tuple(Session.from_json(s) for s in _as_list(data.get("sessions"))),
            standing=Standing.from_json(standing) if isinstance(standing, dict) else None,
            channel=Channel.from_json(channel) if isinstance(channel, dict) else None,
            leftovers=Leftovers.from_json(leftovers) if isinstance(leftovers, dict) else None,
            toolchain=Toolchain.from_json(toolchain) if isinstance(toolchain, dict) else None,
        )

    @property
    def egress_disagrees(self) -> bool:
        """The mode file and the live ruleset differ: a detail on a known mode, or one
        that says so. That is the case worth the header, not a stopped box."""
        detail = (self.firewall_detail or "").lower()
        if not detail:
            return False
        return self.firewall != "unknown" or "disagree" in detail or "mismatch" in detail

    @property
    def target(self) -> str:
        """What to pass to the CLI as ``<repo>``: the repo path, or the box name without one."""
        return self.repo or self.name

    @property
    def is_running(self) -> bool:
        return self.state == "running"

    @property
    def has_running_run(self) -> bool:
        return self.run is not None and self.run.state == "running"

    @property
    def has_waiting_run(self) -> bool:
        """The run stopped to ask a question: nobody is working and somebody is owed an answer."""
        return self.run is not None and self.run.state == "waiting"

    @property
    def pending(self) -> int:
        """Everything somebody has to act on: the channel both ways, plus unseen runs."""
        channel = self.channel.pending if self.channel else 0
        return channel + (self.standing.runs_unseen if self.standing else 0)

    @property
    def sort_key(self) -> tuple[int, str]:
        if self.has_running_run:
            rank = 0
        elif self.has_waiting_run:
            rank = 1  # a question waiting outranks a box that is merely up
        elif self.is_running:
            rank = 2
        else:
            rank = 3
        return (rank, self.name)


@dataclass(frozen=True)
class Status:
    generated_at: str | None
    boxes: tuple[Box, ...] = field(default_factory=tuple)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Status:
        boxes = tuple(
            Box.from_json(b) for b in _as_list(data.get("boxes")) if isinstance(b, dict)
        )
        return cls(generated_at=data.get("generated_at"), boxes=boxes)

    @property
    def sorted_boxes(self) -> list[Box]:
        return sorted(self.boxes, key=lambda b: b.sort_key)

    @property
    def running_runs(self) -> int:
        return sum(1 for b in self.boxes if b.has_running_run)

    @property
    def waiting_runs(self) -> int:
        return sum(1 for b in self.boxes if b.has_waiting_run)

    @property
    def pending(self) -> int:
        return sum(b.pending for b in self.boxes)

    def age_s(self, now: datetime | None = None) -> float | None:
        stamp = parse_iso(self.generated_at)
        if stamp is None:
            return None
        now = now or datetime.now(UTC)
        return max(0.0, (now - stamp).total_seconds())


@dataclass(frozen=True)
class Event:
    ts: str | None
    run: str | None
    kind: str
    text: str
    tool: str | None = None
    detail: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Event:
        kind = str(data.get("kind") or "text")
        if kind not in EVENT_KINDS:
            kind = "text"
        return cls(
            ts=data.get("ts"),
            run=data.get("run"),
            kind=kind,
            text=str(data.get("text") or ""),
            tool=data.get("tool"),
            detail=data.get("detail"),
        )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _count(value: Any) -> int:
    """A count from the CLI, never an exception. A string, a float, None or a list lands on an int.

    A bad cell is a bug report; a poll that raises is a blackout of the whole table.
    """
    if isinstance(value, (list, tuple)):
        return len(value)
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _as_list(value: Any) -> list[Any]:
    """A list, or nothing. A string is not a list of its characters here."""
    return list(value) if isinstance(value, (list, tuple)) else []


def _strings(
    value: Any, entries: int = LEFTOVER_ENTRIES, width: int = LEFTOVER_WIDTH
) -> tuple[str, ...]:
    """The list's entries as clipped strings: bounded in count and in length."""
    return tuple(str(item)[:width] for item in _as_list(value)[:entries])


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    """``1 port`` / ``2 ports``, and nothing at all for zero."""
    if not count:
        return ""
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp


def fmt_elapsed(seconds: float | None) -> str:
    """mm:ss; minutes keep growing past 59 rather than rolling into hours."""
    if seconds is None:
        return ""
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


def fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    return f"{seconds / 3600:.1f}h ago"


def fmt_cost(cost: float | None) -> str:
    return "" if cost is None else f"${cost:.2f}"


def fmt_turns(turns: int | None) -> str:
    return "" if turns is None else str(turns)


def egress_mode(value: Any) -> str:
    """Normalise the status contract's ``firewall`` field to one of EGRESS_MODES."""
    mode = str(value or "unknown")
    if mode == "drop":
        return "deny"
    return mode


def egress_style(mode: str) -> str:
    """deny and unknown dimmed, observe yellow, open red: the louder the more it lets out."""
    if mode == "observe":
        return "yellow"
    if mode == "open":
        return "red"
    return "dim"


def run_state_style(state: str) -> str:
    """failed and lost share a colour; waiting is yellow; unknown is dimmed;
    the rest use the theme's text."""
    if state in ("failed", "lost"):
        return "red"
    if state == "waiting":
        return "yellow"
    if state == "unknown":
        return "dim"
    return ""


def standing_style(state: str) -> str:
    """The run vocabulary's logic on a session: waiting is owed an answer, gone is a corpse."""
    if state == "gone":
        return "red"
    if state == "waiting":
        return "yellow"
    if state in ("idle", "unknown"):
        return "dim"
    return ""


def channel_style(pending: int) -> str:
    """Something to act on is yellow; the rest of the time the cell is empty anyway."""
    return "yellow" if pending else "dim"


def fmt_ts(ts: str | None) -> str:
    stamp = parse_iso(ts)
    return stamp.strftime("%H:%M:%S") if stamp else "        "
