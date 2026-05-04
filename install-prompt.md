# Install Claude Code status line

Paste this entire prompt into Claude Code on the target machine. Claude will detect the OS, fetch the latest source files from this repo, merge the `statusLine` block into your `settings.json`, run a smoke test, and report.

---PROMPT START---

Please install the cross-platform Claude Code status line from `https://github.com/or2ooo/claude-statusline`. Do all of the following without asking me to confirm intermediate steps:

1. **Resolve `$CCDIR`** (the Claude Code config dir):
   - If `CLAUDE_CONFIG_DIR` is set, use it.
   - Else if `~/.claude-px/` exists, use it.
   - Else use `~/.claude/`.
   - Create the directory if it doesn't exist.

2. **Detect OS** — Windows vs. Unix (macOS / Linux). The harness exposes the platform; otherwise check `uname -s` or `$env:OS`.

3. **Fetch the runtime files** from `https://raw.githubusercontent.com/or2ooo/claude-statusline/main/`:
   - `statusline.py` → `$CCDIR/statusline.py` (every OS)
   - `statusline.sh` → `$CCDIR/statusline.sh` (every OS — harmless on Windows, used if you ever run it via Git Bash or WSL)
   - `statusline.cmd` → `$CCDIR/statusline.cmd` (**Windows only**)

   Use `curl -fsSL <url> -o <dest>` on Unix or `Invoke-WebRequest -UseBasicParsing -Uri <url> -OutFile <dest>` on Windows. If a fetch fails, abort and report.

4. **`chmod +x`** the `.sh` and `.py` (no-op on Windows but harmless if attempted).

5. **Merge the `statusLine` block** into `$CCDIR/settings.json`. If the file doesn't exist, create it as `{}`. Read it, parse as JSON, set the `statusLine` key — preserving every other key exactly. Write back as valid JSON with 2-space indentation.

   - **Windows**: `command` should be the absolute path to `statusline.cmd`, using forward slashes (e.g. `C:/Users/<you>/.claude/statusline.cmd`). Do **not** use `~/`, it doesn't expand under cmd.exe.
   - **Unix**: `command` should be the absolute path to `statusline.sh` (or the `~/.claude-px/statusline.sh` form when `$CCDIR=~/.claude-px`).

   The block:
   ```json
   {
     "type": "command",
     "command": "<resolved path>",
     "padding": 0,
     "refreshInterval": 30
   }
   ```

6. **Smoke test** — pipe this synthetic JSON to the shim:
   ```json
   {"model":{"display_name":"Test"},"workspace":{"current_dir":"<your CWD>"},"context_window":{"used_percentage":42,"context_window_size":200000}}
   ```
   Expect two lines of output, line 1 containing `Test`, line 2 containing `Ctx 42%`. ANSI escape codes are normal. If Python 3 is not installed, stop and tell me to install it.

7. **Report** which `$CCDIR` resolved, what was written (sh / cmd / py / settings), and the smoke-test output. Don't restart Claude Code — that's on me.

---PROMPT END---

## Why this is short

The install prompt fetches the source files at runtime instead of embedding them. The repo is the single source of truth — `git push` is the only thing needed to ship updates; the prompt itself rarely changes.

## After Claude finishes

Restart any running Claude Code session. The status line will render on the next refresh.
