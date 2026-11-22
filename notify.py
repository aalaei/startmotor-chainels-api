#!/usr/bin/env python3
"""
Startmotor notifier: fetch new posts/events, keep only the important ones, send them to you.

Drop this file next to digest.py and refresh_token.py, then run it from cron.

    python notify.py --init      # first run: mark everything currently in the feed as seen, send nothing
    python notify.py             # normal run: classify new items, notify if anything matters
    python notify.py --dry-run   # classify and print, don't send, don't update state

Extra .env keys (on top of the CHAINELS_* ones digest.py already uses):

    # Which Claude to use for "is this important?"
    LLM_BACKEND=claude-cli                 # default: Claude Code CLI on your Claude subscription
                                           #   one-time setup: install Claude Code, run `claude` once and log in
    # LLM_BACKEND=api                      # or: Anthropic API, billed per use, needs ANTHROPIC_API_KEY
    # ANTHROPIC_API_KEY=sk-ant-...
    CLAUDE_MODEL=haiku                     # optional; alias (haiku/sonnet/opus) for the CLI,
                                           #   for the API use a full ID, e.g. claude-haiku-5-5
    CLAUDE_BIN=/home/you/.local/bin/claude # optional; cron's PATH is minimal, set this if `claude` isn't found
    NOTIFY_PROFILE=I live in unit X on floor Y, I have a bike in the storage, no car.   # optional, sharpens filtering

    # Email (Gmail: create an App Password, normal password won't work)
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USER=you@gmail.com
    SMTP_PASSWORD=app-password
    NOTIFY_EMAIL_TO=you@gmail.com

    # and/or Telegram
    TELEGRAM_BOT_TOKEN=123:abc
    TELEGRAM_CHAT_ID=123456
"""
import argparse
import hashlib
import json
import os
import shutil
import smtplib
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
STATE_FILE = HERE / "seen.json"
MAX_SEEN = 2000  # cap the state file


# ---------- config ----------

def load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() and not k.strip().startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


# ---------- fetching ----------

def run_digest(count, days):
    return subprocess.run(
        [sys.executable, str(HERE / "digest.py"), "--json", "--count", str(count), "--days", str(days)],
        cwd=HERE, capture_output=True, text=True,
    )


def fetch(count, days):
    """Run digest.py; if auth has expired, refresh cookies once and retry."""
    r = run_digest(count, days)
    if r.returncode != 0 and ("401" in r.stderr or "403" in r.stderr or "No auth" in r.stderr + r.stdout):
        print("Auth expired, refreshing cookies...", file=sys.stderr)
        ref = subprocess.run([sys.executable, str(HERE / "refresh_token.py")], cwd=HERE,
                             capture_output=True, text=True)
        if ref.returncode != 0:
            raise RuntimeError(f"refresh_token.py failed:\n{ref.stdout}\n{ref.stderr}")
        # refresh_token.py rewrote .env; digest.py re-reads it in its own process
        r = run_digest(count, days)
    if r.returncode != 0:
        raise RuntimeError(f"digest.py failed:\n{r.stderr or r.stdout}")
    return json.loads(r.stdout)


def item_key(kind, it):
    # digest.py's normalised output has no IDs, so fingerprint the stable fields
    if kind == "post":
        raw = f"post|{it.get('date')}|{it.get('channel')}|{it.get('title')}|{(it.get('body') or '')[:200]}"
    else:
        raw = f"event|{it.get('start')}|{it.get('title')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_seen():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return []


def save_seen(seen):
    STATE_FILE.write_text(json.dumps(seen[-MAX_SEEN:]))


# ---------- classification ----------

PROMPT = """You filter announcements from a residential building's community app (Startmotor, Rotterdam) for one resident.
Most posts are noise. Mark an item important ONLY if the resident would regret missing it:
- outages or disruptions: water, power, heating, hot water, internet, elevator, doors/access, parking, bike storage
- maintenance or inspections that affect their home, require access to it, or require them to do something
- deadlines, required actions, surveys with consequences, rule or policy changes, house rules, waste collection changes
- money: service costs, rent, fees, refunds
- safety: fire alarms tests, security incidents, emergencies
- official notices from building management or the community manager about the building
Not important: social chatter, neighbours selling/giving away things, lost & found, general community events
(unless an event is a mandatory/official residents' meeting).
{profile}
Return ONLY a JSON array, one object per input item, in the same order:
[{{"id": "<id>", "important": true/false, "category": "outage|maintenance|action|money|rules|safety|meeting|other",
   "summary": "<one sentence, English, what it means for the resident>",
   "action": "<what they must do, or empty>", "when": "<relevant date/time, or empty>"}}]
Posts may be in Dutch; always answer in English.

Items:
{items}"""


SYSTEM = ("You are a filter in an unattended notification pipeline, not a coding assistant. "
          "You have no tools. Reply with the requested JSON only, no prose, no code fences.")


def build_prompt(items):
    profile = os.environ.get("NOTIFY_PROFILE", "").strip()
    profile = f"About this resident: {profile}\n" if profile else ""
    return PROMPT.format(profile=profile, items=json.dumps(items, ensure_ascii=False, indent=1))


def ask_api(prompt):
    """Anthropic API, billed per token. Needs ANTHROPIC_API_KEY."""
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                 "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5"), "max_tokens": 4000,
              "system": SYSTEM, "messages": [{"role": "user", "content": prompt}]},
        timeout=120,
    )
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")


def find_claude():
    for cand in (os.environ.get("CLAUDE_BIN"), shutil.which("claude"),
                 str(Path.home() / ".local/bin/claude")):
        if cand and Path(cand).exists():
            return cand
    raise RuntimeError("Claude Code CLI not found. Install it (curl -fsSL https://claude.ai/install.sh | bash), "
                       "run `claude` once to log in, or set CLAUDE_BIN in .env.")


def ask_claude_cli(prompt):
    """Claude Code CLI in print mode, using whatever account `claude` is logged in with (your subscription).
    One turn, all tools disabled, nothing saved to session history."""
    cmd = [find_claude(), "-p",
           "--model", os.environ.get("CLAUDE_MODEL", "haiku"),
           "--output-format", "json",
           "--system-prompt", SYSTEM,
           "--tools", "",
           "--disallowedTools", "mcp__*",
           "--max-turns", "1",
           "--no-session-persistence"]
    env = dict(os.environ)
    # If an API key is in the environment the CLI may bill it instead of the subscription; keep it out.
    env.pop("ANTHROPIC_API_KEY", None)
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, env=env, cwd=HERE, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p failed (exit {r.returncode}). If it says you're not logged in, "
                           f"run `claude` once interactively.\n{r.stderr or r.stdout}")
    out = json.loads(r.stdout)
    if out.get("is_error"):
        raise RuntimeError(f"claude -p returned an error: {out.get('result') or out}")
    return out.get("result", "")


def classify(items):
    """items: list of dicts with id/kind/... ; returns list of verdict dicts."""
    prompt = build_prompt(items)
    backend = os.environ.get("LLM_BACKEND", "claude-cli").strip().lower()
    text = ask_api(prompt) if backend == "api" else ask_claude_cli(prompt)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise RuntimeError(f"Model did not return a JSON array:\n{text[:500]}")
    return json.loads(text[start:end + 1])


# ---------- delivery ----------

EMOJI = {"outage": "⚠️", "maintenance": "🔧", "action": "✅", "money": "💶",
         "rules": "📜", "safety": "🚨", "meeting": "🗓️", "other": "📌"}


def render(important):
    lines = []
    for v in important:
        it = v["_item"]
        head = f"{EMOJI.get(v.get('category'), '📌')} {it.get('title') or '(untitled)'}"
        lines.append(head)
        lines.append(f"   {v.get('summary', '')}")
        if v.get("action"):
            lines.append(f"   → Action: {v['action']}")
        if v.get("when"):
            lines.append(f"   → When: {v['when']}")
        lines.append("")
    return "\n".join(lines).strip()


def send_email(subject, text):
    if not os.environ.get("SMTP_HOST"):
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ.get("NOTIFY_EMAIL_TO", os.environ["SMTP_USER"])
    msg.set_content(text + "\n\nOpen Startmotor: https://startmotor.chainels.com")
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", 587))) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        s.send_message(msg)
    return True


def send_telegram(text):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                  json={"chat_id": chat, "text": text[:4000]}, timeout=30).raise_for_status()
    return True


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--init", action="store_true", help="mark current feed as seen, send nothing")
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending; don't update state")
    ap.add_argument("--count", type=int, default=30, help="posts to fetch (default 30)")
    ap.add_argument("--days", type=int, default=30, help="days ahead for events (default 30)")
    args = ap.parse_args()

    load_env()
    data = fetch(args.count, args.days)
    seen = load_seen()
    seen_set = set(seen)

    new = []
    for kind, key in (("post", "posts"), ("event", "events")):
        for it in data.get(key, []):
            k = item_key(kind, it)
            if k not in seen_set:
                new.append((k, kind, it))

    if args.init:
        save_seen(seen + [k for k, _, _ in new])
        print(f"Initialised: {len(new)} items marked as seen.")
        return

    if not new:
        print("Nothing new.")
        return

    payload = [{"id": k, "kind": kind, **it} for k, kind, it in new]
    for p in payload:  # keep the prompt small
        p["body"] = (p.get("body") or "")[:3000]
    verdicts = classify(payload)
    by_id = {k: it for k, _, it in new}
    important = []
    for v in verdicts:
        if v.get("important") and v.get("id") in by_id:
            v["_item"] = by_id[v["id"]]
            important.append(v)

    print(f"{len(new)} new item(s), {len(important)} important.")
    if important:
        text = render(important)
        subject = f"Startmotor: {len(important)} important update{'s' if len(important) > 1 else ''}"
        if args.dry_run:
            print("\n" + subject + "\n\n" + text)
        else:
            sent = [name for name, ok in (("email", send_email(subject, text)),
                                          ("telegram", send_telegram(f"{subject}\n\n{text}"))) if ok]
            if not sent:
                print("No delivery channel configured (set SMTP_* or TELEGRAM_*). Output:\n\n" + text)
            else:
                print("Sent via " + ", ".join(sent))

    if not args.dry_run:
        save_seen(seen + [k for k, _, _ in new])


if __name__ == "__main__":
    main()
