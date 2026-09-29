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
GIT_CACHE_TTL_SECONDS = 10   # dirty / ahead / behind only; branch is never cached
GIT_CACHE_DIR = Path(tempfile.gettempdir())
GIT_BRANCH_TIMEOUT_S = 1      # symbolic-ref / rev-parse: ~20 ms even in huge repos
GIT_STATUS_TIMEOUT_S = 3      # status scans untracked files; can take seconds
NARROW_COLUMNS = 80
WIDE_FALLBACK_COLUMNS = 120

CTX_YELLOW_AT = 60
CTX_RED_AT = 85
LIMIT_YELLOW_AT = 60
LIMIT_RED_AT = 85
COST_YELLOW_AT = 5.0    # session USD (list-price estimate from Claude Code)
COST_RED_AT = 20.0
CACHE_YELLOW_BELOW_PCT = 70  # prompt-cache hit ratio, main conversation only
CACHE_RED_BELOW_PCT = 40
# Bedrock regional CRIS profiles bill +10% over list; flag their estimate with ≈
REGIONAL_MODEL_PREFIXES = ("eu.", "us.", "apac.", "us-gov.")

# Hide-when-quiet thresholds
DURATION_HIDE_BELOW_MS = 60_000  # hide ⏱ under 1 minute
SEVEN_DAY_HIDE_BELOW_PCT = 50    # hide 7d unless it's getting close
COST_HIDE_BELOW_USD = 0.01       # hide $ until the first cent
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


def fmt_cost(usd: float) -> str:
    if usd < 10:
        return f"${usd:.2f}"
    if usd < 1000:
        return f"${usd:.1f}"
    return f"${usd / 1000:.1f}k"


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


def _git(args, cwd: str, timeout: float):
    """Run a read-only git command; return stripped stdout, or None on any failure."""
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", *args],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _git_head(cwd: str):
    """Return (branch_or_short_sha, detached), or None if not a git repo.

    Fast (~20 ms) regardless of repo size, so it runs fresh every time.
    """
    branch = _git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd, GIT_BRANCH_TIMEOUT_S)
    if branch:
        return branch, False
    # Detached HEAD or mid-rebase
    sha = _git(["rev-parse", "--short", "HEAD"], cwd, GIT_BRANCH_TIMEOUT_S)
    if sha:
        return sha, True
    return None


def _git_status_counts(cwd: str):
    """Return (head, detached, dirty, ahead, behind) from `git status`, or None.

    `head` is the branch name, or the full commit ID when detached.
    """
    out = _git(["status", "--porcelain=v2", "--branch"], cwd, GIT_STATUS_TIMEOUT_S)
    if out is None:
        return None

    head = None
    oid = ""
    ahead = 0
    behind = 0
    dirty = 0
    for line in out.splitlines():
        if line.startswith("# branch.head"):
            parts = line.split(" ", 2)
            if len(parts) >= 3 and parts[2] != "(detached)":
                head = parts[2]
        elif line.startswith("# branch.oid"):
            parts = line.split(" ", 2)
            if len(parts) >= 3:
                oid = parts[2]
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

    if head is not None:
        return head, False, dirty, ahead, behind
    return oid, True, dirty, ahead, behind


def _counts_match(cached, branch: str, detached: bool) -> bool:
    """True if cached counts were computed on the HEAD we're showing now."""
    if not isinstance(cached, dict) or cached.get("detached") != detached:
        return False
    head = cached.get("head")
    if not isinstance(head, str) or not head:
        return False
    # Detached: cache holds the full commit ID; `branch` is its short form.
    return head.startswith(branch) if detached else head == branch


def get_git_info(cwd: str):
    """Return dict with branch/dirty/ahead/behind, or None if not a git repo.

    The branch is read fresh every run (fast). Only the slow `git status`
    extras are cached, tagged with the HEAD they belong to, and reused only
    while that HEAD is still current. If `git status` fails or times out, the
    branch still shows — with the last counts for that HEAD, or none.
    """
    if not cwd:
        return None
    if not Path(cwd).is_dir():
        return None

    head = _git_head(cwd)
    if head is None:
        return None
    branch, detached = head
    info = {"branch": branch, "detached": detached, "ahead": 0, "behind": 0, "dirty": 0}

    cache_key = hashlib.sha256(cwd.encode()).hexdigest()[:16]
    cache_file = GIT_CACHE_DIR / f"cc-statusline-gitcounts-{cache_key}"

    cached = None
    age = None
    try:
        age = time.time() - cache_file.stat().st_mtime
        cached = json.loads(cache_file.read_text())
    except (OSError, ValueError):
        cached = None

    matches = _counts_match(cached, branch, detached)

    def apply(entry):
        for k in ("dirty", "ahead", "behind"):
            v = entry.get(k)
            if isinstance(v, int):
                info[k] = v

    if matches and age is not None and age < GIT_CACHE_TTL_SECONDS:
        # Fresh counts, or a recent failure we shouldn't retry yet.
        apply(cached)
        return info

    status = _git_status_counts(cwd)
    if status is not None and _counts_match(
        {"head": status[0], "detached": status[1]}, branch, detached
    ):
        s_head, s_detached, dirty, ahead, behind = status
        entry = {"head": s_head, "detached": s_detached,
                 "dirty": dirty, "ahead": ahead, "behind": behind}
    else:
        # Timed out, failed, or HEAD moved mid-scan: keep the last counts for
        # this HEAD if we have them, else show none. Rewriting the entry
        # refreshes its mtime so a stuck `git status` isn't retried every run.
        entry = dict(cached) if matches else {"head": branch, "detached": detached}

    apply(entry)
    _write_cache(cache_file, entry)
    return info


def _write_cache(path: Path, entry: dict) -> None:
    """Atomic write, so concurrent sessions never read a half-written file."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(entry))
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


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

    # Session cost — Claude Code's list-price estimate; one ledger per session,
    # so it includes subagents, workflow agents and background helpers.
    # Bedrock regional profiles (eu./us./apac.) bill +10% over list → ≈ marker.
    usd = cost.get("total_cost_usd")
    if isinstance(usd, (int, float)) and not isinstance(usd, bool) and usd >= COST_HIDE_BELOW_USD:
        model_id = (data.get("model") or {}).get("id") or ""
        approx = "≈" if model_id.startswith(REGIONAL_MODEL_PREFIXES) else ""
        text = f"{approx}{fmt_cost(usd)}"
        if usd >= COST_RED_AT:
            col = C.RED
        elif usd >= COST_YELLOW_AT:
            col = C.YELLOW
        else:
            col = C.GRAY
        line1.append((75, text, f"{col}{text}{C.RESET}"))

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

    # Prompt cache — main conversation only (subagents aren't counted). Hit
    # ratio plus warm/cold state to judge the 5m vs 1h TTL trade-off.
    pc = data.get("prompt_cache") or {}
    ratio = pc.get("hit_ratio")
    if pc.get("requests") and ratio is not None:
        try:
            hit = float(ratio)
        except (TypeError, ValueError):
            hit = None
        if hit is not None:
            hit_pct = int(round(hit * 100 if hit <= 1 else hit))
            if hit_pct < CACHE_RED_BELOW_PCT:
                col = C.RED
            elif hit_pct < CACHE_YELLOW_BELOW_PCT:
                col = C.YELLOW
            else:
                col = C.GRAY
            if pc.get("warm"):
                left = fmt_remaining(pc.get("expires_at"))
                state = f"warm {left[:-5]}" if left and left.endswith(" left") else "warm"
            else:
                state = "cold"
            ttl = pc.get("ttl")
            ttl_part = f"{ttl} · " if ttl in ("5m", "1h") else ""
            plain = f"cache {hit_pct}% · {ttl_part}{state}"
            colored = (
                f"{C.GRAY}cache{C.RESET} {col}{hit_pct}%{C.RESET} "
                f"{C.DIM}· {ttl_part}{state}{C.RESET}"
            )
            line2.append((45, plain, colored))

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
