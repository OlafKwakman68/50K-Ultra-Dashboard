#!/usr/bin/env python3
"""
Regenerates "Ultra Dashboard App.html" from a fresh Strava activities export.

Usage:
    python3 generate_dashboard.py activities.json

`activities.json` must be the raw JSON returned by the Strava MCP tool
`list_activities` (i.e. the object with an "activities" array), fetched with:
    range_start = "2026-07-06T00:00:00"
    range_end   = "2026-10-26T23:59:59"
    first = 100
    ordering = "StartDateLocalAsc"
(paginate with `after`/end_cursor if has_next_page is true; concatenate all
pages' activities into one list before writing activities.json)

This script encodes all the manual decisions made while building the
dashboard so a scheduled run doesn't have to re-derive them:
  - sport_type "Run" or "Workout"  -> counts as training km
  - sport_type "Walk"              -> shown, but never counts as training km
  - sport_type "Hike" (or anything else) -> excluded entirely
  - TYPE_OVERRIDES / SPLIT_OVERRIDES -> manual terrain-type corrections for
    specific historical activities the auto-classifier got wrong
  - MANUAL_ENTRIES -> activities not present on Strava at all (logged by hand)

If a new run needs a manual correction (wrong terrain type, mixed-terrain
split, or a walk Strava never recorded), add it to TYPE_OVERRIDES /
SPLIT_OVERRIDES / MANUAL_ENTRIES below, the same way earlier ones were added.
"""
import json
import sys
import datetime
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(SCRIPT_DIR, "dashboard_template.html")
OUTPUT_PATH = os.path.join(SCRIPT_DIR, "index.html")

RACE_DATE = datetime.date(2026, 10, 23)

# ---- 15-week plan (Base/Build/Peak/Taper), used for week bucketing + chart plan line ----
PLAN_WEEKS = [
    {"week": 0, "start": "2026-07-06", "end": "2026-07-12", "planTotal": None},
    {"week": 1, "start": "2026-07-13", "end": "2026-07-19", "planTotal": 22},
    {"week": 2, "start": "2026-07-20", "end": "2026-07-26", "planTotal": 27},
    {"week": 3, "start": "2026-07-27", "end": "2026-08-02", "planTotal": 30},
    {"week": 4, "start": "2026-08-03", "end": "2026-08-09", "planTotal": 22},
    {"week": 5, "start": "2026-08-10", "end": "2026-08-16", "planTotal": 34},
    {"week": 6, "start": "2026-08-17", "end": "2026-08-23", "planTotal": 39},
    {"week": 7, "start": "2026-08-24", "end": "2026-08-30", "planTotal": 43},
    {"week": 8, "start": "2026-08-31", "end": "2026-09-06", "planTotal": 32},
    {"week": 9, "start": "2026-09-07", "end": "2026-09-13", "planTotal": 49},
    {"week": 10, "start": "2026-09-14", "end": "2026-09-20", "planTotal": 55},
    {"week": 11, "start": "2026-09-21", "end": "2026-09-27", "planTotal": 58},
    {"week": 12, "start": "2026-09-28", "end": "2026-10-04", "planTotal": 41},
    {"week": 13, "start": "2026-10-05", "end": "2026-10-11", "planTotal": 34},
    {"week": 14, "start": "2026-10-12", "end": "2026-10-18", "planTotal": 25},
    {"week": 15, "start": "2026-10-19", "end": "2026-10-25", "planTotal": None},
]

TYPE_COLORS = {"trail": "#4fd1c5", "hills": "#ff8c42", "road": "#7fc4ff",
                "beach": "#ffe066", "treadmill": "#c792ea", "walk": "#4a4e58"}
TYPE_ORDER = ["trail", "hills", "road", "beach", "treadmill"]

# ---- Manual corrections (carried forward from the dashboard's build history) ----
TYPE_OVERRIDES = {
    "2026-07-14": "road",
    # 16 Jul: confirmed a genuine road run (also labeled that way on Strava) —
    # do NOT reclassify this one; leaving no override so it falls through to "road".
}
SPLIT_OVERRIDES = {
    # 12 Jul: single 8.75km Strava activity was 4.5km trail + ~4.25km walking.
    # Only the trail portion counts; the walking remainder is intentionally dropped.
    "2026-07-12": [{"type": "trail", "km": 4.5}],
}
MANUAL_ENTRIES = [
    {"date": "2026-07-11", "km": 4.2, "type": "walk", "isTraining": False, "source": "manual"},
]


def week_for_date(date_str):
    d = datetime.date.fromisoformat(date_str[:10])
    for w in PLAN_WEEKS:
        ws = datetime.date.fromisoformat(w["start"])
        we = datetime.date.fromisoformat(w["end"])
        if ws <= d <= we:
            return w
    return None


def classify_type(activity):
    name = (activity.get("name") or "").lower()
    desc = (activity.get("description") or "").lower()
    text = name + " " + desc
    if activity.get("sport_type") == "TrailRun" or "trail" in text:
        return "trail"
    if "beach" in text:
        return "beach"
    if "hill" in text:
        return "hills"
    # Treadmill: explicit name match or Strava's own trainer flag only.
    if "treadmill" in text or "tread mill" in text:
        return "treadmill"
    if activity.get("is_trainer"):
        return "treadmill"
    # NOTE: classification relies only on Strava's own labels (name, description,
    # sport_type, trainer flag) plus manual TYPE_OVERRIDES/SPLIT_OVERRIDES for dates
    # Olaf has corrected by hand. We deliberately do NOT infer terrain from recorded
    # elevation gain, max speed, or GPS signal — Olaf's watch doesn't always record
    # elevation reliably, and that heuristic previously produced false positives
    # (guessed "hills" for several genuine road runs on 21/23/24 Jul, and would do
    # the same for "treadmill" on any outdoor run with a flaky GPS/elevation fix).
    # "hills" only comes from an explicit name match above, or a TYPE_OVERRIDES entry.
    return "road"


def estimate_steps(summary):
    """
    Strava doesn't report step counts. This is a rough estimate from cadence:
    Strava's avg_cadence is steps-per-minute for ONE foot, so total steps/min
    is roughly double that. Returns None when cadence wasn't recorded (not
    all devices report it) rather than silently showing 0.
    TODO: replace with real step counts if/when a Garmin (or similar) data
    source with actual pedometer totals gets connected.
    """
    cadence = summary.get("avg_cadence")
    moving_min = (summary.get("moving_time") or 0) / 60
    if cadence is None or moving_min <= 0:
        return None
    return round(cadence * 2 * moving_min)


def build_entries(activities):
    entries = []
    for a in activities:
        date_str = a["start_local"][:10]
        summary = a.get("summary") or {}
        km = round((summary.get("distance") or 0) / 1000, 2)
        moving_min = (summary.get("moving_time") or 0) / 60
        pace = round(moving_min / km, 2) if km > 0 else None
        steps = estimate_steps(summary)
        sport = a.get("sport_type")

        if sport == "Walk":
            entries.append({"date": date_str, "km": km, "type": "walk", "steps": steps,
                             "pace": None, "isTraining": False, "source": "strava"})
            continue

        if sport not in ("Run", "Workout"):
            continue  # Hike and anything else: excluded entirely

        split = SPLIT_OVERRIDES.get(date_str)
        if split:
            # Steps aren't split proportionally (we don't have segment-level cadence);
            # attribute the full activity's estimated steps to the first segment only
            # so it isn't double-counted across segments.
            for i, seg in enumerate(split):
                entries.append({"date": date_str, "km": seg["km"], "type": seg["type"],
                                 "steps": steps if i == 0 else None,
                                 "pace": pace, "isTraining": True, "source": "strava"})
            continue

        entries.append({"date": date_str, "km": km,
                         "type": TYPE_OVERRIDES.get(date_str) or classify_type(a),
                         "steps": steps, "pace": pace, "isTraining": True, "source": "strava"})

    for m in MANUAL_ENTRIES:
        entries.append({"date": m["date"], "km": m["km"], "type": m["type"], "steps": m.get("steps"),
                         "pace": None, "isTraining": m.get("isTraining", True),
                         "source": m.get("source", "manual")})

    entries.sort(key=lambda e: e["date"])
    return entries


def fmt_steps(n):
    if n >= 1000:
        return f"{n/1000:.1f}k".replace(".0k", "k")
    return str(n)


def build_chart_svg(week_type_totals, week_steps_totals, cur_week_num):
    W_CAT, BAR_W, STEPS_BAR_W, GAP = 52, 16, 9, 4
    MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 40, 16, 16, 34
    CHART_H = 300

    max_actual = max([sum(v.values()) for v in week_type_totals.values()] + [0])
    max_plan = max([w["planTotal"] or 0 for w in PLAN_WEEKS])
    max_y = max(max_actual, max_plan, 10)
    max_y = ((int(max_y) // 10) + 2) * 10  # round up to next 10 with headroom

    # Steps use their own independent scale (different unit/magnitude than km) —
    # same pixel height range as the km bars, but not numerically comparable to
    # the km gridlines. Floor of 5000 avoids a huge bar on a single low-step week.
    max_steps = max([v for v in week_steps_totals.values()] + [5000])
    max_steps = int(max_steps * 1.15)

    n = len(PLAN_WEEKS)
    chart_w = MARGIN_L + MARGIN_R + n * W_CAT
    total_h = MARGIN_T + CHART_H + MARGIN_B

    def y_of(v):
        return MARGIN_T + CHART_H - (v / max_y) * CHART_H

    parts = [f'<svg viewBox="0 0 {chart_w} {total_h}" xmlns="http://www.w3.org/2000/svg" style="min-width:{chart_w}px">']

    for gv in range(0, max_y + 1, 10):
        y = y_of(gv)
        parts.append(f'<line x1="{MARGIN_L}" y1="{y:.1f}" x2="{chart_w-MARGIN_R}" y2="{y:.1f}" stroke="#1f222b" stroke-width="1"/>')
        parts.append(f'<text x="{MARGIN_L-8}" y="{y+3:.1f}" fill="#8a8f9c" font-size="9" text-anchor="end" font-family="-apple-system,sans-serif">{gv}</text>')

    line_points = []
    for i, w in enumerate(PLAN_WEEKS):
        cx = MARGIN_L + i * W_CAT + W_CAT / 2
        bar_x = cx - GAP / 2 - BAR_W
        y_cursor = MARGIN_T + CHART_H
        for t in TYPE_ORDER:
            v = week_type_totals.get(w["week"], {}).get(t, 0)
            if v <= 0:
                continue
            h = (v / max_y) * CHART_H
            y_top = y_cursor - h
            parts.append(f'<rect x="{bar_x:.1f}" y="{y_top:.1f}" width="{BAR_W}" height="{h:.1f}" rx="2" fill="{TYPE_COLORS[t]}"/>')
            y_cursor = y_top
        sv = week_steps_totals.get(w["week"], 0)
        if sv > 0:
            steps_x = cx + GAP / 2
            sh = (sv / max_steps) * CHART_H
            sy = MARGIN_T + CHART_H - sh
            parts.append(f'<rect x="{steps_x:.1f}" y="{sy:.1f}" width="{STEPS_BAR_W}" height="{sh:.1f}" rx="2" fill="{TYPE_COLORS["walk"]}"/>')
            parts.append(f'<text x="{steps_x+STEPS_BAR_W/2:.1f}" y="{sy-4:.1f}" fill="#6d7280" font-size="7.5" text-anchor="middle" font-family="-apple-system,sans-serif">{fmt_steps(sv)}</text>')
        if w["planTotal"] is not None:
            line_points.append((cx, y_of(w["planTotal"])))
        label = "Restart" if w["week"] == 0 else f'Wk {w["week"]}'
        parts.append(f'<text x="{cx:.1f}" y="{MARGIN_T+CHART_H+16}" fill="#8a8f9c" font-size="9" text-anchor="middle" font-family="-apple-system,sans-serif">{label}</text>')
        if w["week"] == cur_week_num:
            parts.append(f'<rect x="{MARGIN_L + i*W_CAT + 2}" y="{MARGIN_T}" width="{W_CAT-4}" height="{CHART_H}" fill="#4fd1c5" opacity="0.06"/>')

    if line_points:
        path_d = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in line_points)
        parts.append(f'<path d="{path_d}" fill="none" stroke="#ffd166" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>')
        for x, y in line_points:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#ffd166"/>')

    parts.append('</svg>')
    return "\n".join(parts)


def entries_to_js(entries):
    def js_val(v):
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, str):
            return json.dumps(v)
        return json.dumps(v)
    lines = []
    for e in entries:
        fields = ", ".join(f"{k}: {js_val(v)}" for k, v in
                            [("date", e["date"]), ("km", e["km"]), ("type", e["type"]),
                             ("pace", e["pace"]), ("steps", e.get("steps")),
                             ("isTraining", e["isTraining"]), ("source", e["source"])])
        lines.append("  { " + fields + " }")
    return "[\n" + ",\n".join(lines) + "\n]"


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 generate_dashboard.py activities.json", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        data = json.load(f)
    activities = data.get("activities", data if isinstance(data, list) else [])

    entries = build_entries(activities)

    week_type_totals = {w["week"]: {} for w in PLAN_WEEKS}
    week_steps_totals = {w["week"]: 0 for w in PLAN_WEEKS}
    for e in entries:
        w = week_for_date(e["date"])
        if not w:
            continue
        if e["isTraining"] is False:
            pass  # walks: excluded from training km, but still counted in steps below
        else:
            week_type_totals[w["week"]][e["type"]] = week_type_totals[w["week"]].get(e["type"], 0) + e["km"]
        if e.get("steps"):
            week_steps_totals[w["week"]] += e["steps"]

    today = datetime.date.today()
    cur_week = week_for_date(today.isoformat())
    cur_week_num = cur_week["week"] if cur_week else None

    chart_svg = build_chart_svg(week_type_totals, week_steps_totals, cur_week_num)
    entries_js = entries_to_js(entries)

    with open(TEMPLATE_PATH) as f:
        html = f.read()

    html = html.replace("__ENTRIES_JSON__", entries_js)
    html = html.replace("__TODAY_ISO__", f"{today.isoformat()}T00:00:00")
    html = html.replace("__CHART_SVG__", chart_svg.replace("`", "'"))
    html = html.replace("__LAST_UPDATED__", today.strftime("%-d %b %Y"))

    with open(OUTPUT_PATH, "w") as f:
        f.write(html)

    print(f"Wrote {OUTPUT_PATH} ({len(html)} chars), {len(entries)} entries, current week {cur_week_num}")


if __name__ == "__main__":
    main()
