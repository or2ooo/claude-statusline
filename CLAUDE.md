# claude-statusline — Conventions for Claude Code

This file is durable context. Read before changing anything.

## What this is

A cross-platform two-line color status line for Claude Code. Three runtime files (`statusline.py`, `statusline.sh`, `statusline.cmd`) plus a paste-to-Claude install prompt (`install-prompt.md`). Standard library only. No deps, no build step, no CI, no tests.

The repo is the **source of truth**. Installed machines fetch from `raw.githubusercontent.com/or2ooo/claude-statusline/main/<file>` at install time, so a `git push` to `main` is the only release mechanism — no tags, no versions.

## Load-bearing invariants (do not break)

These caused real bugs during the initial Mac→Windows port. Don't undo them.

- **`statusline.py` uses `tempfile.gettempdir()`**, not `Path("/tmp")`. Hardcoding `/tmp` silently disables the git cache on Windows.
- **`statusline.cmd` invokes `python -X utf8 …`**. Without `-X utf8`, Python on Hebrew/Arabic-locale Windows defaults to cp1255/cp1256 and crashes when printing the Unicode separators (`│`, `⏰`, `✱`). UTF-8 mode forces stdout to UTF-8 regardless of system codepage.
- **`statusline.cmd` calls `python`**, not `python3`. Windows ships `python` from the Microsoft Store / installer; `python3` is a Unix convention.
- **`.gitattributes` is load-bearing.** `*.sh` and `*.py` must be `eol=lf` (CRLF in a shebang line breaks `/usr/bin/env`). `*.cmd` must be `eol=crlf`. Don't delete this file.
- **`git status --no-optional-locks`** in `get_git_info`. Without this flag, the 30-second status refresh would create `.git/index.lock` and conflict with foreground commits/rebases.
- **`subprocess.run(..., timeout=2)`** for the git call. A stuck git process must not freeze the status line.

## Don't break the install model

The install path is **fetch at runtime**, not embed. `install-prompt.md` is short on purpose — it tells Claude on a new PC to `curl` / `Invoke-WebRequest` the source files from `main`. Changing this to embed file contents would mean every script change needs a prompt rewrite *and* every existing install gets stale until users re-paste.

When updating `statusline.py`: just push to `main`. Existing installs re-fetch nothing — they keep what they have until the user re-runs the install prompt. That's intentional (no auto-update without consent).

## Adding a new segment

Two-step:
1. Add the segment to `build_segments` in `statusline.py`. Each segment is a 3-tuple `(priority, plain_text, ansi_text)`. Priority 100 = always shown; lower priority drops first when terminal is narrow.
2. Update the **"What's shown"** table in `README.md` to match. Out-of-sync README is the most likely drift.

Color rules (consistent across segments):
- Identity (model, dir, branch): plain white / gray.
- State that's normal: gray.
- State worth attention: yellow at ≥60%, red at ≥85%.
- Hide-when-quiet: e.g., `⏱` hidden under 1 minute, `7d` hidden under 50%.

## Smoke test before pushing

```powershell
# Windows
'{"model":{"display_name":"T"},"workspace":{"current_dir":"."},"context_window":{"used_percentage":42,"context_window_size":200000}}' | & ".\statusline.cmd"
```

```bash
# macOS / Linux
echo '{"model":{"display_name":"T"},"workspace":{"current_dir":"."},"context_window":{"used_percentage":42,"context_window_size":200000}}' | ./statusline.sh
```

Two lines, line 1 contains `T`, line 2 contains `Ctx 42%`. ANSI escapes are normal.

## Things to avoid

- **No dependencies.** Standard library only. Bundle size matters because the script runs every 30s on every Claude Code session.
- **No CI / no tests.** Overkill for ~290 lines of stdlib Python; manual smoke test is enough.
- **No embedding source in `install-prompt.md`.** Single source of truth lives in the runtime files.
- **No real names / personal emails.** Author identity stays as `or2ooo` / `or2ooo@users.noreply.github.com` — this is a public repo.
- **No versioning / release tags.** `main` is the release.
- **`--no-verify`** on commits — never.
