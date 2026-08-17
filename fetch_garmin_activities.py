#!/usr/bin/env python3
"""
Fetches Garmin Connect activities for the training window and reshapes them
into the JSON structure generate_dashboard.py expects (the same structure it
used to get from the Strava MCP tool).

Auth: uses a saved garth token directory (see garmin_login_export.py), NOT a
fresh username/password login -- Garmin blocks password logins from cloud/
datacenter IPs, but API calls with a pre-saved token work fine and the
library proactively refreshes the token without hitting that blocked path.

Usage:
    python3 fetch_garmin_activities.py --token-dir /path/to/garmin_tokens \
        --start 2026-07-06 --end 2026-10-26 --out activities.json

Sport-type mapping decisions (confirmed with Olaf on 2026-08-16):
  - Garmin's averageRunningCadenceInStepsPerMinute is already a TOTAL
    (both-feet) steps/min figure -- verified against real step counts
    (ratio ~1.00-1.01). generate_dashboard.py's estimate_steps() assumes
    Strava's per-foot convention and doubles whatever it's given, so we
    halve Garmin's value here before writing it out.
  - typeKey "other" is what Olaf's watch logs when he uses route
    navigation, which he says is "almost only for runs" -> mapped to "Run"
    by default (include when in doubt, per Olaf).
  - typeKey "hiking" -> "Hike", which generate_dashboard.py drops entirely
    (matches how Strava hikes were already excluded).
  - typeKey "walking"/"casual_walking"/"speed_walking" -> "Walk", shown but
    not counted as training km (matches prior Strava behavior).
  - typeKey "treadmill_running"/"indoor_running" -> "Run" with is_trainer
    True.
  - typeKey "trail_running" -> "TrailRun".
  - Anything else (golf, cycling, road_biking, swimming, etc.) is passed
    through unchanged and gets dropped by generate_dashboard.py's own
    filter, same as "Hike" would be.
  - Garmin does populate a description/notes field, but only on activities
    Olaf has manually annotated (most are blank). E.g. the 2026-07-12 entry
    carries the description "4.5km trail run plus 4.35km walk", which
    corroborates the existing SPLIT_OVERRIDES entry for that date almost
    word for word.
"""
import argparse
import json
import sys
import time

from garminconnect import Garmin

LOGIN_RETRY_DELAYS_S = (30, 90, 240)  # backoff for transient 429s/5xxs on Garmin's side


def login_with_retry(client: Garmin, token_dir: str):
    last_err = None
    for attempt, delay in enumerate((0,) + LOGIN_RETRY_DELAYS_S):
        if delay:
            print(f"Retrying Garmin login in {delay}s (attempt {attempt + 1})...", file=sys.stderr)
            time.sleep(delay)
        try:
            client.login(token_dir)
            return
        except Exception as e:
            last_err = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            transient = status in (429, 500, 502, 503, 504) or status is None and "429" in str(e)
            print(f"Garmin login attempt failed: {type(e).__name__}: {e}", file=sys.stderr)
            if not transient:
                break
    raise last_err

SPORT_MAP = {
    "running": "Run",
    "track_running": "Run",
    "street_running": "Run",
    "ultra_run": "Run",
    "obstacle_run": "Run",
    "virtual_run": "Run",
    "trail_running": "TrailRun",
    "treadmill_running": "Run",
    "indoor_running": "Run",
    "walking": "Walk",
    "casual_walking": "Walk",
    "speed_walking": "Walk",
    "hiking": "Hike",
    "other": "Run",  # route-navigation runs, per Olaf
}
TRAINER_TYPEKEYS = {"treadmill_running", "indoor_running"}


def map_activity(a: dict) -> dict:
    activity_type = a.get("activityType") or {}
    type_key = activity_type.get("typeKey")
    sport_type = SPORT_MAP.get(type_key, type_key or "Unknown")

    distance_m = a.get("distance") or 0

    moving_s = a.get("movingDuration")
    if moving_s is None:
        moving_s = a.get("duration") or 0

    cadence_total = a.get("averageRunningCadenceInStepsPerMinute")
    avg_cadence = (cadence_total / 2) if cadence_total else None

    start_local = a.get("startTimeLocal") or ""
    if " " in start_local and "T" not in start_local:
        start_local = start_local.replace(" ", "T", 1)

    return {
        "start_local": start_local,
        "sport_type": sport_type,
        "name": a.get("activityName") or "",
        "description": a.get("description") or "",
        "is_trainer": type_key in TRAINER_TYPEKEYS,
        "summary": {
            "distance": distance_m,
            "moving_time": moving_s,
            "avg_cadence": avg_cadence,
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token-dir", required=True, help="Directory with saved garth tokens")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--out", required=True, help="Output activities.json path")
    args = ap.parse_args()

    client = Garmin()
    try:
        login_with_retry(client, args.token_dir)
    except Exception as e:
        print(f"Garmin login (token load) failed after retries: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    raw_activities = client.get_activities_by_date(args.start, args.end)
    print(f"Fetched {len(raw_activities)} raw activities from Garmin ({args.start} to {args.end})", file=sys.stderr)

    mapped = [map_activity(a) for a in raw_activities]
    mapped.sort(key=lambda e: e["start_local"])

    with open(args.out, "w") as f:
        json.dump({"activities": mapped}, f, indent=2)

    sport_counts = {}
    for e in mapped:
        sport_counts[e["sport_type"]] = sport_counts.get(e["sport_type"], 0) + 1
    print(f"Wrote {args.out}: {len(mapped)} activities. Sport type breakdown: {sport_counts}", file=sys.stderr)


if __name__ == "__main__":
    main()
