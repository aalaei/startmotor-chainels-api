import os, re, time, json, requests, argparse
from datetime import datetime, timezone
from pathlib import Path

for line in Path(".env").read_text().splitlines() if Path(".env").exists() else []:
    k, _, v = line.partition("=")
    if k.strip() and not k.strip().startswith("#"):
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))

BASE = "https://startmotor.chainels.com"
HTML_ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&nbsp;": " "}

def strip_html(text):
    text = re.sub(r"<[^>]+>", "", text or "")
    for ent, char in HTML_ENTITIES.items():
        text = text.replace(ent, char)
    return re.sub(r"\n{3,}", "\n\n", text).strip()

def fmt_ts(ts):
    if not ts:
        return "N/A"
    try:
        return datetime.fromisoformat(str(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(ts)

def first_sentence(text, max=160):
    m = re.search(r'[.!?][\s]', text)
    snippet = text[:m.end()].strip() if m and m.end() <= max else text[:max]
    return snippet + ("..." if len(text) > len(snippet) else "")

# Auth
session = requests.Session()
session.headers.update({"Accept": "application/json", "x-chainels-client": "true"})
auth = os.environ.get("CHAINELS_COOKIE_CHAINELS_PROD_AUTH")
ssid = os.environ.get("CHAINELS_COOKIE_CHAINELS_PROD_SSID")
COMPANY = os.environ.get("CHAINELS_COMPANY")
if not auth or not ssid:
    raise SystemExit("No auth. Run: python refresh_token.py")
session.cookies.set("chainels_prod_auth", auth, domain=".chainels.com")
session.cookies.set("chainels_prod_ssid", ssid, domain=".chainels.com")

# Args
parser = argparse.ArgumentParser(
    description=(
        "Fetch and display a digest from the Startmotor Chainels community platform.\n\n"
        "MODES:\n"
        "  digest.py                  Summary: numbered list of recent posts and upcoming events.\n"
        "                             Each item shows date, channel, title, and a one-line snippet.\n"
        "  digest.py post N           Full detail for post #N (use the number from the summary).\n"
        "  digest.py event N          Full detail for event #N (use the number from the summary).\n"
        "  digest.py --json           All data as structured JSON (posts + events).\n"
        "  digest.py post N --json    Single post as JSON.\n"
        "  digest.py event N --json   Single event as JSON.\n\n"
        "TYPICAL AI WORKFLOW:\n"
        "  1. Run with no args to get the numbered summary.\n"
        "  2. Run `digest.py post N` or `digest.py event N` to read a specific item in full.\n"
        "  3. Use --json if you need structured data to process programmatically.\n\n"
        "AUTH:\n"
        "  Requires cookies in .env (CHAINELS_COOKIE_* vars).\n"
        "  If you get a 401, run: python refresh_token.py"
    ),
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
parser.add_argument("type",  nargs="?", choices=["post", "event"],
                    help="Item type to open in full (post or event)")
parser.add_argument("index", nargs="?", type=int,
                    help="1-based index of the item from the summary list")
parser.add_argument("--count", type=int, default=10, metavar="N",
                    help="Number of timeline posts to fetch (default: 10)")
parser.add_argument("--days",  type=int, default=14, metavar="N",
                    help="How many days ahead to look for events (default: 14)")
parser.add_argument("--json",  action="store_true",
                    help="Output as JSON instead of plain text")
args = parser.parse_args()

# Fetch
now = int(time.time())
tl = session.get(f"{BASE}/api/v2/companies/{COMPANY}/timeline?channel=mytimeline&count={args.count}&include=permissions,latest_replies,my_registration,account.permissions,survey.questions")
tl.raise_for_status()
ev = session.get(f"{BASE}/api/v2/companies/{COMPANY}/events?from={now}&to={now + args.days * 86400}")
ev.raise_for_status()

raw_tl, raw_ev = tl.json(), ev.json()
posts = raw_tl if isinstance(raw_tl, list) else raw_tl.get("results", raw_tl.get("items", []))
evts  = raw_ev  if isinstance(raw_ev,  list) else raw_ev.get("results",  raw_ev.get("items",  []))

# Normalise
def norm_post(p):
    return {
        "title":   strip_html(p.get("title") or p.get("subject") or ""),
        "channel": (p.get("channel") or {}).get("name") or p.get("channel_name") or "",
        "author":  (p.get("author") or {}).get("name") or p.get("author_name") or "",
        "date":    fmt_ts(p.get("created_at") or p.get("timestamp") or p.get("date")),
        "body":    strip_html(p.get("content") or p.get("body") or p.get("text") or ""),
    }

def norm_event(e):
    return {
        "title":    strip_html(e.get("title") or e.get("name") or ""),
        "start":    fmt_ts(e.get("start_date") or e.get("start") or e.get("date")),
        "end":      fmt_ts(e.get("end_date") or e.get("end")),
        "location": e.get("place") or e.get("location") or "",
        "body":     strip_html(e.get("description") or e.get("body") or ""),
    }

posts = [norm_post(p) for p in posts]
evts  = [norm_event(e) for e in evts]

# JSON mode
if args.json:
    if args.type == "post" and args.index:
        print(json.dumps(posts[args.index - 1], indent=2))
    elif args.type == "event" and args.index:
        print(json.dumps(evts[args.index - 1], indent=2))
    else:
        print(json.dumps({"posts": posts, "events": evts}, indent=2))
    raise SystemExit(0)

# Detail mode
if args.type and args.index is not None:
    items = posts if args.type == "post" else evts
    if args.index < 1 or args.index > len(items):
        raise SystemExit(f"No {args.type} #{args.index}. Range: 1–{len(items)}")
    it = items[args.index - 1]
    if args.type == "post":
        print(f"# {it['title']}")
        print(f"{it['date']} | {it['channel']}" + (f" | {it['author']}" if it['author'] else ""))
        print()
        print(it['body'] or "(no content)")
    else:
        end = f" → {it['end']}" if it['end'] != "N/A" else ""
        print(f"# {it['title']}")
        print(f"{it['start']}{end}" + (f" | {it['location']}" if it['location'] else ""))
        print()
        print(it['body'] or "(no description)")
    raise SystemExit(0)

# Summary mode
print(f"## Posts ({len(posts)})  — `digest.py post N` for full view")
for i, p in enumerate(posts, 1):
    author = f" · {p['author']}" if p['author'] else ""
    snippet = first_sentence(p['body']) if p['body'] else ""
    print(f"{i}. [{p['date']}] {p['channel']}{author} — {p['title']}")
    if snippet:
        print(f"   {snippet}")

print(f"\n## Events ({len(evts)})  — `digest.py event N` for full view")
for i, e in enumerate(evts, 1):
    loc = f" · {e['location']}" if e['location'] else ""
    print(f"{i}. [{e['start']}]{loc} — {e['title']}")
    if e['body']:
        print(f"   {first_sentence(e['body'])}")
