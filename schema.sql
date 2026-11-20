-- Health Hub database. Paste this whole file into Supabase → SQL Editor → Run.
-- Before running, change the secret on the last line to your own long random string.

-- One row per day per source (whoop / garmin / apple)
create table if not exists daily_metrics (
  date          date        not null,
  source        text        not null,
  recovery      numeric,    -- WHOOP recovery %
  hrv           numeric,    -- ms (WHOOP RMSSD / Garmin overnight avg)
  rhr           numeric,    -- resting heart rate, bpm
  spo2          numeric,    -- %
  skin_temp_c   numeric,
  resp_rate     numeric,    -- breaths/min during sleep
  sleep_min     numeric,    -- time asleep
  deep_min      numeric,
  rem_min       numeric,
  light_min     numeric,
  awake_min     numeric,
  sleep_score   numeric,    -- WHOOP sleep performance % / Garmin sleep score
  strain        numeric,    -- WHOOP day strain (0-21)
  steps         numeric,
  active_kcal   numeric,
  exercise_min  numeric,
  body_battery  numeric,    -- Garmin daily high
  stress        numeric,    -- Garmin avg stress
  weight_kg     numeric,
  updated_at    timestamptz default now(),
  primary key (date, source)
);

create table if not exists workouts (
  source        text not null,
  external_id   text not null,
  start_time    timestamptz,
  date          date,
  sport         text,
  duration_min  numeric,
  distance_m    numeric,
  avg_hr        numeric,
  max_hr        numeric,
  kcal          numeric,
  strain        numeric,
  primary key (source, external_id)
);

-- Stores the WHOOP refresh token (it changes on every refresh) and the app secret
create table if not exists app_secrets (
  key   text primary key,
  value text not null,
  updated_at timestamptz default now()
);

-- Lock everything down: no public reads/writes. Only the service key (sync script)
-- and the two secret-checked functions below can touch the data.
alter table daily_metrics enable row level security;
alter table workouts      enable row level security;
alter table app_secrets   enable row level security;

-- Parse a number out of whatever the iPhone Shortcut sends ("8,432", "61.0 bpm", 8432)
create or replace function _num(j jsonb, k text) returns numeric
language sql immutable as $body$
  select nullif(regexp_replace(coalesce(j->>k, ''), '[^0-9.\-]', '', 'g'), '')::numeric
$body$;

create or replace function _check_secret(s text) returns void
language plpgsql security definer set search_path = public as $body$
begin
  if s is null or s is distinct from (select value from app_secrets where key = 'app_secret') then
    raise exception 'unauthorized';
  end if;
end $body$;

-- Called by the iPhone Shortcut every morning
create or replace function ingest_apple(secret text, payload jsonb) returns text
language plpgsql security definer set search_path = public as $body$
begin
  perform _check_secret(secret);
  insert into daily_metrics (date, source, steps, active_kcal, exercise_min, rhr, hrv, weight_kg, sleep_min, updated_at)
  values (coalesce((payload->>'date')::date, current_date - 1), 'apple',
          _num(payload,'steps'), _num(payload,'active_kcal'), _num(payload,'exercise_min'),
          _num(payload,'rhr'), _num(payload,'hrv'), _num(payload,'weight_kg'), _num(payload,'sleep_min'), now())
  on conflict (date, source) do update set
    steps        = coalesce(excluded.steps,        daily_metrics.steps),
    active_kcal  = coalesce(excluded.active_kcal,  daily_metrics.active_kcal),
    exercise_min = coalesce(excluded.exercise_min, daily_metrics.exercise_min),
    rhr          = coalesce(excluded.rhr,          daily_metrics.rhr),
    hrv          = coalesce(excluded.hrv,          daily_metrics.hrv),
    weight_kg    = coalesce(excluded.weight_kg,    daily_metrics.weight_kg),
    sleep_min    = coalesce(excluded.sleep_min,    daily_metrics.sleep_min),
    updated_at   = now();
  return 'ok ' || coalesce(payload->>'date', 'yesterday');
end $body$;

-- Called by the dashboard
create or replace function get_dashboard(secret text, days int default 60) returns jsonb
language plpgsql security definer set search_path = public as $body$
begin
  perform _check_secret(secret);
  return jsonb_build_object(
    'daily', coalesce((select jsonb_agg(to_jsonb(m) order by m.date, m.source)
                       from daily_metrics m where m.date >= current_date - days), '[]'::jsonb),
    'workouts', coalesce((select jsonb_agg(to_jsonb(w) order by w.start_time desc)
                          from workouts w where w.date >= current_date - days), '[]'::jsonb)
  );
end $body$;

revoke all on function _check_secret(text) from public, anon, authenticated;
grant execute on function ingest_apple(text, jsonb) to anon;
grant execute on function get_dashboard(text, int)  to anon;

-- ⚠️ CHANGE THIS to your own long random string (e.g. from a password generator)
insert into app_secrets (key, value) values ('app_secret', 'CHANGE-ME-to-a-long-random-string')
on conflict (key) do update set value = excluded.value;

-- Newer Supabase projects don't auto-grant table access to the sync key
grant usage on schema public to service_role;
grant all on table daily_metrics, workouts, app_secrets to service_role;

-- Checklist + non-negotiables, synced across devices
create table if not exists daily_checks (
  date       date primary key,
  checks     jsonb not null default '{}'::jsonb,
  updated_at timestamptz default now()
);
alter table daily_checks enable row level security;
grant all on table daily_checks to service_role;

create or replace function save_checks(secret text, day date, checks jsonb) returns text
language plpgsql security definer set search_path = public as $body$
begin
  perform _check_secret(secret);
  insert into daily_checks (date, checks, updated_at) values (day, coalesce(checks, '{}'::jsonb), now())
  on conflict (date) do update set checks = excluded.checks, updated_at = now();
  return 'ok';
end $body$;
grant execute on function save_checks(text, date, jsonb) to anon;

create or replace function get_dashboard(secret text, days int default 60) returns jsonb
language plpgsql security definer set search_path = public as $body$
begin
  perform _check_secret(secret);
  return jsonb_build_object(
    'daily', coalesce((select jsonb_agg(to_jsonb(m) order by m.date, m.source)
                       from daily_metrics m where m.date >= current_date - days), '[]'::jsonb),
    'workouts', coalesce((select jsonb_agg(to_jsonb(w) order by w.start_time desc)
                          from workouts w where w.date >= current_date - days), '[]'::jsonb),
    'checks', coalesce((select jsonb_object_agg(c.date::text, c.checks)
                        from daily_checks c where c.date >= current_date - days), '{}'::jsonb)
  );
end $body$;
grant execute on function get_dashboard(text, int) to anon;
