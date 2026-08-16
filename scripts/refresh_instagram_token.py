from __future__ import annotations

import argparse
import os
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def load_env_file(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"[ERROR] env file missing: {path}")
    text = path.read_text(encoding="utf-8-sig")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return text


def required_env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise SystemExit(f"[ERROR] Missing environment variable: {key}")
    return value


def replace_env_value(text: str, key: str, value: str) -> str:
    lines = text.splitlines()
    replaced = False
    out: list[str] = []
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
    return "\n".join(out).rstrip() + "\n"


def refresh_token(current_token: str) -> tuple[str, int | None]:
    response = requests.get(
        "https://graph.instagram.com/refresh_access_token",
        params={
            "grant_type": "ig_refresh_token",
            "access_token": current_token,
        },
        timeout=60,
    )

    if not response.ok:
        message = ""
        try:
            payload = response.json()
            message = str(payload.get("error", {}).get("message", ""))
        except Exception:
            message = response.text[:500]

        low = message.lower()
        # Meta only permits refresh after a long-lived token is at least 24 hours old.
        # A newly-created token can therefore harmlessly miss its first scheduled refresh.
        if response.status_code == 400 and ("24" in low or "hour" in low or "too early" in low):
            print("[SKIP] Instagram token is not old enough to refresh yet; keeping the current token.")
            return current_token, None
        raise SystemExit(f"[ERROR] Instagram token refresh failed: HTTP {response.status_code}: {message}")

    payload = response.json()
    new_token = str(payload.get("access_token", "")).strip()
    if not new_token:
        raise SystemExit("[ERROR] Instagram refresh response did not include access_token")

    expires_in = payload.get("expires_in")
    try:
        expires_in_int = int(expires_in) if expires_in is not None else None
    except (TypeError, ValueError):
        expires_in_int = None
    return new_token, expires_in_int


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="Path for the refreshed AUTOPOST_ENV bundle")
    args = parser.parse_args()

    env_text = load_env_file(ENV_PATH)
    current_token = required_env("INSTAGRAM_ACCESS_TOKEN")
    required_env("INSTAGRAM_USER_ID")
    required_env("GITHUB_SECRET_PAT")

    new_token, expires_in = refresh_token(current_token)
    if new_token == current_token:
        print("[NO CHANGE] Instagram access token was not rotated.")
        raise SystemExit(2)

    refreshed_text = replace_env_value(env_text, "INSTAGRAM_ACCESS_TOKEN", new_token)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(refreshed_text, encoding="utf-8")

    if expires_in:
        days = expires_in / 86400
        print(f"[PASS] Instagram token refreshed; new lifetime reported by Meta: about {days:.1f} days.")
    else:
        print("[PASS] Instagram token refreshed.")
    print("[PASS] Refreshed AUTOPOST_ENV bundle prepared for secure GitHub Secret replacement.")


if __name__ == "__main__":
    main()
