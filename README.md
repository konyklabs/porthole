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

Left, a table of boxes: a state dot, the box's egress mode, the name, the
current run's state, elapsed time, turns, cost and last tool line. The egress
mode is what the CLI reports as the box's outbound network policy: `deny`
(dimmed; the normal state), `observe` (yellow), `open` (red) or `unknown`
(dimmed). `unknown` means the CLI could not establish the live mode: the box
is stopped, the firewall unit is not active, or the mode file and the live
ruleset disagree. When the CLI says why (its optional `firewall_detail`), the
reason is appended dimmed at the end of that box's row, and a disagreement
between the file and the ruleset is also raised as the header error line,
since that is the one case worth acting on. Running runs sort first, then
running boxes, then stopped ones. A run's state is one of `running`, `done`,
`failed`, `stopped`, `lost` (its process vanished without recording an exit)
or `unknown` (no status file); only `running` counts as running, `failed` and
`lost` share a colour, `unknown` is dimmed. The header shows how many boxes and running
runs there are and how old the status is.

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
Runs and logs are answered per box the way the CLI would: the box with no
runs has an empty runs list and nothing to follow. `a` is unavailable
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
