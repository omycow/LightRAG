#!/usr/bin/env python3
"""
Auto-restart wrapper for benchmark shell scripts.

Detects network / rate-limit errors in the log and restarts the script
at the right time:
  - Qwen/local  : OpenAI ConnectionError → server down → wait QWEN_WAIT_MIN min
  - Claude CLI  : usage limit (rc=1, "usage limit" in output) → wait until reset
  - Groq        : 429 rate limit → wait GROQ_WAIT_MIN min

Usage:
    python benchmark/auto_runner.py <script.sh> [log_file]
    python benchmark/auto_runner.py run_benchmark_final.sh
    python benchmark/auto_runner.py run_benchmark_claude_cli.sh
    python benchmark/auto_runner.py run_benchmark_groq.sh

The script continues until it detects "Done:" in the log (success),
or CONNECTION_MAX_RETRIES consecutive connection failures.
"""

import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# ── Tuning ────────────────────────────────────────────────────────────────────
QWEN_WAIT_MIN         = int(os.environ.get("QWEN_WAIT_MIN", "30"))    # minutes to wait when Qwen down
GROQ_WAIT_MIN         = int(os.environ.get("GROQ_WAIT_MIN", "5"))     # minutes between Groq 429 retries
CLAUDE_DEFAULT_WAIT_H = int(os.environ.get("CLAUDE_WAIT_H", "4"))     # hours if no reset time parsed
CONNECTION_MAX_RETRIES= int(os.environ.get("MAX_RETRIES", "20"))      # max reconnect attempts before giving up
POLL_INTERVAL_S       = 30                                             # log poll frequency

# ── Error patterns ────────────────────────────────────────────────────────────
QWEN_CONNECTION_RE = re.compile(
    r"(Connection error|APIConnectionError|ConnectTimeout|Failed to connect|"
    r"openai.*connection|ConnectionRefused)", re.IGNORECASE
)
PROGRESS_RE = re.compile(r"Completed processing file \d+/\d+", re.IGNORECASE)
CLAUDE_LIMIT_RE = re.compile(
    r"(claude.*usage.?limit|usage.?limit.*claude|claude.*rate.?limit|"
    r"RateLimitError.*claude-cli|retry_after.*claude|"
    r"session.?limit|hit your.*limit|resets.*(?:am|pm).*(?:Asia|Seoul|KST|UTC))",
    re.IGNORECASE
)
GROQ_LIMIT_RE = re.compile(
    r"rate_limit_exceeded|RateLimitError.*groq|groq.*rate.?limit|too many requests.*groq|openai.*rate.?limit.*error", re.IGNORECASE
)
GROQ_DAILY_RE = re.compile(
    r"requests per day.*limit|RPD.*limit|per day.*limit", re.IGNORECASE
)
DONE_RE = re.compile(r"^Done:", re.MULTILINE)
CLAUDE_RETRY_RE = re.compile(
    r"(?:resets? (?:at|in)|retry in|try again in)\s*"
    r"(?:(\d+)\s*hours?)?\s*(?:(\d+)\s*minutes?)?",
    re.IGNORECASE
)


def log(msg: str):
    print(f"[auto_runner {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def tail_new(path: str, pos: int) -> tuple[str, int]:
    """Read new content from pos onwards; return (new_text, new_pos)."""
    try:
        with open(path, "r", errors="replace") as f:
            f.seek(pos)
            chunk = f.read()
            return chunk, f.tell()
    except FileNotFoundError:
        return "", pos


def parse_claude_wait(text: str) -> int:
    """Return seconds to wait. Handles two formats:
      - 'resets in Xh Ym' / 'retry in X hours Y minutes'
      - 'resets 4:10pm (Asia/Seoul)' — absolute clock time
    """
    import datetime as _dt
    # Try absolute time format: "resets 4:10pm" or "resets at 4:10pm"
    abs_m = re.search(r"resets\s+(?:at\s+)?(\d{1,2}):(\d{2})(am|pm)", text, re.IGNORECASE)
    if abs_m:
        hour, minute, ampm = int(abs_m.group(1)), int(abs_m.group(2)), abs_m.group(3).lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        now = _dt.datetime.now()
        reset = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if reset <= now:
            reset += _dt.timedelta(days=1)
        secs = int((reset - now).total_seconds()) + 60  # +1min buffer
        return secs
    # Try relative format: "resets in Xh Ym" / "retry in X hours Y minutes"
    m = CLAUDE_RETRY_RE.search(text)
    if m:
        h = int(m.group(1) or 0)
        mins = int(m.group(2) or 0)
        secs = h * 3600 + mins * 60
        if secs > 0:
            return secs
    return CLAUDE_DEFAULT_WAIT_H * 3600


def sleep_with_countdown(seconds: int, reason: str):
    log(f"Waiting {seconds//60}m {seconds%60}s — {reason}")
    step = 300  # report every 5 min
    elapsed = 0
    while elapsed < seconds:
        chunk = min(step, seconds - elapsed)
        time.sleep(chunk)
        elapsed += chunk
        remaining = seconds - elapsed
        if remaining > 0:
            log(f"  … {remaining//60}m remaining")
    log("Wait complete, restarting.")


def run_once(script_path: Path, log_path: Path) -> str:
    """
    Launch the benchmark script, tail its log, return:
      'done'         — completed successfully
      'qwen_down'    — Qwen server connection error
      'claude_limit' — Claude CLI usage limit hit
      'groq_limit'   — Groq rate limit hit
      'crash'        — non-zero exit for unknown reason
    """
    log(f"Starting: {script_path.name}")
    proc = subprocess.Popen(
        ["bash", str(script_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(script_path.parent.parent.parent),  # LightRAG/
    )

    # Give the script a moment to start (tee will truncate/create the log)
    time.sleep(8)
    # Start reading from current end of file so old content is skipped
    try:
        pos = os.path.getsize(str(log_path))
    except FileNotFoundError:
        pos = 0
    consecutive_conn_errors = 0
    CONN_THRESHOLD = 10  # X error-only windows in a row before declaring "server down"

    while True:
        # Check if process has exited
        ret = proc.poll()

        new_text, pos = tail_new(str(log_path), pos)

        if new_text:
            if DONE_RE.search(new_text):
                log("Detected 'Done:' — benchmark completed successfully.")
                return "done"
            if CLAUDE_LIMIT_RE.search(new_text):
                log("Detected Claude CLI usage limit.")
                proc.terminate()
                return "claude_limit"
            if GROQ_LIMIT_RE.search(new_text):
                log("Detected Groq rate limit.")
                proc.terminate()
                return "groq_limit"
            if QWEN_CONNECTION_RE.search(new_text):
                if PROGRESS_RE.search(new_text):
                    # Errors present but files still completing → transient, reset
                    consecutive_conn_errors = 0
                else:
                    consecutive_conn_errors += 1
                    if consecutive_conn_errors >= CONN_THRESHOLD:
                        log(f"Detected {CONN_THRESHOLD} consecutive error-only windows — server appears down.")
                        proc.terminate()
                        return "qwen_down"
            else:
                # No connection error in this window → reset
                if new_text.strip():
                    consecutive_conn_errors = 0

        if ret is not None:
            log(f"Process exited with code {ret}")
            # Re-read tail in case we missed final lines
            new_text2, _ = tail_new(str(log_path), pos)
            if DONE_RE.search(new_text2):
                return "done"
            return "done" if ret == 0 else "crash"

        time.sleep(POLL_INTERVAL_S)


def main():
    if len(sys.argv) < 2:
        print("Usage: auto_runner.py <script.sh> [log_file]")
        sys.exit(1)

    script_path = Path(sys.argv[1])
    if not script_path.is_absolute():
        script_path = SCRIPT_DIR / script_path

    if not script_path.exists():
        print(f"Script not found: {script_path}")
        sys.exit(1)

    # Derive log path from script name or use override
    if len(sys.argv) >= 3:
        log_path = Path(sys.argv[2])
    else:
        stem = script_path.stem.replace("run_benchmark_", "benchmark_")
        log_path = SCRIPT_DIR / "results" / f"{stem}.log"

    log(f"Script : {script_path}")
    log(f"Log    : {log_path}")

    attempts = 0
    while attempts < CONNECTION_MAX_RETRIES:
        attempts += 1
        log(f"Attempt #{attempts}")
        result = run_once(script_path, log_path)

        if result == "done":
            log("SUCCESS — benchmark complete.")
            sys.exit(0)

        elif result == "qwen_down":
            wait_s = QWEN_WAIT_MIN * 60
            sleep_with_countdown(wait_s, f"Qwen server down — check {QWEN_WAIT_MIN}m later")

        elif result == "claude_limit":
            # Read the last portion of the log to parse reset time
            try:
                with open(log_path, "r", errors="replace") as f:
                    tail = f.read()[-3000:]
            except FileNotFoundError:
                tail = ""
            wait_s = parse_claude_wait(tail)
            sleep_with_countdown(wait_s + 60, "Claude CLI usage limit — waiting for reset")

        elif result == "groq_limit":
            # Check if it's a daily (RPD) limit → wait until midnight UTC
            try:
                with open(log_path, "r", errors="replace") as f:
                    tail = f.read()[-2000:]
            except FileNotFoundError:
                tail = ""
            if GROQ_DAILY_RE.search(tail):
                import datetime as _dt
                now = _dt.datetime.utcnow()
                midnight = (now + _dt.timedelta(days=1)).replace(hour=0, minute=5, second=0)
                wait_s = int((midnight - now).total_seconds())
                sleep_with_countdown(wait_s, f"Groq daily (RPD) limit — waiting until midnight UTC ({midnight.strftime('%H:%M')})")
            else:
                wait_s = GROQ_WAIT_MIN * 60
                sleep_with_countdown(wait_s, f"Groq TPM rate limit — waiting {GROQ_WAIT_MIN}m")

        elif result == "crash":
            log("Unknown crash — waiting 2 minutes before retry")
            time.sleep(120)

    log(f"Giving up after {CONNECTION_MAX_RETRIES} attempts.")
    sys.exit(1)


if __name__ == "__main__":
    main()
