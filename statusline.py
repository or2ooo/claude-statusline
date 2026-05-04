#!/usr/bin/env python3
"""
Claude Code status line — global, two-line, color-coded.

Reads the JSON payload Claude Code pipes to stdin and emits up to two
ANSI-colored lines on stdout. Designed to be fast (<50 ms) and to render
gracefully when fields are absent or the terminal is narrow.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ---------- Tunables -------------------------------------------------
GIT_CACHE_TTL_SECONDS = 2
GIT_CACHE_DIR = Path(tempfile.gettempdir())
NARROW_COLUMNS = 80
WIDE_FALLBACK_COLUMNS = 120

CTX_YELLOW_AT = 60
CTX_RED_AT = 85
LIMIT_YELLOW_AT = 60
LIMIT_RED_AT = 85

# Hide-when-quiet thresholds
DURATION_HIDE_BELOW_MS = 60_000  # hide ⏱ under 1 minute
SEVEN_DAY_HIDE_BELOW_PCT = 50    # hide 7d unless it's getting close
# ---------------------------------------------------------------------

NO_COLOR = bool(os.environ.get("NO_COLOR"))


def _esc(seq: str) -> str:
    return "" if NO_COLOR else seq


def _fg(code: int) -> str:
    return _esc(f"\033[38;5;{code}m")


class C:
    RESET = _esc("\033[0m")
    BOLD = _esc("\033[1m")
    DIM = _esc("\033[2m")
    CYAN = _fg(51)
    BLUE = _fg(75)
    MAGENTA = _fg(207)
    YELLOW = _fg(220)
    RED = _fg(196)
    GREEN = _fg(82)
    GRAY = _fg(244)
    WHITE = _fg(255)


def color_for_pct(pct, yellow_at: int, red_at: int) -> str:
    """Minimalist palette: gray when calm, color only on threshold crossings."""
    if pct is None:
        return C.GRAY
    if pct >= red_at:
        return C.RED
    if pct >= yellow_at:
        return C.YELLOW
    return C.GRAY


def fmt_tokens(n) -> str:
    if n is None:
        return "—"
    if n < 1000:
        return str(int(n))
    return f"{n / 1000:.0f}k"


def fmt_duration_ms(ms) -> str:
    if not ms:
        return "0m"
    secs = int(ms) // 1000
    if secs < 60:
        return f"{secs}s"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m"
    hours = mins // 60
    return f"{hours}h{mins % 60:02d}m"


def fmt_remaining(resets_at) -> str | None:
    """Format Unix-epoch reset time as human countdown, or None if absent/past."""
    if resets_at is None:
        return None
    try:
        delta = int(resets_at) - int(time.time())
    except (TypeError, ValueError):
        return None
    if delta <= 0:
        return None
    if delta < 60:
        return "<1m left"
    mins = delta // 60
    if mins < 60:
        return f"{mins}m left"
    hours = mins // 60
    if hours < 24:
        rem_min = mins % 60
        return f"{hours}h left" if rem_min == 0 else f"{hours}h {rem_min}m left"
    days = hours // 24
    rem_h = hours % 24
    return f"{days}d left" if rem_h == 0 else f"{days}d {rem_h}h left"


def get_git_info(cwd: str):
    """Return dict with branch/dirty/ahead/behind, or None if not a git repo."""
    if not cwd:
        return None
    cwd_path = Path(cwd)
    if not cwd_path.exists():
        return None

    cache_key = hashlib.sha256(cwd.encode()).hexdigest()[:16]
    cache_file = GIT_CACHE_DIR / f"cc-statusline-git-{cache_key}"

    try:
        if cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < GIT_CACHE_TTL_SECONDS:
                return json.loads(cache_file.read_text())
    except (OSError, json.JSONDecodeError):
        pass

    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "status", "--porcelain=v2", "--branch"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    if result.returncode != 0:
        return None

    branch = None
    sha = None
    ahead = 0
    behind = 0
    dirty = 0

    for line in result.stdout.splitlines():
        if line.startswith("# branch.head"):
            parts = line.split(" ", 2)
            if len(parts) >= 3:
                branch = parts[2]
                if branch == "(detached)":
                    branch = None
        elif line.startswith("# branch.oid"):
            parts = line.split(" ", 2)
            if len(parts) >= 3:
                sha = parts[2]
        elif line.startswith("# branch.ab"):
            parts = line.split()
            if len(parts) >= 4:
                try:
                    ahead = int(parts[2].lstrip("+"))
                    behind = int(parts[3].lstrip("-"))
                except ValueError:
                    pass
        elif line and not line.startswith("#"):
            dirty += 1

    info = {
        "branch": branch or (sha[:7] if sha else None),
        "detached": branch is None,
        "ahead": ahead,
        "behind": behind,
        "dirty": dirty,
    }

    try:
        cache_file.write_text(json.dumps(info))
    except OSError:
        pass

    return info


def build_segments(data: dict):
    """Return (line1_segs, line2_segs); each seg = (priority, plain_text, ansi_text).

    Palette: identity is plain (white/gray); color (yellow/red) signals state.
    """
    line1: list = []
    line2: list = []

    # Vim mode (line 1, leading) — yellow because it's meaningful state
    vim_mode = (data.get("vim") or {}).get("mode")
    if vim_mode:
        text = f"[{vim_mode}]"
        line1.append((100, text, f"{C.BOLD}{C.YELLOW}{text}{C.RESET}"))

    # Model — bold white, the one identity anchor on the line
    model_name = (data.get("model") or {}).get("display_name") or "?"
    text = f" {model_name} "
    line1.append((100, text, f"{C.BOLD}{C.WHITE}{text}{C.RESET}"))

    # Subagent — gray (informational, not alert)
    agent_name = (data.get("agent") or {}).get("name")
    if agent_name:
        text = f"↪ {agent_name}"
        line1.append((85, text, f"{C.GRAY}{text}{C.RESET}"))

    # Project dir — gray
    cwd = (data.get("workspace") or {}).get("current_dir") or data.get("cwd", "")
    dir_name = os.path.basename(cwd) or cwd or "?"
    text = f" {dir_name}"
    line1.append((100, text, f"{C.GRAY}{text}{C.RESET}"))

    # Worktree (preferred) or branch
    wt_name = (data.get("worktree") or {}).get("name")
    if wt_name:
        # Worktree badge stays yellow — it warns "you're not on trunk"
        text = f" ⌥ wt:{wt_name}"
        line1.append((90, text, f"{C.YELLOW}{text}{C.RESET}"))
    else:
        git = get_git_info(cwd)
        if git and git.get("branch"):
            b = git["branch"]
            prefix = "" if git.get("detached") else " "
            plain = f"{prefix}{b}"
            # Branch itself is gray (clean = quiet); only the dirty flag colors.
            colored = f"{C.GRAY}{plain}{C.RESET}"
            if git["dirty"]:
                plain += f" ✱{git['dirty']}"
                colored += f" {C.YELLOW}✱{git['dirty']}{C.RESET}"
            if git["ahead"]:
                plain += f" ↑{git['ahead']}"
                colored += f" {C.GRAY}↑{git['ahead']}{C.RESET}"
            if git["behind"]:
                plain += f" ↓{git['behind']}"
                colored += f" {C.GRAY}↓{git['behind']}{C.RESET}"
            line1.append((90, plain, colored))

    # Output style — gray, only when not 'default'
    style_name = (data.get("output_style") or {}).get("name")
    if style_name and style_name != "default":
        text = f"🎨 {style_name}"
        line1.append((40, text, f"{C.GRAY}{text}{C.RESET}"))

    # Effort — color graded; this is the main "alert" segment
    effort = (data.get("effort") or {}).get("level")
    if effort:
        text = f"⚡ {effort}"
        if effort in ("low", "medium"):
            colored = f"{C.GRAY}{text}{C.RESET}"
        elif effort == "high":
            colored = f"{C.YELLOW}{text}{C.RESET}"
        else:  # xhigh, max
            colored = f"{C.RED}{text}{C.RESET}"
        line1.append((50, text, colored))

    # Duration — hidden when under 1 minute
    cost = data.get("cost") or {}
    dur_ms = cost.get("total_duration_ms", 0) or 0
    if dur_ms >= DURATION_HIDE_BELOW_MS:
        text = f"⏱ {fmt_duration_ms(dur_ms)}"
        line1.append((70, text, f"{C.GRAY}{text}{C.RESET}"))

    # Lines added / removed — hidden when both zero, plain gray otherwise
    added = cost.get("total_lines_added") or 0
    removed = cost.get("total_lines_removed") or 0
    if added or removed:
        text = f"+{added} −{removed}"
        colored = f"{C.GRAY}{text}{C.RESET}"
        line1.append((30, text, colored))

    # ---- Line 2 ----
    cw = data.get("context_window") or {}
    pct = cw.get("used_percentage")
    pct_int = int(pct) if pct is not None else None
    size = cw.get("context_window_size") or 200000
    used = int(pct_int * size / 100) if pct_int is not None else None

    pct_color = color_for_pct(pct_int, CTX_YELLOW_AT, CTX_RED_AT)
    pct_text = "—" if pct_int is None else f"{pct_int}%"
    tok_text = f"{fmt_tokens(used)}/{fmt_tokens(size)}"
    plain = f"Ctx {pct_text} · {tok_text}"
    colored = (
        f"{C.GRAY}Ctx{C.RESET} {pct_color}{pct_text}{C.RESET} "
        f"{C.DIM}· {tok_text}{C.RESET}"
    )
    line2.append((100, plain, colored))

    rl = data.get("rate_limits") or {}
    five = rl.get("five_hour") or {}
    if "used_percentage" in five:
        p = int(five["used_percentage"])
        col = color_for_pct(p, LIMIT_YELLOW_AT, LIMIT_RED_AT)
        suffix_plain = ""
        suffix_colored = ""
        remaining = fmt_remaining(five.get("resets_at"))
        if remaining:
            suffix_plain = f" · {remaining}"
            suffix_colored = f" {C.DIM}· {remaining}{C.RESET}"
        plain = f"⏰ 5h {p}%{suffix_plain}"
        colored = (
            f"{C.GRAY}⏰ 5h{C.RESET} {col}{p}%{C.RESET}{suffix_colored}"
        )
        line2.append((60, plain, colored))

    seven = rl.get("seven_day") or {}
    if "used_percentage" in seven:
        p = int(seven["used_percentage"])
        if p >= SEVEN_DAY_HIDE_BELOW_PCT:
            col = color_for_pct(p, LIMIT_YELLOW_AT, LIMIT_RED_AT)
            suffix_plain = ""
            suffix_colored = ""
            remaining = fmt_remaining(seven.get("resets_at"))
            if remaining:
                suffix_plain = f" · {remaining}"
                suffix_colored = f" {C.DIM}· {remaining}{C.RESET}"
            plain = f"7d {p}%{suffix_plain}"
            colored = (
                f"{C.GRAY}7d{C.RESET} {col}{p}%{C.RESET}{suffix_colored}"
            )
            line2.append((50, plain, colored))

    # Clock
    clock = time.strftime("%H:%M")
    text = f"🕒 {clock}"
    line2.append((80, text, f"{C.DIM}{text}{C.RESET}"))

    return line1, line2


def assemble_line(segments, max_width: int) -> str:
    sep_plain = " │ "
    sep_colored = f" {C.GRAY}│{C.RESET} "

    def total_plain(segs) -> int:
        if not segs:
            return 0
        return sum(len(s[1]) for s in segs) + (len(segs) - 1) * len(sep_plain)

    segs = list(segments)
    optional = sorted(
        [i for i, s in enumerate(segs) if s[0] < 100], key=lambda i: segs[i][0]
    )
    while total_plain([s for s in segs if s is not None]) > max_width and optional:
        idx = optional.pop(0)
        segs[idx] = None

    return sep_colored.join(s[2] for s in segs if s is not None)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print("[statusline: invalid input]", file=sys.stderr)
        return 1

    cols_env = os.environ.get("COLUMNS")
    if cols_env and cols_env.isdigit():
        cols = int(cols_env)
    else:
        cols = shutil.get_terminal_size((WIDE_FALLBACK_COLUMNS, 24)).columns

    line1_segs, line2_segs = build_segments(data)

    if cols < NARROW_COLUMNS:
        cw_pct = (data.get("context_window") or {}).get("used_percentage")
        if cw_pct is not None:
            p = int(cw_pct)
            col = color_for_pct(p, CTX_YELLOW_AT, CTX_RED_AT)
            line1_segs.append(
                (95, f"Ctx {p}%", f"{col}Ctx {p}%{C.RESET}")
            )
        print(assemble_line(line1_segs, cols))
        return 0

    print(assemble_line(line1_segs, cols))
    print(assemble_line(line2_segs, cols))
    return 0


if __name__ == "__main__":
    sys.exit(main())
