#!/usr/bin/env python3
"""Generic JSON endpoint watcher.

Periodically checks a date-ranged JSON endpoint and sends a push
notification via ntfy.sh when its content changes.

All target-specific values are supplied via environment variables
(GitHub Secrets). Nothing about the monitored site appears in this
code, in the state file, or in execution logs.

Environment variables:
  TARGET_URL_TEMPLATE  URL with {from} / {to} placeholders (required)
  NTFY_TOPIC           ntfy.sh topic to publish to (required)
  CLICK_URL            URL opened when tapping the notification (optional)
  MONTHS_AHEAD         how many months ahead to check (default: 4)
  SEND_TEST            "true" to send a test notification (manual runs)
"""

import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Tokyo")
STATE_FILE = "state.json"
TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
FAILURE_ALERT_THRESHOLD = 6  # consecutive all-failed runs before warning


def month_ranges(start: date, months_ahead: int):
    """Yield (first_day, last_day) per month, from `start` through
    the end of the month `months_ahead` months later."""
    for i in range(months_ahead + 1):
        yy = start.year + (start.month - 1 + i) // 12
        mm = (start.month - 1 + i) % 12 + 1
        first = date(yy, mm, 1)
        nxt = date(yy + 1, 1, 1) if mm == 12 else date(yy, mm + 1, 1)
        last = nxt - timedelta(days=1)
        if i == 0:
            first = start
        yield first, last


def fetch(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8")


def notify(topic: str, title: str, message: str, click: str = "",
           priority: int = 3, tags=None) -> None:
    body = {
        "topic": topic,
        "title": title,
        "message": message,
        "priority": priority,
    }
    if click:
        body["click"] = click
    if tags:
        body["tags"] = tags
    req = urllib.request.Request(
        "https://ntfy.sh",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        resp.read()


def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def extract_signal(body: str):
    """Return (has_data, date_strings) for one JSON response.

    has_data is True when any top-level list in the JSON object is
    non-empty (e.g. an availability list that is normally empty).
    """
    try:
        data = json.loads(body)
    except ValueError:
        return False, set()
    has_data = False
    dates = set()
    if isinstance(data, dict):
        values = data.values()
    elif isinstance(data, list):
        values = [data]
    else:
        values = []
    for v in values:
        if isinstance(v, list) and len(v) > 0:
            has_data = True
            dates.update(re.findall(r"\d{4}-\d{2}-\d{2}", json.dumps(v)))
    return has_data, dates


def main() -> int:
    template = os.environ["TARGET_URL_TEMPLATE"]
    topic = os.environ["NTFY_TOPIC"]
    click = os.environ.get("CLICK_URL", "")
    months_ahead = int(os.environ.get("MONTHS_AHEAD", "4"))

    if os.environ.get("SEND_TEST", "").lower() == "true":
        notify(topic, "テスト通知",
               "監視システムは正常にセットアップされています。",
               click, priority=3, tags=["white_check_mark"])
        print("test notification sent")

    today = datetime.now(TZ).date()
    state = load_state()

    bodies = []
    has_data = False
    all_dates = set()
    failures = 0
    checked = 0

    for first, last in month_ranges(today, months_ahead):
        url = (template
               .replace("{from}", first.isoformat())
               .replace("{to}", last.isoformat()))
        checked += 1
        try:
            body = fetch(url)
        except Exception:
            failures += 1
            print(f"range {checked}: request failed")
            continue
        bodies.append(body)
        hd, dates = extract_signal(body)
        has_data = has_data or hd
        all_dates.update(dates)
        print(f"range {checked}: ok (data={hd})")

    if failures == checked:
        # every request failed -> count it, warn once after threshold
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        if state["consecutive_failures"] == FAILURE_ALERT_THRESHOLD:
            notify(topic, "監視システム警告",
                   "チェックが連続して失敗しています。サイト改修などの可能性が"
                   "あるため、GitHubのActionsログを確認してください。",
                   priority=4, tags=["warning"])
        state["last_checked"] = today.isoformat()
        save_state(state)
        print("all requests failed")
        return 0

    state["consecutive_failures"] = 0

    snapshot = "\n".join(bodies)
    import hashlib
    new_hash = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
    old_hash = state.get("hash")
    had_data = state.get("had_data", False)

    if old_hash is not None and new_hash != old_hash:
        if has_data:
            date_list = "\n".join(sorted(all_dates)[:20]) or "(日付の抽出不可)"
            notify(topic,
                   "予約枠が出た可能性があります!",
                   "監視中のカレンダーに変化がありました。\n"
                   f"検出された日付:\n{date_list}\n\n"
                   "今すぐ予約サイトを確認してください。",
                   click, priority=5, tags=["rotating_light", "tada"])
            print("CHANGE detected: data present -> notified (max priority)")
        elif had_data:
            notify(topic,
                   "枠が見えなくなりました",
                   "先ほどまで存在した枠が消えたか、内容が変化しました。",
                   click, priority=3)
            print("CHANGE detected: data disappeared -> notified")
        else:
            print("change in payload but still empty -> no notification")
    elif old_hash is None and has_data:
        # first ever run and data already present
        date_list = "\n".join(sorted(all_dates)[:20]) or "(日付の抽出不可)"
        notify(topic, "予約枠が出た可能性があります!",
               f"検出された日付:\n{date_list}", click,
               priority=5, tags=["rotating_light"])
        print("first run: data present -> notified")
    else:
        print("no change")

    state["hash"] = new_hash
    state["had_data"] = has_data
    state["last_checked"] = today.isoformat()
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
