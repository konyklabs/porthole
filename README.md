# porthole

A terminal window into every agent box.

porthole is a small Textual app for the host machine that lists every
[agent-box](https://github.com/konyklabs/agent-box) you have, shows the run
each one is on, streams that run's event log, and lets you stop a run or
attach a terminal, all from one screen.

## The boundary

porthole is a renderer. It calls the `agentbox` CLI and reads its `--json`
output. It never calls `limactl`, never reads guest files, never parses
transcripts, never opens the network. That boundary is the point of the
project: the CLI is the only thing that crosses into a box, and porthole only
shows what the CLI already scrubbed.

Concretely, the five CLI calls it makes are `status --json`, `runs --json --
<repo>`, `logs -f --json -- <repo> [runid]`, `stop-run -- <repo> [runid]` and
`attach -- <repo> [session]`. Nothing is written to disk. No telemetry.

Five is still five. The standing session, channel, toolchain and leftovers
columns and notices all come out of `status --json`, which already carried them
— reading and answering a box's messages, and the fleet triage view, are
deliberately not here yet, because each is a sixth call and its own design.

Two things about that promise. Textual's command palette is turned off in
porthole, because its Screenshot command would write an SVG of the screen to
disk; there is no key that saves anything. And the promise describes
porthole's own behaviour: Textual's developer environment variables
(`TEXTUAL=devtools` opens a local websocket to `textual console`,
`TEXTUAL_LOG=<path>` writes Textual's log to a file) are the documented
exception, off unless you set them.

## Install

From a checkout:

```
uv sync
uv run porthole
```

As a tool, straight from git:

```
uv tool install git+https://github.com/konyklabs/porthole
porthole
```

Python 3.12 or newer. The `agentbox` CLI must be on `PATH`, or named with
`--agentbox PATH` or the `AGENTBOX` environment variable.

## Options

```
porthole [--agentbox PATH] [--interval SECS] [--fixtures DIR]
```

| Option | Meaning |
|---|---|
| `--agentbox PATH` | The CLI to call. Default: `$AGENTBOX`, then `agentbox` on `PATH`. |
| `--interval SECS` | How often to poll `status --json`. Default 3. |
| `--fixtures DIR` | Replay `status.json`, `runs.json` and `logs.jsonl` from DIR instead of calling any CLI. |

## The window

Left, a table of boxes, ten columns: a state dot, the box's egress mode, the
name, the current run's state, elapsed time, turns, cost, the last tool line,
the standing session's state and what is pending in the box's channel. The
egress mode is what the CLI reports as the box's outbound network policy:
`deny` (dimmed; the normal state), `observe` (yellow), `open` (red) or
`unknown` (dimmed). `unknown` means the CLI could not establish the live mode:
the box is stopped, the firewall unit is not active, or the mode file and the
live ruleset disagree. When the CLI says why (its optional `firewall_detail`),
the reason is appended dimmed at the end of that box's row. Running runs sort
first, then boxes whose newest run is waiting for an answer, then running
boxes, then stopped ones. A run's state is one of `running`, `done`,
`failed`, `stopped`, `lost` (its process vanished without recording an exit)
or `unknown` (no status file); only `running` counts as running, `failed` and
`lost` share a colour, `unknown` is dimmed.

The last two columns are the box's other loop, the standing interactive session
inside it:

| Column | Shows |
|---|---|
| `session` | the standing session's state — `working`, `idle`, `waiting` or `gone` — and nothing at all when the box has never run one |
| `chan` | at most three tokens, and empty when nothing pends: `1h` a handoff the host has not read, `2↓` a request the box has not picked up, `+2` finished runs the session has not been told about |

The header shows how many boxes and running runs there are, how many runs are
waiting for an answer, how many messages are pending across the fleet, and how
old the status is. The two new counts are omitted when they are zero: a header
that always says `0 waiting` stops being read.

One header line is also where four kinds of notice go, in priority order — what
can leave the box first, then what invalidates the work, then what costs
resources, then what was silently dropped:

| Notice | Raised when |
|---|---|
| `egress` | a box's recorded mode and its live ruleset disagree |
| `toolchain` | a box's baseline toolchain has a finding. `unknown` is not one: nobody answered |
| `leftovers` | an earlier run in some box left a process, port, worktree or tmux session running |
| `channel` | a request the host queued is gone from the box's mailbox — the box removed it, unanswered |

The line shows the first of those and says how many it is not showing. Every one
of them is cleared by the next clean poll, including the new ones.

Right, the event log of the selected box's current or newest run, fed by
`logs -f --json`, one line per event, coloured by kind. It follows the tail
until you toggle that off. Changing the selection restarts the log and ends
the previous `logs` process.

A failed CLI call shows as one line in the header; the last good data stays
on screen. That covers all five calls: a failing `status` or `stop-run`, a
`runs` list that will not load (the modal says so too), a `logs` stream that
dies (the pane gets a "log stopped" line too; `r` retries it), and an `attach`
that exits non-zero or cannot start. A `status` call that outlives its
interval is killed and reported the same way rather than left running.

## Keys

| Key | Action |
|---|---|
| `j` / `k`, arrows | select a box |
| `enter` | runs list for the selected box (`runs --json`); `esc` closes |
| `s` | stop the selected run, after a confirmation |
| `a` | attach a terminal: porthole suspends, runs `agentbox attach`, resumes |
| `r` | refresh status now |
| `l` | toggle log follow |
| `?` | help |
| `q` | quit |

## Try it without a box

```
uv run porthole --fixtures tests/fixtures
```

That replays four fixture boxes: one running a run, one running with no run,
one whose newest run is `lost`, one stopped, and one in each egress mode.
Each also carries the four newer status keys on one axis: a box with a working
standing session and things pending in both directions, a box whose toolchain
answer is `unknown` (nobody answered — not a finding), a stopped box whose guest
keys are all `null`, and an all-clear one. Runs and logs are answered per box the
way the CLI would: the box with no runs has an empty runs list and nothing to
follow. The committed fixture raises **no** header notice, deliberately, so that
"a clean poll clears the header" stays testable; the loud cases and the priority
order run off mutated copies in the tests. `a` is unavailable
in fixture mode, since there is no terminal to attach to; porthole says so in
the header. `tests/fake-agentbox` is a stand-in CLI that answers from the same
fixtures; `AGENTBOX=tests/fake-agentbox uv run porthole` drives the real
subprocess path against it.

## In a browser

Textual can serve the same app in a browser with `textual serve`. That is
deliberately not wired up yet: a served porthole would need its own thinking
about who can reach the `stop-run` and `attach` keys, and the terminal is
enough for now.

## Development

```
uv sync
uv run ruff check .
uv run pytest -v
```

Tests run against `tests/fake-agentbox` and the fixtures only. They never
call `limactl` or a real `agentbox`.

## Licence

MIT. See `LICENSE`.
