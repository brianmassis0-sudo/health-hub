# Health Hub — WHOOP + Garmin + Apple Health → one dashboard (free)

```
WHOOP API ─┐
           ├─ GitHub Actions (2×/day) ─┐
Garmin  ───┘                           ├─► Supabase (Postgres) ─► Dashboard (GitHub Pages)
iPhone Shortcut (daily, Apple Health) ─┘
```

Everything runs on free tiers. Setup takes about 1–2 hours, once.
Want a preview first? Open `docs/index.html` and click **Try demo data**.

---

## Step 1 — Supabase (database) · ~10 min

1. Sign up at **supabase.com** → **New project** (any name, pick a region near Miami such as `us-east-1`, save the DB password somewhere).
2. Open **SQL Editor** → **New query** and paste in all of `supabase/schema.sql`.
3. **On the last line, replace `CHANGE-ME-to-a-long-random-string`** with your own random secret (e.g. 30+ characters from a password generator). This is your **app secret**. Click **Run**.
4. Go to **Project Settings → API** and copy three values:
   - **Project URL** → `https://xxxx.supabase.co`
   - **anon public** key (safe for the dashboard and the Shortcut)
   - **service_role** key (⚠️ keep this private; it's only used by the sync script)

> Free Supabase projects pause after 7 days with no activity. The twice-daily sync counts as activity, so yours stays awake.

## Step 2 — GitHub repo · ~10 min

1. Create a GitHub account, then a **new repository** (e.g. `health-hub`). Upload all of these files, keeping the folders.
   - You can make it **Public**: it holds no keys or data, and free GitHub Pages needs a public repo. If you'd rather keep it private, host the dashboard on Netlify or Cloudflare Pages (both free) instead.
2. **Settings → Secrets and variables → Actions → New repository secret.** Add:
   | Name | Value |
   |---|---|
   | `SUPABASE_URL` | your Project URL |
   | `SUPABASE_SERVICE_KEY` | the **service_role** key |
   | `WHOOP_CLIENT_ID` | from Step 3 |
   | `WHOOP_CLIENT_SECRET` | from Step 3 |
   | `GARMIN_TOKENS` | from Step 4 |

## Step 3 — WHOOP · ~15 min

1. Go to **developer.whoop.com**, sign in with your WHOOP account, and **create a Team**, then an **App**.
2. **Redirect URL:** `http://localhost:8765/callback`
3. **Scopes:** tick all the `read:` scopes and **offline**.
4. Copy the **Client ID** and **Client Secret** into the GitHub secrets above.
5. On your computer (needs Python 3.10+):
   ```bash
   pip install -r sync/requirements.txt
   export SUPABASE_URL=https://xxxx.supabase.co
   export SUPABASE_SERVICE_KEY=your-service-role-key
   export WHOOP_CLIENT_ID=...
   export WHOOP_CLIENT_SECRET=...
   python sync/whoop_auth.py
   ```
   A browser opens: approve it, and you'll see "WHOOP connected". The token is saved in Supabase and renews itself automatically.

## Step 4 — Garmin · ~5 min

```bash
python sync/garmin_auth.py
```
Enter your Garmin email, password and MFA code if asked. Copy the token text it prints into the GitHub secret `GARMIN_TOKENS`. Your password isn't stored anywhere.

> This uses the unofficial `garminconnect` library (Garmin's official API is for businesses only). If Garmin changes its login, run `pip install -U garminconnect` and re-run this step.

## Step 5 — First sync · ~2 min

In GitHub, go to **Actions → Health sync → Run workflow**, set **days = 90**, and run it. The log shows how many rows were saved.
After that it runs automatically at ~7:17 am and ~5:17 pm.

## Step 6 — iPhone Shortcut for Apple Health · ~15 min

This sends yesterday's Apple Health totals each morning. In the **Shortcuts** app, create a new shortcut called **"Health Hub Sync"** and add these actions in order:

1. **Date**: Current Date → **Adjust Date**: subtract **1 day**. Rename the result `Yesterday`.
2. **Format Date**: `Yesterday`, Custom format `yyyy-MM-dd`. Rename it `DayString`.
3. **Steps:**
   - **Find Health Samples** where *Type* is **Steps** and *Start Date* **is** `Yesterday` (same day)
   - **Get Details of Health Sample**: Value
   - **Calculate Statistics**: **Sum**. Rename it `Steps`.
4. **Active energy:** repeat step 3 with *Type* **Active Energy** → Sum. Rename it `ActiveKcal`.
5. **Exercise minutes:** repeat with *Type* **Exercise Minutes** → Sum. Rename it `ExerciseMin`.
6. **Resting HR:** repeat with *Type* **Resting Heart Rate** → **Average**. Rename it `RHR`.
7. **Weight** (optional): **Find Health Samples** of *Type* **Weight**, sorted by *Start Date*, Latest First, Limit 1 → Get Details: Value. Set the unit to **kg** in the action, then rename it `Weight`.
8. **Dictionary** with these keys (Text type for `date`, Number type for the rest):
   - `date` → `DayString`
   - `steps` → `Steps`
   - `active_kcal` → `ActiveKcal`
   - `exercise_min` → `ExerciseMin`
   - `rhr` → `RHR`
   - `weight_kg` → `Weight`
9. **Get Contents of URL:**
   - URL: `https://xxxx.supabase.co/rest/v1/rpc/ingest_apple`
   - Method: **POST**
   - Headers: `apikey` = *your anon key*, `Content-Type` = `application/json`
   - Request Body: **JSON**, with two fields:
     - `secret` (Text) = *your app secret*
     - `payload` (Dictionary) = the Dictionary from step 8
10. Run it once by hand. It should return `"ok 2026-…"`, and the first time iOS will ask for Health access, so allow it.

**Automate it:** go to **Shortcuts → Automation → + → Time of Day**, pick **8:00 AM, Daily**, select **Run Immediately**, and choose **Health Hub Sync**.

> Turn on Apple Health syncing inside the WHOOP and Garmin apps too, so Apple Health has everything in one place. The dashboard picks the best source for each metric, so nothing is double-counted.

## Step 7 — Dashboard · ~5 min

1. In the GitHub repo, go to **Settings → Pages → Source: Deploy from a branch → `main` / `/docs`** and click Save.
2. After about a minute your dashboard is live at `https://YOUR-USERNAME.github.io/health-hub/`.
3. Open it and enter the **Project URL**, **anon key** and **app secret**. These are saved only on that device.
4. On iPhone, open it in Safari and tap **Share → Add to Home Screen**, so it opens like an app.

---

## What shows where

| Metric | Source used (first available) |
|---|---|
| Recovery, Strain | WHOOP |
| HRV, Resting HR, Sleep stages | WHOOP → Garmin → Apple |
| Body Battery, Stress | Garmin |
| Steps, Active calories | whichever is higher of Garmin / Apple |
| Weight | Apple → Garmin |
| Workouts | Garmin + WHOOP, merged when they start within 15 min of each other |

## Troubleshooting

- **Dashboard says "unauthorized":** the app secret doesn't match the one in `schema.sql`.
- **WHOOP token refresh failed:** re-run `python sync/whoop_auth.py`.
- **Garmin errors / 401:** re-run `python sync/garmin_auth.py` and update the `GARMIN_TOKENS` secret.
- **Shortcut error 401:** check the `apikey` header uses the **anon** key.
- **No data for today yet:** WHOOP and Garmin finalize sleep after you wake up and sync your device. The next scheduled run will pick it up, or you can run the workflow by hand.

## Security notes

- Your data tables can't be read with the public anon key. The only way in is the two functions (`get_dashboard`, `ingest_apple`), and both require your app secret.
- Never put the **service_role** key in the dashboard or the Shortcut.
- To change your app secret, run this in the Supabase SQL Editor:
  `update app_secrets set value = 'new-secret' where key = 'app_secret';`
