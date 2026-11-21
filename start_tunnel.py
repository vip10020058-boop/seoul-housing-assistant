"""Open a Cloudflare Quick Tunnel to the running ordinance QA server."""

from collections import deque
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import requests


ROOT = Path(__file__).resolve().parent
ORIGIN = "http://127.0.0.1:8000"
DOWNLOAD_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/"
    "cloudflared-windows-amd64.exe"
)
PUBLIC_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com\b")


def find_cloudflared():
    local = ROOT / "cloudflared.exe"
    if local.is_file():
        return local
    installed = shutil.which("cloudflared.exe")
    if installed:
        return Path(installed)
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(variable):
            installed = Path(os.environ[variable]) / "cloudflared" / local.name
            if installed.is_file():
                return installed

    print("[SETUP] Downloading cloudflared from Cloudflare's GitHub releases.", flush=True)
    temporary = local.with_suffix(".exe.part")
    try:
        with requests.get(DOWNLOAD_URL, stream=True, timeout=(10, 30)) as response:
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    output.write(chunk)
        temporary.replace(local)
    finally:
        temporary.unlink(missing_ok=True)
    return local


def main():
    url_file = ROOT / "tunnel_url.txt"
    url_file.unlink(missing_ok=True)
    try:
        with requests.get(f"{ORIGIN}/health", timeout=5) as response:
            response.raise_for_status()
            if response.json().get("ok") is not True:
                raise ValueError("Unexpected /health response")
    except (requests.RequestException, ValueError, AttributeError) as error:
        print(f"[ERROR] Local server check failed: {error}", file=sys.stderr)
        print("Run the server batch file first and keep its window open.", file=sys.stderr)
        return 1

    cloudflared = find_cloudflared()
    print("[START] Requesting a public URL. Keep this window open during grading.", flush=True)
    public_url = None
    recent_lines = deque(maxlen=10)
    with (ROOT / "_tunnel_raw.log").open("w", encoding="utf-8", buffering=1) as log:
        with subprocess.Popen(
            [str(cloudflared), "tunnel", "--url", ORIGIN, "--no-autoupdate"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
        ) as tunnel:
            try:
                for line in tunnel.stdout:
                    log.write(line)
                    recent_lines.append(line)
                    match = PUBLIC_URL.search(line)
                    if match and public_url is None:
                        public_url = match.group()
                        url_file.write_text(f"{public_url}\n", encoding="ascii")
                        if os.name == "nt" and sys.stdout.isatty():
                            os.system("cls")
                        print(f"PUBLIC URL:  {public_url}")
                        print(f"GRADING URL: {public_url}/ask")
                        print()
                        print("Keep this window open until grading finishes.")
                        print("Tunnel log: _tunnel_raw.log", flush=True)
                exit_code = tunnel.wait()
            except (KeyboardInterrupt, OSError):
                if tunnel.poll() is None:
                    tunnel.terminate()
                raise

    print(f"[ERROR] Tunnel stopped (cloudflared exit code {exit_code}).")
    if public_url is None:
        print("[ERROR] No public URL was issued.")
    if recent_lines:
        print("Recent Cloudflare output:")
        print("".join(recent_lines), end="")
    print("See _tunnel_raw.log for the Cloudflare output.")
    return exit_code or 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[STOP] Tunnel interrupted.")
        sys.exit(130)
    except (OSError, requests.RequestException) as error:
        print(f"[ERROR] Could not start or log the tunnel: {error}", file=sys.stderr)
        sys.exit(1)
