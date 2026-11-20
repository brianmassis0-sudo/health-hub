"""
One-time Garmin sign-in. Run on your computer:
    python sync/garmin_auth.py

It asks for your Garmin email + password (and MFA code if you use it), then prints
a long token string. Copy that whole string into a GitHub secret named GARMIN_TOKENS.
Your password is NOT saved anywhere. Tokens last about a year; re-run this if Garmin sync stops.
"""
import getpass

from garminconnect import Garmin

email = input("Garmin email: ").strip()
password = getpass.getpass("Garmin password: ")
g = Garmin(email=email, password=password, prompt_mfa=lambda: input("MFA code: ").strip())
g.login()
print(f"\n✅ Signed in as {g.get_full_name()}\n")
print("Copy EVERYTHING between the lines into a GitHub secret named GARMIN_TOKENS:\n")
print("-" * 60)
print(g.client.dumps())
print("-" * 60)
