"""The window: a table of boxes on the left, one run's event log on the right."""

from __future__ import annotations

import asyncio
import subprocess
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Label, RichLog, Static
from textual.worker import Worker

from .backend import Backend, BackendError, LogFollower
from .model import (
    Box,
    Event,
    Status,
    channel_style,
    egress_style,
    fmt_age,
    fmt_cost,
    fmt_count,
    fmt_elapsed,
    fmt_exit,
    fmt_ts,
    run_state_style,
    standing_style,
)

# The only colours in the app: one per event kind. Everything else is the theme's.
KIND_STYLES: dict[str, str] = {
    "text": "",
    "tool": "cyan",
    "tool_result": "dim",
    "hook": "magenta",
    "result": "bold green",
    "status": "yellow",
}

HELP_TEXT = """\
porthole keys

  j / k, arrows   select a box
  enter           runs list for the selected box
  s               stop the selected run (asks first)
  a               attach a terminal (porthole suspends, then resumes)
  r               refresh status now
  l               toggle log follow
  ?               this help
  q               quit

esc closes any dialog.
"""


def render_event(event: Event) -> Text:
    """One line per event: time, kind, then what the CLI said."""
    line = Text(no_wrap=True, overflow="ellipsis")
    line.append(fmt_ts(event.ts), style="dim")
    line.append(" ")
    line.append(f"{event.kind:<11}", style=KIND_STYLES.get(event.kind, ""))
    line.append(" ")
    body = event.text
    if event.kind == "tool" and event.tool:
        # The CLI puts the tool name in both `tool` and `text`, and the
        # argument (file path, command, pattern) in `detail`.
        argument = event.detail or (event.text if event.text != event.tool else "")
        body = f"{event.tool}  {argument}".rstrip()
    line.append(body, style=KIND_STYLES.get(event.kind, ""))
    if event.detail and event.kind != "tool":
        line.append(f"  {event.detail}", style="dim")
    return line


def attach_session(box: Box) -> str:
    """Which tmux session ``a`` attaches to.

    The interactive ``claude`` session when the box lists one; otherwise the
    running run's own session (which the CLI attaches read-only); otherwise
    ``claude``, and the CLI will say if it is missing.
    """
    names = {s.name for s in box.sessions}
    if "claude" in names:
        return "claude"
    if box.has_running_run and box.run is not None:
        return f"run-{box.run.id}"
    return "claude"


def run_state_cell(state: str) -> Text:
    return Text(state, style=run_state_style(state))


def egress_cell(mode: str) -> Text:
    return Text(mode, style=egress_style(mode))


def last_cell(last_tool: str | None, firewall_detail: str | None) -> Text:
    """The last-tool text, with the egress detail appended dimmed when the CLI sent one."""
    cell = Text(last_tool or "")
    if firewall_detail:
        if cell.plain:
            cell.append("  ")
        cell.append(f"egress: {firewall_detail}", style="dim")
    return cell


def session_cell(box: Box) -> Text:
    """The standing in-box session's state, or nothing when the box runs none."""
    standing = box.standing
    if standing is None:
        return Text("")
    return Text(standing.state, style=standing_style(standing.state))


def channel_cell(box: Box) -> Text:
    """At most three tokens, empty when nothing pends: messages up, requests down, unseen runs.

    ``1h`` a handoff the host has not read · ``2↓`` a request the box has not picked up ·
    ``+2`` finished runs the standing session has not been told about.
    """
    channel = box.channel
    standing = box.standing
    tokens = []
    if channel is not None and channel.to_host_unread:
        tokens.append(f"{channel.to_host_unread}h")
    if channel is not None and channel.to_box_queued:
        tokens.append(f"{channel.to_box_queued}↓")
    if standing is not None and standing.runs_unseen:
        tokens.append(f"+{standing.runs_unseen}")
    return Text(" ".join(tokens), style=channel_style(box.pending))


def box_row(box: Box) -> tuple[Text, Text, Text, Text, Text, Text, Text, Text, Text, Text]:
    """Ten cells, one per COLUMNS entry, every one of them a Text.

    Never a plain ``str``: DataTable runs a string cell through ``Text.from_markup``, so a
    ``[dim]`` in a name the CLI reported would vanish and a ``[/]`` would raise mid-render.
    """
    dot = Text("●", style="green") if box.is_running else Text("○", style="dim")
    egress = egress_cell(box.firewall)
    run = box.run
    if run is None:
        return (
            dot,
            egress,
            Text(box.name),
            Text(""),
            Text(""),
            Text(""),
            Text(""),
            last_cell(None, box.firewall_detail),
            session_cell(box),
            channel_cell(box),
        )
    return (
        dot,
        egress,
        Text(box.name),
        run_state_cell(run.state),
        Text(fmt_elapsed(run.elapsed_s)),
        Text(fmt_count(run.turns)),
        Text(fmt_cost(run.cost_usd)),
        last_cell(run.last_tool, box.firewall_detail),
        session_cell(box),
        channel_cell(box),
    )


def egress_disagreements(status: Status) -> str | None:
    """One header line naming every box whose mode file and live ruleset disagree."""
    parts = [f"{b.name}: {b.firewall_detail}" for b in status.sorted_boxes if b.egress_disagrees]
    return "egress " + "; ".join(parts) if parts else None


def toolchain_notices(status: Status) -> str | None:
    """Boxes whose baseline toolchain has a finding. `unknown` is not one: nobody answered."""
    parts = [
        f"{b.name}: {b.toolchain.summary}"
        for b in status.sorted_boxes
        if b.toolchain is not None and b.toolchain.has_findings
    ]
    return "toolchain " + "; ".join(parts) if parts else None


def leftover_notices(status: Status) -> str | None:
    """What earlier runs left running. No column: when nothing survived there is nothing to say."""
    parts = [
        f"{b.name}: {b.leftovers.summary}"
        for b in status.sorted_boxes
        if b.leftovers is not None and b.leftovers.total
    ]
    return "leftovers " + "; ".join(parts) if parts else None


def channel_notices(status: Status) -> str | None:
    """A queued request that is gone from the box's mailbox: the box removed it, unanswered."""
    parts = [
        f"{b.name}: {b.channel.lost_summary} the box removed"
        for b in status.sorted_boxes
        if b.channel is not None and b.channel.to_box_lost
    ]
    return "channel " + "; ".join(parts) if parts else None


# One header line can show, and four things want it. Priority: what can leave the box, then
# what invalidates the work, then what costs resources, then what was silently dropped.
POLL_NOTICES = (
    ("egress", egress_disagreements),
    ("toolchain", toolchain_notices),
    ("leftovers", leftover_notices),
    ("channel", channel_notices),
)
# Every source a poll can raise, so a later clean poll clears it. A literal list here is how a
# new source stays on the screen for ever.
POLL_SOURCES = ("status",) + tuple(name for name, _ in POLL_NOTICES)


def poll_notices(status: Status) -> list[tuple[str, str]]:
    """Every notice this poll raises, in priority order."""
    return [(name, line) for name, fn in POLL_NOTICES if (line := fn(status))]


class ConfirmScreen(ModalScreen[bool]):
    """A yes/no question. Enter or y confirms; esc or n cancels."""

    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
        Binding("n", "cancel", "no", show=False),
        Binding("y", "confirm", "yes"),
        Binding("enter", "confirm", "confirm", show=False),
    ]

    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    ConfirmScreen > Vertical {
        width: auto; max-width: 80%; height: auto; padding: 1 2;
        border: round $primary; background: $surface;
    }
    ConfirmScreen Horizontal { width: auto; height: auto; margin-top: 1; }
    ConfirmScreen Button { margin-right: 2; }
    """

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical():
            # Text, not the string: the question carries a runid and a box name, and Label
            # would read a `[dim]` in either as markup and a `[/]` would raise here.
            yield Label(Text(self.question))
            with Horizontal():
                yield Button("yes", id="yes", variant="error")
                yield Button("no", id="no")

    @on(Button.Pressed, "#yes")
    def action_confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def action_cancel(self) -> None:
        self.dismiss(False)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "close", "close"),
        Binding("question_mark", "close", "close", show=False),
        Binding("q", "close", "close", show=False),
    ]

    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    HelpScreen > VerticalScroll {
        width: 72; height: auto; max-height: 90%; padding: 1 2;
        border: round $primary; background: $surface;
    }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(HELP_TEXT)

    def action_close(self) -> None:
        self.dismiss(None)


class RunsScreen(ModalScreen[None]):
    """The ``runs --json`` table for one box."""

    BINDINGS = [
        Binding("escape", "close", "close"),
        Binding("q", "close", "close", show=False),
    ]

    DEFAULT_CSS = """
    RunsScreen { align: center middle; }
    RunsScreen > Vertical {
        width: 90%; height: 80%; padding: 1 2;
        border: round $primary; background: $surface;
    }
    RunsScreen DataTable { height: 1fr; }
    RunsScreen #runs-status { height: auto; }
    """

    COLUMNS = (
        "runid",
        "state",
        "exit",
        "model",
        "branch",
        "started",
        "duration",
        "turns",
        "cost",
        "files",
    )

    def __init__(self, backend: Backend, box: Box) -> None:
        super().__init__()
        self.backend = backend
        self.box = box

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(Text(f"runs for {self.box.name}"), id="runs-title")
            yield DataTable(id="runs", cursor_type="row", zebra_stripes=True)
            yield Static("loading…", id="runs-status")

    def on_mount(self) -> None:
        table = self.query_one("#runs", DataTable)
        table.add_columns(*self.COLUMNS)
        table.focus()
        self.load_runs()

    @work(exclusive=True, exit_on_error=False)
    async def load_runs(self) -> None:
        status = self.query_one("#runs-status", Static)
        table = self.query_one("#runs", DataTable)
        try:
            runs = await self.backend.runs(self.box.target)
            for index, run in enumerate(runs):
                runid = run.get("runid")
                key = str(runid) if runid else f"row-{index}"
                table.add_row(*self._cells(run), key=key)
        except Exception as exc:  # a failed call is shown here and in the header, never raised
            message = f"runs failed: {exc}"
            status.update(Text(message, style="yellow"))
            app = self.app
            if isinstance(app, PortholeApp):
                app.set_error(message, "runs")
            return
        status.update(f"{len(runs)} runs  ·  esc closes")

    @staticmethod
    def _cells(run: dict[str, Any]) -> tuple[Text, ...]:
        """Every cell a Text, for the reason box_row gives: a runid, a model name and a
        branch are the box's or the agent's words, and DataTable parses a `str` cell.

        Every number goes through the model's formatters, including the two that used to take
        `str()` raw: a 4001-digit `exit` sized its column to 4003 cells inside an 80-cell
        viewport, which pushes the columns after it off the screen for every run in the list.
        """
        return (
            Text(str(run.get("runid") or "")),
            run_state_cell(str(run.get("state") or "")),
            Text(fmt_exit(run.get("exit"))),
            Text(str(run.get("model") or "")),
            Text(str(run.get("branch") or "")),
            Text(str(run.get("started_at") or "")),
            Text(fmt_elapsed(run.get("duration_s"))),
            Text(fmt_count(run.get("turns"))),
            Text(fmt_cost(run.get("cost_usd"))),
            Text(fmt_count(run.get("files_changed"))),
        )

    def action_close(self) -> None:
        self.dismiss(None)


class PortholeApp(App[None]):
    """A renderer of ``agentbox --json`` output. It never crosses into a box itself.

    The command palette is off: its Screenshot command would write the screen
    to disk, and the boundary paragraph in the README says nothing does.
    """

    TITLE = "porthole"
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    #summary { height: 1; padding: 0 1; background: $primary; color: $text; }
    #error { height: 1; padding: 0 1; background: $error; color: $text; }
    #error.hidden { display: none; }
    #body { height: 1fr; }
    #boxes { width: 1fr; height: 1fr; }
    #log { width: 1fr; height: 1fr; border-left: solid $primary; }
    """

    BINDINGS = [
        Binding("j", "cursor_down", "select", key_display="j/k"),
        Binding("k", "cursor_up", "up", show=False),
        Binding("enter", "open_runs", "runs"),
        Binding("s", "stop_run", "stop"),
        Binding("a", "attach", "attach"),
        Binding("r", "refresh_now", "refresh"),
        Binding("l", "toggle_follow", "follow"),
        Binding("question_mark", "help", "help", key_display="?"),
        Binding("q", "quit", "quit"),
    ]

    COLUMNS = (
        "",
        "egress",
        "box",
        "run",
        "elapsed",
        "turns",
        "cost",
        "last tool",
        "session",
        "chan",
    )

    def __init__(self, backend: Backend, interval: float = 3.0) -> None:
        super().__init__()
        self.backend = backend
        self.interval = interval
        self.status: Status | None = None
        self.error: str | None = None
        self.error_source: str | None = None
        self.selected: str | None = None
        self.follow_enabled = True
        self.follow_key: tuple[str, str | None] | None = None
        self.follower: LogFollower | None = None
        self.last_follower: LogFollower | None = None
        self.follow_failed = False
        self.log_lines = 0
        self.polls_started = 0
        self.polls_skipped = 0
        self._status_worker: Worker[None] | None = None
        self._selection_lock = asyncio.Lock()

    # ------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        yield Static("porthole", id="summary")
        yield Static("", id="error", classes="hidden")
        with Horizontal(id="body"):
            yield DataTable(id="boxes", cursor_type="row", zebra_stripes=True)
            yield RichLog(id="log", max_lines=5000, wrap=False, markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#boxes", DataTable)
        table.add_columns(*self.COLUMNS)
        table.focus()
        self.poll_tick()
        self.set_interval(self.interval, self.poll_tick, name="status-poll")

    # ------------------------------------------------------------- status

    def poll_tick(self) -> None:
        """Start a poll unless the previous one is still running; never pile them up."""
        worker = self._status_worker
        if worker is not None and not worker.is_finished:
            self.polls_skipped += 1
            return
        self.polls_started += 1
        self._status_worker = self.refresh_status()

    @work(group="status", exit_on_error=False)
    async def refresh_status(self) -> None:
        try:
            status = await self.backend.status()
        except Exception as exc:  # a failed poll is a header line, never a crash
            self.set_error(str(exc) or exc.__class__.__name__, "status")
            return
        try:
            self.render_table(status)
            self.status = status
            notices = poll_notices(status)
            if notices:
                source, line = notices[0]
                extra = f"  (+{len(notices) - 1} more)" if len(notices) > 1 else ""
                self.set_error(line + extra, source)
            elif self.error_source in POLL_SOURCES:
                self.clear_error()
            else:
                self.render_header()
            await self.sync_selection()
        except Exception as exc:  # a bad render keeps the last good table
            self.set_error(f"status render failed: {exc}", "status")

    def action_refresh_now(self) -> None:
        if self.follow_failed:
            self.follow_key = None  # a refresh also retries a log that stopped on an error
        self.poll_tick()

    def set_error(self, message: str, source: str) -> None:
        """One line in the header. A later successful poll clears only poll errors."""
        self.error = message
        self.error_source = source
        self.render_header()

    def clear_error(self) -> None:
        self.error = None
        self.error_source = None
        self.render_header()

    def render_header(self) -> None:
        summary = self.query_one("#summary", Static)
        if self.status is None:
            summary.update(Text("porthole  ·  waiting for agentbox status"))
        else:
            boxes = len(self.status.boxes)
            runs = self.status.running_runs
            waiting = self.status.waiting_runs
            pending = self.status.pending
            follow = "follow on" if self.follow_enabled else "follow off"
            # Both new counts are omitted when zero: a waiting run and a pending message are
            # exceptions, and a header that always says "0 waiting" stops being read.
            run_part = f"{runs} running run{'s' if runs != 1 else ''}"
            if waiting:
                run_part += f" · {waiting} waiting"
            pending_part = f"  ·  {pending} pending" if pending else ""
            summary.update(
                Text(
                    f"porthole  ·  {boxes} box{'es' if boxes != 1 else ''}  ·  {run_part}"
                    f"{pending_part}  ·  status {fmt_age(self.status.age_s())}  ·  {follow}"
                )
            )
        error = self.query_one("#error", Static)
        if self.error:
            # Text, not a markup string: the line carries CLI stderr and box-authored detail,
            # and a `[word]` in it would otherwise be parsed as markup and silently vanish.
            error.update(Text(f"agentbox: {self.error}"))
            error.remove_class("hidden")
        else:
            error.update("")
            error.add_class("hidden")

    def render_table(self, status: Status) -> None:
        """Build every row first, so a bad value fails before the old table is touched."""
        boxes = status.sorted_boxes
        names = [b.name for b in boxes]
        if len(set(names)) != len(names):
            raise ValueError("two boxes share a name")
        rows = [box_row(box) for box in boxes]
        table = self.query_one("#boxes", DataTable)
        if table.row_count and 0 <= table.cursor_row < table.row_count:
            # The cursor is the operator's intent, even when its highlight message is
            # still queued behind this poll: a re-render must never undo a keypress.
            self.selected = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        if self.selected not in names:
            self.selected = names[0] if names else None
        table.clear()
        for name, row in zip(names, rows, strict=True):
            table.add_row(*row, key=name)
        if self.selected is not None:
            table.move_cursor(row=names.index(self.selected), animate=False)

    @property
    def boxes(self) -> list[Box]:
        return self.status.sorted_boxes if self.status else []

    def box_named(self, name: str | None) -> Box | None:
        for box in self.boxes:
            if box.name == name:
                return box
        return None

    @property
    def selected_box(self) -> Box | None:
        return self.box_named(self.selected)

    # ---------------------------------------------------------- selection

    def action_cursor_down(self) -> None:
        self.query_one("#boxes", DataTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#boxes", DataTable).action_cursor_up()

    @on(DataTable.RowHighlighted, "#boxes")
    async def on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        table = self.query_one("#boxes", DataTable)
        if table.row_count == 0 or event.cursor_row != table.cursor_row:
            return  # stale: the cursor moved on while this message waited
        name = event.row_key.value
        if name != self.selected:
            self.selected = name
        await self.sync_selection()

    async def sync_selection(self) -> None:
        """Make the log pane follow the selected box's current or newest run.

        Serialised behind a lock, and after every await the selection is
        re-checked: a call that finds the operator moved on gives up and lets the
        call for the new selection do the work.
        """
        async with self._selection_lock:
            box = self.selected_box
            if box is None:
                await self.stop_following()
                self.follow_key = None
                return
            runid = box.run.id if box.run else None
            key = (box.name, runid)
            if key == self.follow_key:
                return
            await self.stop_following()
            self.follow_key = None
            if self.selected != box.name:
                return  # the cursor moved while the old follower was being killed
            log = self.query_one("#log", RichLog)
            log.clear()
            self.log_lines = 0
            self.follow_failed = False
            if not box.is_running:
                self.log_write(Text(f"{box.name} is stopped; nothing to follow", style="dim"))
                self.follow_key = key
                return
            if box.run is None and box.runs_total == 0:
                self.log_write(Text(f"{box.name} has no runs yet", style="dim"))
                self.follow_key = key
                return
            label = runid or "newest run"
            self.log_write(Text(f"── {box.name} · {label} ──", style="dim"))
            try:
                follower = self.backend.follow(box.target, runid)
                self.follower = follower  # handle first, then the cancellable spawn
                await follower.start()
            except BackendError as exc:
                self.follower = None
                self.follow_failed = True
                self.log_write(Text(f"log stopped: {exc}", style="yellow"))
                self.set_error(f"logs: {exc}", "logs")
                self.follow_key = key
                return
            if self.selected != box.name or self.follower is not follower:
                await follower.close()
                return
            self.follow_key = key
            self.follow_log(follower)

    async def stop_following(self) -> None:
        follower = self.follower
        self.follower = None
        if follower is not None:
            self.last_follower = follower
            await follower.close()
        self.workers.cancel_group(self, "log")

    @work(group="log", exit_on_error=False)
    async def follow_log(self, follower: LogFollower) -> None:
        try:
            async for event in follower:
                if self.follower is not follower:
                    break
                self.log_write(render_event(event))
        except Exception as exc:  # anything: a dead stream is shown, never silent
            reason = str(exc) or exc.__class__.__name__
            self.log_write(Text(f"log stopped: {reason}", style="yellow"))
            if self.follower is follower:
                self.follow_failed = True
                self.set_error(f"logs: {reason}", "logs")
        finally:
            await follower.close()

    def log_write(self, line: Text) -> None:
        log = self.query_one("#log", RichLog)
        log.write(line, scroll_end=self.follow_enabled)
        self.log_lines += 1

    def action_toggle_follow(self) -> None:
        self.follow_enabled = not self.follow_enabled
        log = self.query_one("#log", RichLog)
        log.auto_scroll = self.follow_enabled
        if self.follow_enabled:
            log.scroll_end(animate=False)
        self.render_header()

    # ------------------------------------------------------------ actions

    @on(DataTable.RowSelected, "#boxes")
    def on_row_selected(self) -> None:
        self.action_open_runs()

    def action_open_runs(self) -> None:
        box = self.selected_box
        if box is None:
            return
        self.push_screen(RunsScreen(self.backend, box))

    def action_stop_run(self) -> None:
        box = self.selected_box
        if box is None:
            return
        if not box.has_running_run:
            self.set_error(f"{box.name} has no running run to stop", "stop")
            return
        assert box.run is not None
        runid = box.run.id

        def confirmed(answer: bool | None) -> None:
            if answer:
                self.send_stop(box, runid)

        self.push_screen(ConfirmScreen(f"stop run {runid} on {box.name}?"), confirmed)

    @work(group="stop", exit_on_error=False)
    async def send_stop(self, box: Box, runid: str) -> None:
        try:
            await self.backend.stop_run(box.target, runid)
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            self.set_error(reason, "stop")
            # A toast as well as the header line: the header is one line that the next poll's
            # notice takes over within an interval, and a stop that failed must not read as
            # nothing happened. Success already toasts; this is the other half of that.
            self.notify(
                f"stop-run failed for {runid} on {box.name}: {reason}",
                severity="error",
                markup=False,
            )
            return
        if self.error_source == "stop":
            self.clear_error()
        # markup=False for the same reason as the header: the box name and runid are not ours.
        self.notify(f"stop-run sent for {runid} on {box.name}", markup=False)
        self.poll_tick()

    def action_attach(self) -> None:
        box = self.selected_box
        if box is None:
            return
        if not box.is_running:
            self.set_error(f"{box.name} is stopped; nothing to attach to", "attach")
            return
        try:
            argv = self.backend.attach_argv(box.target, attach_session(box))
        except BackendError as exc:
            self.set_error(f"attach: {exc}", "attach")
            return
        if argv is None:
            self.set_error("attach is unavailable in fixture mode", "attach")
            return
        try:
            with self.suspend():
                code = subprocess.run(argv, check=False).returncode
        except SuspendNotSupported:
            self.set_error("attach needs a real terminal; cannot suspend here", "attach")
            return
        except OSError as exc:
            self.set_error(f"cannot run {argv[0]}: {exc}", "attach")
            return
        if code != 0:
            self.set_error(f"attach exited {code} ({' '.join(argv[1:])})", "attach")
            return
        if self.error_source == "attach":
            self.clear_error()
        self.poll_tick()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    async def action_quit(self) -> None:
        await self.stop_following()
        self.exit()
