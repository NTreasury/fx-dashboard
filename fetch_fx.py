# -*- coding: utf-8 -*-
import json
import time
import base64
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

try:
    import requests
    import certifi
except ImportError as e:
    print(f"pip install requests certifi")
    raise

# ---- settings ----
import os
AUTH_KEY     = os.environ.get("EXIM_AUTH_KEY", "izAbe7kGpFDD4LHBX02AgeI3qCkEQT3I")
GITHUB_TOKEN = "YOUR_TOKEN_HERE"
GITHUB_USER  = "NTreasury"
GITHUB_REPO  = "fx-dashboard"
GITHUB_FILE  = "fx_data.json"

API_URL   = "https://www.koreaexim.go.kr/site/program/financial/exchangeJSON"
DATA_FILE = Path(__file__).parent / "fx_data.json"
TARGETS   = ["USD", "EUR", "JPY"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def fetch_rates(date_str):
    try:
        r = requests.get(
            API_URL,
            params={"authkey": AUTH_KEY, "searchdate": date_str, "data": "AP01"},
            timeout=10,
            verify=certifi.where(),
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.error(f"API error ({date_str}): {e}")
        return None

    if not data or not isinstance(data, list):
        log.warning(f"no data ({date_str}) - holiday?")
        return None

    rates = {}
    for item in data:
        cur = item.get("cur_unit", "").replace("(100)", "")
        if cur not in TARGETS:
            continue
        try:
            rate = float(item.get("deal_bas_r", "").replace(",", ""))
            if cur == "JPY":
                rate = round(rate / 100, 6)
            rates[f"{cur}/KRW"] = rate
        except Exception:
            pass

    if not rates:
        return None

    u = rates.get("USD/KRW")
    e = rates.get("EUR/KRW")
    j = rates.get("JPY/KRW")
    if u and j: rates["USD/JPY"] = round(u / j, 3)
    if e and j: rates["EUR/JPY"] = round(e / j, 3)
    if u and e: rates["EUR/USD"] = round(e / u, 5)
    return rates


def load_data():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"updated": "", "rates": {}}


def save_data(data):
    kst = datetime.utcnow() + timedelta(hours=9)
    data["updated"] = kst.strftime("%Y-%m-%d %H:%M:%S")
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(f"saved: {DATA_FILE} ({len(data['rates'])} days)")


def push_github(data):
    if GITHUB_TOKEN == "YOUR_TOKEN_HERE":
        log.warning("GitHub token not set - skip upload")
        return False

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{GITHUB_FILE}"

    sha = None
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception as e:
        log.error(f"GitHub SHA error: {e}")
        return False

    content = json.dumps(data, ensure_ascii=False, indent=2)
    payload = {
        "message": f"auto update {data.get('updated', '')}",
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
    }
    if sha:
        payload["sha"] = sha

    try:
        r = requests.put(url, headers=headers, json=payload, timeout=15)
        if r.status_code in (200, 201):
            log.info(f"GitHub upload OK: https://{GITHUB_USER}.github.io/{GITHUB_REPO}/")
            return True
        else:
            log.error(f"GitHub upload failed: {r.status_code} {r.text[:200]}")
            return False
    except Exception as e:
        log.error(f"GitHub upload error: {e}")
        return False


def collect_today(data):
    today     = datetime.now().strftime("%Y%m%d")
    today_key = datetime.now().strftime("%Y-%m-%d")
    log.info(f"fetching: {today_key}")
    rates = fetch_rates(today)
    if rates:
        data["rates"][today_key] = rates
        for k, v in rates.items():
            log.info(f"  {k}: {v}")
        return True
    log.warning("no data today - retry after 11:00 AM")
    return False


def collect_backfill(data, years=2):
    end   = datetime.now()
    start = end - timedelta(days=365 * years)
    existing = set(data["rates"].keys())
    dates = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            key = d.strftime("%Y-%m-%d")
            if key not in existing:
                dates.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)

    log.info(f"backfill start: {len(dates)} days")
    success = 0
    for i, ds in enumerate(dates):
        dk = f"{ds[:4]}-{ds[4:6]}-{ds[6:]}"
        r  = fetch_rates(ds)
        if r:
            data["rates"][dk] = r
            success += 1
        if (i + 1) % 20 == 0:
            log.info(f"  progress: {i+1}/{len(dates)} ({success} ok)")
            save_data(data)
        time.sleep(0.3)
    log.info(f"backfill done: {success}/{len(dates)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--date", type=str)
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    data = load_data()

    if args.backfill:
        collect_backfill(data)
        save_data(data)
        if not args.no_push:
            push_github(data)

    elif args.date:
        dk = f"{args.date[:4]}-{args.date[4:6]}-{args.date[6:]}"
        r  = fetch_rates(args.date)
        if r:
            data["rates"][dk] = r
            log.info(f"{dk}: {r}")
        save_data(data)
        if not args.no_push:
            push_github(data)

    else:
        ok = collect_today(data)
        save_data(data)
        if ok and not args.no_push:
            push_github(data)


if __name__ == "__main__":
    main()
