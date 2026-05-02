# Self-hosted GitHub Actions runner — setup guide

`zakia-server` (a Mac mini at home) has no public IP, so the standard "GitHub
SSHs into your server" deploy pattern doesn't work. Instead we install
GitHub's self-hosted runner on the server: it dials out to GitHub and polls
for jobs. When you push to `main`, the runner picks up the job and runs
`.github/workflows/deploy.yml` *locally* on the server — `rsync` to
`~/handler-one-bot/`, `pip install`, restart the bot.

This doc covers replicating the runner setup on a fresh machine. The
workflow itself is in [`../.github/workflows/deploy.yml`](../.github/workflows/deploy.yml).

## Prerequisites on the host

- macOS 12+ (Apple Silicon or Intel; this guide assumes arm64)
- The repo cloned to `~/handler-one-bot/` with a working
  `.venv/` and a populated `.env`. See the [main README](../README.md)
  for that setup.
- The bot already runs successfully via
  `~/handler-one-bot/.venv/bin/python -m scripts.run`. Get this
  green before adding the runner — debugging two layers at once is no fun.
- **Don't install under `~/Documents/`, `~/Desktop/`, or `~/Downloads/`.**
  macOS TCC silently blocks launchd-spawned processes (which the runner is)
  from writing to those locations. Symptom: rsync hangs forever with no
  output and the workflow times out. Stick to `~/handler-one-bot/` (or any
  non-TCC-protected path).

## One-time GitHub repo settings

In `Settings → Actions → General`:
- **Fork pull request workflows from outside collaborators** → set to
  *Require approval for all outside collaborators*. The deploy workflow
  triggers only on `push: branches: [main]`, but this is belt-and-suspenders
  for any future workflows that might fire on PRs.
- **Workflow permissions** → leave at "Read repository contents" (the
  deploy workflow doesn't need write access to GitHub itself).

In `Settings → Actions → Runners → Runner groups → Default`:
- Make sure the runner is restricted to this repository only (default for
  free/Pro accounts).

## Install the runner

The release version below was current at setup. Bump to the latest from
<https://github.com/actions/runner/releases> when replicating.

```bash
ssh zakia-server '
  set -e
  mkdir -p ~/actions-runner && cd ~/actions-runner
  curl -fsSL -o runner.tar.gz \
    https://github.com/actions/runner/releases/download/v2.334.0/actions-runner-osx-arm64-2.334.0.tar.gz
  tar xzf runner.tar.gz && rm runner.tar.gz
'
```

Get a registration token (expires in 1 hour) using `gh` from your laptop:

```bash
TOKEN=$(gh api -X POST repos/CommanderBlop/Handler-One-Bot/actions/runners/registration-token --jq .token)
```

Or fetch one manually from `Settings → Actions → Runners → New self-hosted
runner` in the GitHub UI.

Configure the runner — it talks to GitHub during this step to register:

```bash
ssh zakia-server "cd ~/actions-runner && \
  ./config.sh \
    --url https://github.com/CommanderBlop/Handler-One-Bot \
    --token $TOKEN \
    --name zakia-server \
    --labels handler-deploy \
    --runnergroup default \
    --work _work \
    --unattended --replace"
```

Key flags:
- `--labels handler-deploy` — what `runs-on:` in the workflow matches against.
  GitHub also tacks on `self-hosted, macOS, ARM64` automatically.
- `--unattended --replace` — don't prompt; if a runner with this name
  already exists, blow it away and re-register. Safe for re-runs.

Install + start as a launchd agent (auto-restarts on user login):

```bash
ssh zakia-server "cd ~/actions-runner && ./svc.sh install && ./svc.sh start"
```

Verify it shows up online:

```bash
gh api repos/CommanderBlop/Handler-One-Bot/actions/runners --jq '.runners[] | {name,status,busy}'
# expect: {"busy":false,"name":"zakia-server","status":"online"}
```

You can also see it under `Settings → Actions → Runners` as **Idle**.

## Operating the runner

```bash
# Service status / start / stop
ssh zakia-server "cd ~/actions-runner && ./svc.sh status"
ssh zakia-server "cd ~/actions-runner && ./svc.sh stop"
ssh zakia-server "cd ~/actions-runner && ./svc.sh start"

# Tail the runner's own logs (different from the bot's logs)
ssh zakia-server "tail -f ~/Library/Logs/actions.runner.CommanderBlop-Handler-One-Bot.zakia-server/*.log"

# Tail the bot logs (what the deploy workflow writes)
ssh zakia-server "tail -f ~/handler-one-bot/handler.log"
```

To trigger a manual deploy without pushing code: in the GitHub UI, go to
`Actions → Deploy to zakia-server → Run workflow`.

## Caveats / things to know

- **LaunchAgent vs LaunchDaemon.** `./svc.sh install` creates a
  *LaunchAgent* in `~/Library/LaunchAgents/`. It runs only when
  `zakiaserver` is logged in to a GUI session. That's typical for a
  Mac mini with auto-login enabled. If you want it to start before any
  user logs in (e.g. after a reboot of a headless box), promote the
  plist to `/Library/LaunchDaemons/` and adjust the `UserName` key —
  but this requires sudo and a small bit of plist surgery.
- **Tailscale not required.** The runner phones outbound to GitHub. Your
  laptop only needs SSH access for `./svc.sh status` and ad-hoc debugging.
  Local LAN (`zakia-server.local`) and tailscale (`zakia-server-ts` →
  `100.107.204.34`) are both fine.
- **Bot restart in the workflow.** The deploy workflow runs `nohup ... &
  disown` inside a subshell to fully detach the bot from the runner step's
  process group. If the runner ever kills the bot when the step exits,
  the cleanest long-term fix is to also manage the bot itself via
  launchd (a separate plist) and have the workflow do
  `launchctl kickstart -k <label>` instead of `pkill` + `nohup`.
- **Secrets stay on the host.** `.env` is in the rsync exclude list, so
  the workflow can never overwrite or read it. No GitHub Secrets needed.

## Troubleshooting

### TCC: rsync hangs forever, workflow times out

macOS Transparency, Consent, and Control (TCC) protects `~/Documents/`,
`~/Desktop/`, `~/Downloads/`, network volumes, etc. Any process launched
from a launchd agent (which the runner is) gets a TCC profile that lacks
access to those locations by default. When such a process tries to read
or write a TCC-protected path, **macOS doesn't return EPERM** — it sends
a permission prompt to the user's GUI session. Headless launchd processes
have no GUI session, so the syscall blocks indefinitely.

Symptom in this repo: the `Sync working tree` step in `deploy.yml`
produces no output for 5 minutes and gets killed by `timeout-minutes`.

Two ways to fix:

1. **Keep your install paths off TCC-protected dirs (recommended).**
   This repo installs to `~/handler-one-bot/`, not `~/Documents/`, for
   exactly this reason. If you reference any other path that lives under
   `~/Documents/` (e.g. an external MCP server's working dir), the same
   problem will hit when the bot tries to read it.

2. **Grant the runner Full Disk Access (FDA).** On the Mac mini directly
   (or via Screen Sharing), open **System Settings → Privacy & Security →
   Full Disk Access**, click **+**, and add `~/actions-runner/runsvc.sh`
   (or the runner agent itself). After adding, restart the runner:
   `cd ~/actions-runner && ./svc.sh stop && ./svc.sh start`. Verify with
   a workflow run that touches a Documents path.

   You can sanity-check the current TCC grants with:
   ```bash
   sqlite3 ~/Library/Application\ Support/com.apple.TCC/TCC.db \
     'SELECT client, service, auth_value FROM access
      WHERE service LIKE "%FullDisk%" OR service LIKE "%Documents%";'
   ```

### Bot crashes on butler MCP startup after deploy

If `BUTLER_MCP_COMMAND` in `.env` points at a path under `~/Documents/`
and you haven't granted FDA to the runner, the bot will start but fail
to spawn butler MCP. Either move butler out of `~/Documents/` too, or
grant FDA per the previous section.

## Removing the runner

If you ever want to tear it down:

```bash
TOKEN=$(gh api -X POST repos/CommanderBlop/Handler-One-Bot/actions/runners/remove-token --jq .token)
ssh zakia-server "cd ~/actions-runner && ./svc.sh stop && ./svc.sh uninstall && ./config.sh remove --token $TOKEN"
```

This stops the launchd agent, deregisters the runner with GitHub, and
deletes the runner's local credentials. The repo's `.github/workflows/`
files stay; with no runner online, jobs just queue indefinitely until a
runner reappears (or you cancel them).
