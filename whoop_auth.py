"""
One-time WHOOP sign-in. Run on your computer:
    python sync/whoop_auth.py

Needs env vars: WHOOP_CLIENT_ID, WHOOP_CLIENT_SECRET, SUPABASE_URL, SUPABASE_SERVICE_KEY
In your WHOOP developer app, the Redirect URL must be exactly:
    http://localhost:8765/callback
"""
import os
import secrets
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

sys.path.insert(0, os.path.dirname(__file__))
from sync import sb_set_secret, WHOOP_TOKEN_URL  # noqa: E402

CID = os.environ["WHOOP_CLIENT_ID"]
CSEC = os.environ["WHOOP_CLIENT_SECRET"]
REDIRECT = "http://localhost:8765/callback"
SCOPES = "offline read:recovery read:cycles read:sleep read:workout read:profile read:body_measurement"
STATE = secrets.token_urlsafe(12)
result = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if q.get("state", [""])[0] != STATE or "code" not in q:
            self.send_response(400); self.end_headers()
            self.wfile.write(b"Something went wrong - check the terminal.")
            result["error"] = q
            return
        result["code"] = q["code"][0]
        self.send_response(200); self.end_headers()
        self.wfile.write(b"WHOOP connected! You can close this tab.")

    def log_message(self, *a):
        pass


url = "https://api.prod.whoop.com/oauth/oauth2/auth?" + urllib.parse.urlencode({
    "response_type": "code", "client_id": CID, "redirect_uri": REDIRECT,
    "scope": SCOPES, "state": STATE})
print("Opening WHOOP sign-in in your browser…\nIf it doesn't open, paste this:\n", url)
webbrowser.open(url)
HTTPServer(("localhost", 8765), Handler).handle_request()

if "code" not in result:
    sys.exit(f"Sign-in failed: {result.get('error')}")

r = requests.post(WHOOP_TOKEN_URL, data={
    "grant_type": "authorization_code", "code": result["code"], "redirect_uri": REDIRECT,
    "client_id": CID, "client_secret": CSEC}, timeout=30)
r.raise_for_status()
tok = r.json()
if not tok.get("refresh_token"):
    sys.exit("No refresh token returned - make sure the 'offline' scope is enabled in your WHOOP app.")
sb_set_secret("whoop_refresh_token", tok["refresh_token"])
print("✅ WHOOP connected. Refresh token saved to Supabase.")
