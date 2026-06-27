"""
Launch a Chromium-based browser with remote debugging on port 9222.

Uses a copy of your real Chrome profile so all existing logins (LinkedIn, GCP,
Gmail, etc.) are available to the agent — no re-authentication needed.

Usage:
  python launch_chrome_cdp.py                  # default: Chrome
  python launch_chrome_cdp.py --browser edge
  python launch_chrome_cdp.py --browser brave
  python launch_chrome_cdp.py --fresh          # wipe session copy and start clean
"""
import subprocess
import time
import urllib.request
import sys
import os
import shutil
import argparse

CDP_PORT = 9222

BROWSERS = {
    "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "edge":   r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "brave":  r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
}

REAL_PROFILES = {
    "chrome": os.path.join(os.environ["LOCALAPPDATA"], "Google", "Chrome", "User Data"),
    "edge":   os.path.join(os.environ["LOCALAPPDATA"], "Microsoft", "Edge", "User Data"),
    "brave":  os.path.join(os.environ["LOCALAPPDATA"], "BraveSoftware", "Brave-Browser", "User Data"),
}

# Files that must not be copied — Chrome locks these and the real instance needs them
LOCK_PATTERNS = (
    "*.lock", "SingletonLock", "SingletonCookie", "SingletonSocket",
    "lockfile", "LOCK", "*.tmp",
)

def is_cdp_up():
    try:
        urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json/version", timeout=2)
        return True
    except Exception:
        return False

def copy_profile(src, dst):
    print(f"Copying Chrome profile to session dir (first run only — takes ~30s)...")
    print(f"  From: {src}")
    print(f"  To:   {dst}")
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*LOCK_PATTERNS))
    print("  Profile copy done.")

parser = argparse.ArgumentParser(description="Launch browser with CDP debugging")
parser.add_argument("--browser", choices=list(BROWSERS), default="chrome")
parser.add_argument("--fresh", action="store_true",
                    help="Delete session copy and re-copy from real profile")
args = parser.parse_args()

if is_cdp_up():
    print(f"CDP already running on port {CDP_PORT}.")
    sys.exit(0)

exe = BROWSERS[args.browser]
if not os.path.exists(exe):
    print(f"Error: {args.browser} not found at {exe}")
    print("Edit BROWSERS dict in this file to set the correct path.")
    sys.exit(1)

real_profile    = REAL_PROFILES[args.browser]
session_profile = os.path.join(os.environ["TEMP"], f"{args.browser}-cdp-session-profile")

if args.fresh and os.path.exists(session_profile):
    print("--fresh: removing old session profile copy...")
    shutil.rmtree(session_profile)

if not os.path.exists(session_profile):
    if os.path.exists(real_profile):
        copy_profile(real_profile, session_profile)
    else:
        print(f"Real profile not found at {real_profile} — using blank profile.")
        os.makedirs(session_profile, exist_ok=True)
else:
    print(f"Using existing session profile: {session_profile}")
    print("  (run with --fresh to re-copy from your real Chrome profile)")

print(f"\nLaunching {args.browser} with CDP on port {CDP_PORT}...")
subprocess.Popen([
    exe,
    f"--remote-debugging-port={CDP_PORT}",
    f"--user-data-dir={session_profile}",
    "--remote-allow-origins=*",
    "--no-first-run",
    "--no-default-browser-check",
    "about:blank",
])

for i in range(15):
    time.sleep(1)
    if is_cdp_up():
        print(f"Ready. {args.browser} CDP live at http://localhost:{CDP_PORT}")
        print(f"Logged-in sessions from your real profile are available.")
        sys.exit(0)
    print(f"  Waiting... ({i+1}/15)")

print("ERROR: Browser did not start CDP in time.")
sys.exit(1)
