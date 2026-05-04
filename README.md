# claude-statusline

Cross-platform two-line color status line for [Claude Code](https://claude.ai/code). Renders model, project dir, git branch (with dirty / ahead / behind markers), context-window usage, 5-hour and 7-day rate limits, and a clock — refreshed every 30 seconds.

```
 Sonnet 4.6  │  my-project │  main ✱2 ↑1 │ ⚡ high │ ⏱ 4m
Ctx 38% · 76k/200k │ ⏰ 5h 22% · 3h 12m left │ 🕒 14:23
```

Designed to stay quiet (gray) when things are calm, and color (yellow / red) only when state crosses a threshold worth attention.

## Install

On the target machine, open Claude Code in any directory and paste the contents of [`install-prompt.md`](./install-prompt.md). Claude will:

- detect `$CCDIR` (`$CLAUDE_CONFIG_DIR` → `~/.claude-px/` → `~/.claude/`),
- fetch `statusline.py` + the right shim for the OS,
- merge the `statusLine` block into your `settings.json` without disturbing other keys,
- run a smoke test,
- report.

After it finishes, restart any running Claude Code session.

### Manual install (if you'd rather not paste a prompt)

```bash
# Unix
curl -fsSL https://raw.githubusercontent.com/or2ooo/claude-statusline/main/statusline.py -o ~/.claude/statusline.py
curl -fsSL https://raw.githubusercontent.com/or2ooo/claude-statusline/main/statusline.sh -o ~/.claude/statusline.sh
chmod +x ~/.claude/statusline.sh ~/.claude/statusline.py
# then add the statusLine block to ~/.claude/settings.json — see install-prompt.md
```

```powershell
# Windows
$dst = "$env:USERPROFILE\.claude"
iwr https://raw.githubusercontent.com/or2ooo/claude-statusline/main/statusline.py -OutFile "$dst\statusline.py"
iwr https://raw.githubusercontent.com/or2ooo/claude-statusline/main/statusline.sh  -OutFile "$dst\statusline.sh"
iwr https://raw.githubusercontent.com/or2ooo/claude-statusline/main/statusline.cmd -OutFile "$dst\statusline.cmd"
# then add the statusLine block to $dst\settings.json — see install-prompt.md
```

## What's shown

**Line 1**
- vim mode (when active) — yellow
- model name — bold white
- subagent — gray
- project dir — gray
- worktree badge (yellow) **or** git branch with `✱<dirty>`, `↑<ahead>`, `↓<behind>` — gray, dirty count yellow
- output style (when not `default`) — gray
- effort level — gray (low/medium), yellow (high), red (xhigh/max)
- duration (only when ≥ 1 minute) — gray
- lines added/removed (when nonzero) — gray

**Line 2**
- context window: `Ctx <pct>% · <used>/<total>`
- 5-hour rate limit: `⏰ 5h <pct>% · <countdown>`
- 7-day rate limit: `7d <pct>%` (hidden under 50% to reduce noise)
- clock: `🕒 HH:MM`

Color thresholds are gray < 60% < yellow < 85% < red.

## Cross-platform notes

| | Unix | Windows |
|---|---|---|
| Shim | `statusline.sh` (calls `python3`) | `statusline.cmd` (calls `python -X utf8`) |
| `settings.json` `command` | absolute path to `.sh` | absolute path to `.cmd` |
| Git status cache | `tempfile.gettempdir()` | `tempfile.gettempdir()` (`%TEMP%`) |
| Python | `python3` | `python` (Microsoft Store / installer) |

The Windows shim adds `-X utf8` because Python defaults to the system codepage on Windows, which fails to encode the Unicode separators (`│`, `⏰`, etc.) on non-UTF-8 locales.

## Requirements

- Python 3.7+ (only the standard library is used — no pip installs).
- `git` on `PATH` if you want the branch / dirty / ahead / behind markers (they hide cleanly when missing).

## License

MIT — see [LICENSE](./LICENSE).
