"""
Health Hub sync: pulls WHOOP + Garmin into Supabase.

Runs daily on GitHub Actions (see .github/workflows/sync.yml), or locally:
    python sync/sync.py --days 90      # first-time backfill
    python sync/sync.py                # last 3 days (default)

Env vars (GitHub → Settings → Secrets → Actions):
    SUPABASE_URL, SUPABASE_SERVICE_KEY
    WHOOP_CLIENT_ID, WHOOP_CLIENT_SECRET     (optional — skips WHOOP if missing)
    GARMIN_TOKENS                            (optional — skips Garmin if missing)
    TZ_NAME                                  (default America/New_York)
"""
import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
TZ = ZoneInfo(os.environ.get("TZ_NAME", "America/New_York"))

WHOOP_API = "https://api.prod.whoop.com/developer/v2"
WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"


# ---------------------------------------------------------------- Supabase
def sb_headers(extra=None):
    h = {"apikey": SUPABASE_KEY, "Content-Type": "application/json"}
    if SUPABASE_KEY.startswith("eyJ"):  # legacy JWT keys also go in Authorization
        h["Authorization"] = f"Bearer {SUPABASE_KEY}"
    h.update(extra or {})
    return h


def sb_upsert(table, rows, conflict):
    if not rows:
        return
    # PostgREST needs every row in one request to have the same keys
    keys = sorted({k for r in rows for k in r})
    rows = [{k: r.get(k) for k in keys} for r in rows]
    r = requests.post(f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={conflict}",
                      headers=sb_headers({"Prefer": "resolution=merge-duplicates"}),
                      json=rows, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"Supabase upsert {table} failed: {r.status_code} {r.text}")
    print(f"  upserted {len(rows)} rows → {table}")


def sb_get_secret(key):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/app_secrets?key=eq.{key}&select=value",
                     headers=sb_headers(), timeout=30)
    r.raise_for_status()
    data = r.json()
    return data[0]["value"] if data else None


def sb_set_secret(key, value):
    sb_upsert("app_secrets", [{"key": key, "value": value,
                               "updated_at": datetime.now(timezone.utc).isoformat()}], "key")


# ---------------------------------------------------------------- helpers
def ms_to_min(v):
    return round(v / 60000, 1) if v is not None else None


def s_to_min(v):
    return round(v / 60, 1) if v is not None else None


def local_date(iso):
    """ISO timestamp → local calendar date string."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ).date().isoformat()


def merge(rows_by_date, d, source, **vals):
    row = rows_by_date.setdefault(d, {"date": d, "source": source})
    for k, v in vals.items():
        if v is not None:
            row[k] = v


# ---------------------------------------------------------------- WHOOP
def whoop_access_token():
    cid, csec = os.environ.get("WHOOP_CLIENT_ID"), os.environ.get("WHOOP_CLIENT_SECRET")
    refresh = sb_get_secret("whoop_refresh_token")
    if not (cid and csec and refresh):
        return None
    r = requests.post(WHOOP_TOKEN_URL, data={
        "grant_type": "refresh_token", "refresh_token": refresh,
        "client_id": cid, "client_secret": csec, "scope": "offline",
    }, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"WHOOP token refresh failed ({r.status_code}): {r.text}\n"
                           "Re-run sync/whoop_auth.py to sign in again.")
    tok = r.json()
    # WHOOP rotates refresh tokens — save the new one immediately
    if tok.get("refresh_token"):
        sb_set_secret("whoop_refresh_token", tok["refresh_token"])
    return tok["access_token"]


def whoop_get_all(token, path, start, end):
    out, next_token = [], None
    while True:
        params = {"start": start, "end": end, "limit": 25}
        if next_token:
            params["nextToken"] = next_token
        r = requests.get(f"{WHOOP_API}{path}", headers={"Authorization": f"Bearer {token}"},
                         params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        out += body.get("records", [])
        next_token = body.get("next_token")
        if not next_token:
            return out


def sync_whoop(days):
    print("WHOOP…")
    token = whoop_access_token()
    if not token:
        print("  skipped (WHOOP not set up)")
        return
    start = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    end = datetime.now(timezone.utc).isoformat()
    rows = {}

    # Sleep → filed under the day you woke up
    sleep_by_id = {}
    for s in whoop_get_all(token, "/activity/sleep", start, end):
        if s.get("nap") or s.get("score_state") != "SCORED":
            continue
        sc = s.get("score") or {}
        st = sc.get("stage_summary") or {}
        d = local_date(s["end"])
        sleep_by_id[s["id"]] = d
        in_bed = st.get("total_in_bed_time_milli") or 0
        awake = st.get("total_awake_time_milli") or 0
        merge(rows, d, "whoop",
              sleep_min=ms_to_min(in_bed - awake) if in_bed else None,
              deep_min=ms_to_min(st.get("total_slow_wave_sleep_time_milli")),
              rem_min=ms_to_min(st.get("total_rem_sleep_time_milli")),
              light_min=ms_to_min(st.get("total_light_sleep_time_milli")),
              awake_min=ms_to_min(awake),
              sleep_score=sc.get("sleep_performance_percentage"),
              resp_rate=sc.get("respiratory_rate"))

    # Recovery → same day as the sleep it's based on
    for rc in whoop_get_all(token, "/recovery", start, end):
        if rc.get("score_state") != "SCORED":
            continue
        sc = rc.get("score") or {}
        d = sleep_by_id.get(rc.get("sleep_id")) or local_date(rc["created_at"])
        merge(rows, d, "whoop",
              recovery=sc.get("recovery_score"),
              hrv=round(sc["hrv_rmssd_milli"], 1) if sc.get("hrv_rmssd_milli") else None,
              rhr=sc.get("resting_heart_rate"),
              spo2=sc.get("spo2_percentage"),
              skin_temp_c=sc.get("skin_temp_celsius"))

    # Cycles → day strain
    for c in whoop_get_all(token, "/cycle", start, end):
        sc = c.get("score") or {}
        if sc.get("strain") is not None:
            merge(rows, local_date(c["start"]), "whoop", strain=round(sc["strain"], 1))

    sb_upsert("daily_metrics", list(rows.values()), "date,source")

    wo = []
    for w in whoop_get_all(token, "/activity/workout", start, end):
        sc = w.get("score") or {}
        dur = (datetime.fromisoformat(w["end"].replace("Z", "+00:00")) -
               datetime.fromisoformat(w["start"].replace("Z", "+00:00"))).total_seconds()
        wo.append({"source": "whoop", "external_id": str(w["id"]), "start_time": w["start"],
                   "date": local_date(w["start"]), "sport": w.get("sport_name"),
                   "duration_min": s_to_min(dur), "distance_m": sc.get("distance_meter"),
                   "avg_hr": sc.get("average_heart_rate"), "max_hr": sc.get("max_heart_rate"),
                   "kcal": round(sc["kilojoule"] / 4.184) if sc.get("kilojoule") else None,
                   "strain": round(sc["strain"], 1) if sc.get("strain") else None})
    sb_upsert("workouts", wo, "source,external_id")


# ---------------------------------------------------------------- Garmin
def sync_garmin(days):
    print("Garmin…")
    # Prefer the latest tokens saved in Supabase (they can refresh), fall back to the GitHub secret
    tokens = sb_get_secret("garmin_tokens") or os.environ.get("GARMIN_TOKENS")
    if not tokens:
        print("  skipped (GARMIN_TOKENS not set)")
        return
    from garminconnect import Garmin
    g = Garmin()
    g.login(tokens.strip())  # a JSON token string is loaded directly
    try:
        sb_set_secret("garmin_tokens", g.client.dumps())
    except Exception as e:
        print(f"  couldn't save refreshed Garmin tokens: {e}")

    rows = {}
    today = datetime.now(TZ).date()
    for i in range(days):
        d = today - timedelta(days=i)
        ds = d.isoformat()
        try:
            st = g.get_stats(ds) or {}
            merge(rows, ds, "garmin",
                  steps=st.get("totalSteps"),
                  active_kcal=st.get("activeKilocalories"),
                  rhr=st.get("restingHeartRate"),
                  body_battery=st.get("bodyBatteryHighestValue"),
                  stress=st.get("averageStressLevel") if (st.get("averageStressLevel") or -1) >= 0 else None,
                  exercise_min=(st.get("moderateIntensityMinutes") or 0) + 2 * (st.get("vigorousIntensityMinutes") or 0) or None)
        except Exception as e:
            print(f"  stats {ds}: {e}")
        try:
            sl = (g.get_sleep_data(ds) or {}).get("dailySleepDTO") or {}
            if sl.get("sleepTimeSeconds"):
                merge(rows, ds, "garmin",
                      sleep_min=s_to_min(sl.get("sleepTimeSeconds")),
                      deep_min=s_to_min(sl.get("deepSleepSeconds")),
                      rem_min=s_to_min(sl.get("remSleepSeconds")),
                      light_min=s_to_min(sl.get("lightSleepSeconds")),
                      awake_min=s_to_min(sl.get("awakeSleepSeconds")),
                      sleep_score=((sl.get("sleepScores") or {}).get("overall") or {}).get("value"),
                      resp_rate=sl.get("averageRespirationValue"))
        except Exception as e:
            print(f"  sleep {ds}: {e}")
        try:
            hrv = ((g.get_hrv_data(ds) or {}).get("hrvSummary") or {}).get("lastNightAvg")
            merge(rows, ds, "garmin", hrv=hrv)
        except Exception as e:
            print(f"  hrv {ds}: {e}")

    # drop days that returned nothing
    sb_upsert("daily_metrics", [r for r in rows.values() if len(r) > 2], "date,source")

    wo = []
    start = (today - timedelta(days=days)).isoformat()
    for a in g.get_activities_by_date(start, today.isoformat()) or []:
        wo.append({"source": "garmin", "external_id": str(a["activityId"]),
                   "start_time": (a.get("startTimeGMT") or "").replace(" ", "T") + "Z" if a.get("startTimeGMT") else None,
                   "date": (a.get("startTimeLocal") or "")[:10] or None,
                   "sport": (a.get("activityType") or {}).get("typeKey", "").replace("_", " ").title() or a.get("activityName"),
                   "duration_min": s_to_min(a.get("duration")), "distance_m": a.get("distance") or None,
                   "avg_hr": a.get("averageHR"), "max_hr": a.get("maxHR"),
                   "kcal": a.get("calories"), "strain": None})
    sb_upsert("workouts", wo, "source,external_id")


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=int(os.environ.get("SYNC_DAYS", 3)))
    args = ap.parse_args()
    if not (SUPABASE_URL and SUPABASE_KEY):
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY")

    failed = False
    for fn in (sync_whoop, sync_garmin):
        try:
            fn(args.days)
        except Exception as e:  # one source failing shouldn't stop the other
            failed = True
            print(f"  ERROR: {e}")
    print("Done." if not failed else "Done with errors.")
    sys.exit(1 if failed else 0)
