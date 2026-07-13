import re, json, sys, os
from datetime import datetime, timezone

def redact(s: str) -> str:
    s = re.sub(r"<?https?://\S+>?", "[link redacted]", s)
    s = re.sub(r"(ends in\s+)`?\d{3,8}`?", r"\1[redacted]", s, flags=re.IGNORECASE)
    s = re.sub(r"`\d{3,8}`", "[redacted]", s)
    return s

with open("root_messages.json") as f:
    roots = json.load(f)
by_ts = {m["ts"]: m for m in roots}

with open("threads_raw.txt", encoding="utf-8") as f:
    text = f.read()

thread_blocks = re.split(r"(?:\A|\n)ROOT_TS:\s*", text.strip())
total_replies = 0
missing = []
for block in thread_blocks:
    block = block.strip()
    if not block:
        continue
    lines = block.split("\n")
    root_ts = lines[0].strip()
    body = "\n".join(lines[1:])
    if root_ts not in by_ts:
        missing.append(root_ts)
        continue

    reply_blocks = re.split(r"\n(?=--- Reply \d+ of \d+ ---)", body.strip())
    replies = []
    for rb in reply_blocks:
        rb = rb.strip()
        if not rb or not rb.startswith("--- Reply"):
            continue
        header_m = re.search(r"\((U[A-Z0-9]+)\)", rb)
        ts_m = re.search(r"Message TS:\s*([\d.]+)", rb)
        reactions_m = re.search(r"Reactions:\s*(.+)", rb)
        if not header_m or not ts_m:
            continue
        user = header_m.group(1)
        ts = ts_m.group(1)
        reactions_total = 0
        if reactions_m:
            for count in re.findall(r"\((\d+)\)", reactions_m.group(1)):
                reactions_total += int(count)

        rlines = rb.split("\n")
        text_lines = []
        started = False
        for line in rlines:
            if line.startswith("Message TS:"):
                started = True
                continue
            if not started:
                continue
            if line.startswith("Reactions:") or line.startswith("Files:"):
                break
            text_lines.append(line)
        rtext = redact("\n".join(text_lines).strip())

        replies.append({
            "ts": ts,
            "user": user,
            "text": rtext,
            "thread_ts": root_ts,
            "reply_count": 0,
            "reply_users_count": 0,
            "reactions": reactions_total,
            "replies": [],
            "permalink": None,
        })
    by_ts[root_ts]["replies"] = replies
    total_replies += len(replies)

print(f"Merged replies for {len(thread_blocks)} threads; total replies = {total_replies}")
if missing:
    print("MISSING root ts (no match):", missing)

mismatches = [m["ts"] for m in roots if m["reply_count"] > 0 and len(m["replies"]) == 0]
print(f"Roots with reply_count>0 but 0 replies attached: {len(mismatches)}")
if mismatches:
    print(mismatches)

with open("merged_messages.json", "w") as f:
    json.dump(roots, f, indent=2)
print(f"Total root messages: {len(roots)}")

# --- Seed into the repo's data/raw/ cache, bucketed by UTC calendar date ---
if len(sys.argv) < 2:
    print("\nSkipping repo seed step: pass the repo root as an argument, e.g.")
    print("  python3 merge_replies.py /path/to/Merge-Partnerships-Slack-Data-Analysis")
    sys.exit(0)

repo_root = os.path.abspath(sys.argv[1])
sys.path.insert(0, repo_root)
from src import store  # noqa: E402

grouped = {}
for msg in roots:
    date_str = datetime.fromtimestamp(float(msg["ts"]), tz=timezone.utc).strftime("%Y-%m-%d")
    grouped.setdefault(date_str, []).append(msg)

for date_str, day_messages in sorted(grouped.items()):
    path = store.write_raw(day_messages, date_str)
    print(f"Wrote {len(day_messages)} messages -> {path}")

print(f"\nSeeded {len(grouped)} daily raw files under {repo_root}/data/raw/")
