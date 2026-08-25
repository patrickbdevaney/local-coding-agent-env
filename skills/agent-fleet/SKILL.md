---
name: agent-fleet
description: Launch, observe and coordinate other headless agent processes — additional clank runs, Claude Code, or any long-running agent or job of the user's own making. Use when work should proceed in the background, when several independent pieces can run at once, or when the user asks what a running agent is doing or wants one supervised.
---

# Supervising other agents

Another agent is just a process that writes to a log. You can start one, watch
its output, decide whether it is stuck, and stop it. Nothing here requires
special support — it is `setsid`, a log file, and `tail`.

## Launching

Detach properly, or it dies with your shell:

```bash
cd /path/to/work && setsid nohup <agent-command> > run.log 2>&1 < /dev/null &
```

Give every run its **own log file and its own directory**. Two agents editing
the same tree concurrently will corrupt each other's work; if the pieces touch
the same files, run them in sequence or in separate git worktrees:

```bash
git worktree add ../work-b -b task-b
```

Headless forms worth knowing:

```bash
clank -p "task"                 # one shot, prints the answer
clank -p --loop "task"          # keeps going until it reports completion
```

Any other agent with a non-interactive mode works the same way — the contract
is only "writes progress to stdout".

## Observing

Poll; do not block. `tail -f` in an agent turn is a hang.

```bash
tail -n 40 run.log              # what has it said lately
pgrep -f 'run.log' >/dev/null   # is it still alive
```

Judge progress by **change**, not by the presence of output:

- output growing, and the content differs → working, leave it
- output growing but repeating the same tool call or the same paragraph →
  **it is looping.** Stop it. It will not recover on its own.
- no output for far longer than a step should take → stuck, likely on an
  interactive prompt it cannot answer
- process gone and no completion line → it died; read the tail for why

Check back on a timescale that matches the work — a build is seconds, a research
pass is minutes. Do not poll every second; do not disappear for an hour either.

## Coordinating several

Keep it simple and inspectable:

- one directory and one log per agent
- a plain markdown file as the shared plan, one section per agent, so state
  survives any of them dying
- collect results by reading their logs and their outputs, then synthesise
  yourself — do not have them talk to each other

## Stopping

```bash
pkill -f 'unique-substring-of-the-command'
```

Match on something genuinely unique. A loose pattern will match **your own
shell** and kill the turn — this has actually happened. Prefer finding the pid
first (`pgrep -f ... | head -1`) and killing that.

Always report what a supervised agent actually did, including failure. A
background run that died is not a run that succeeded quietly.
