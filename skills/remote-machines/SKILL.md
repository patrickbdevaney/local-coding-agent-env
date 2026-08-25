---
name: remote-machines
description: Operate other machines on the LAN or Tailscale over SSH — check whether a service is up, read logs, start or stop a server, inspect GPU or disk state, copy files. Use whenever the thing you need to observe or change lives on a different host than the one you are running on.
---

# Working on other machines

You have `ssh` and `scp` and permission to use them. Other hosts on the LAN or
the tailnet are as reachable as the local filesystem; treat them that way rather
than telling the user to go and check something themselves.

## Before assuming a host is unreachable

```bash
ssh -o ConnectTimeout=5 -o BatchMode=yes user@host 'hostname'
```

`BatchMode=yes` fails immediately instead of hanging on a password prompt, which
is what you want inside an agent — a hung prompt looks like a dead host and
burns the turn. If it fails on `Permission denied (publickey)`, key auth is not
set up; say so rather than retrying, and suggest `ssh-copy-id`.

## Useful shapes

```bash
# is a service up?
curl -sf --max-time 6 http://HOST:PORT/health

# what is the GPU doing?
ssh user@host 'nvidia-smi --query-gpu=name,memory.used,utilization.gpu --format=csv,noheader'

# tail a remote log without an interactive session
ssh user@host 'tail -n 200 ~/service.log'

# start something that must survive the SSH session ending
ssh user@host 'setsid nohup ~/start.sh > ~/start.log 2>&1 < /dev/null &'
```

That last one matters: a plain `ssh host './server.sh &'` dies when the
connection closes. `setsid nohup ... < /dev/null &` is what actually detaches.

## Rules

- Read before you write. Look at a config or a log before changing it.
- Prefer the host's own scripts (`~/qwen-start.sh`, a systemd unit) over
  hand-rolled command lines — they encode flags that matter.
- Never run something destructive on a remote host without saying what you are
  about to do first. Being on another machine makes mistakes harder to see and
  harder to undo.
- If a remote command hangs, it is usually an interactive prompt. Add
  `BatchMode=yes`, `-n`, or `< /dev/null`.
