"""
Bulk-create router positions from a CSV file via the Deepfield API.

USAGE:
    python bulk_create_routers.py

BEFORE RUNNING:
    1. Set CSV_PATH, API_URL, and BEARER_TOKEN below.
    2. Review the MAPPING section — match it to your real CSV columns.
       Your CSV headers are:
       device_id, hostname, contact, v4ip, v6ip, isp, series, location, make,
       model, version, clli, rancid_hostname, organization, organization_id,
       status, status_changed, fingerprint, operating_system, autonomous_system,
       created_at, updated_at, has_comment, mrtg
    3. Fields the API schema expects that are NOT in your CSV
       (flow_ip, snmp_ip, snmp_community, city, country) are set to None/defaults
       below — edit these if you have real values, or confirm with the API docs
       whether they're actually required.
    4. Only rows with status == "ACTIVE" are processed by default — change
       STATUS_FILTER if you want all rows.

OUTPUT:
    Prints progress per router and writes results to
    router_creation_results.csv (name, status, position_id or error).
"""

import csv
import json
import sys
import time
import requests
import urllib3

# Suppress "InsecureRequestWarning" since verify=False is used below (self-signed cert).
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------- CONFIG ----------------
CSV_PATH = "routers.csv"                # path to your CSV file
API_URL = "https://98.7.129.213/api/router"   # CORRECTED endpoint - was /api/dimension/router/position/create
BEARER_TOKEN = "krrooiWnoveYu0xkODlYM93Lm"  # confirm this is complete/correct
VERIFY_SSL = False                      # False = skip cert check (self-signed IP)
REQUEST_DELAY_SECONDS = 0.3             # small delay between requests to avoid hammering the API
STATUS_FILTER = "ACTIVE"                # only process rows with this status; set to None to process all rows
DRY_RUN = True                          # True = build payloads and print them WITHOUT sending. Set False to actually POST.
MAX_ROWS = 3                            # SAFETY: only process the first N rows. Set to None to process ALL rows - only do this after confirming test rows work correctly against the corrected endpoint.

RESULTS_CSV = "router_creation_results.csv"
# -----------------------------------------


def build_payload(row: dict) -> dict:
    """
    Map one CSV row to the API's expected JSON schema.
    Adjust field mappings here to match your actual CSV column names/values.
    """
    return {
        "name": row.get("hostname"),
        "description": f"{row.get('make', '')} {row.get('model', '')} router".strip(),
        "loopback0": {
            "v4": row.get("v4ip") or None,
            "v6": row.get("v6ip") or "0:0:0:0",
        },
        "pop": None,
        "configured_vendor": (row.get("make") or "").upper() or None,
        "router": {
            "data_types": ["flow", "bgp", "snmp"],  # CSV has no data_types column; adjust if needed
            "geoip": {
                "city": None,     # not present in CSV as a discrete field; "location" is a free-text address
                "region": None,
                "country": None,  # same — parse from "location" if you need this populated
                "loc": {"lat": None, "lon": None},
                "type": None,
            },
            "flow_ip": None,           # not present in CSV — fill in if you have a real value/source
            "sampling_rate_override": None,
            "mirror_format": 0,
            "snmp_ip": None,           # not present in CSV
            "snmp_community": None,    # not present in CSV — required by API? confirm before running
            "bgp": None,
            "use_bgp_sessions": None,
        },
    }


def main():
    headers = {
        "Authorization": f"Bearer {BEARER_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except FileNotFoundError:
        print(f"ERROR: could not find CSV file at '{CSV_PATH}'. Update CSV_PATH and try again.")
        sys.exit(1)

    if STATUS_FILTER:
        rows = [r for r in rows if r.get("status") == STATUS_FILTER]

    if MAX_ROWS is not None:
        rows = rows[:MAX_ROWS]
        print(f"MAX_ROWS is set to {MAX_ROWS} — only processing the first {len(rows)} row(s). Set MAX_ROWS = None to process all rows.")

    print(f"Loaded {len(rows)} row(s) to process (status filter: {STATUS_FILTER!r}).")
    if DRY_RUN:
        print("DRY_RUN is True — no requests will be sent. Set DRY_RUN = False in the script to actually create routers.\n")

    results = []

    for i, row in enumerate(rows, start=1):
        payload = build_payload(row)
        name = payload["name"] or f"row-{i}"

        print(f"[{i}/{len(rows)}] {name} ...", end=" ")

        if DRY_RUN:
            print("DRY RUN - payload built:")
            print(json.dumps(payload, indent=2))
            results.append({"name": name, "status": "dry_run", "position_id": "", "error": ""})
            continue

        try:
            resp = requests.post(
                API_URL,
                headers=headers,
                data=json.dumps(payload),
                verify=VERIFY_SSL,
                timeout=30,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                # /api/router likely does NOT return "position_id" (that was specific to the old,
                # incorrect /dimension/router/position/create endpoint). Try a few common ID key
                # names instead, and fall back to showing the raw response if none match.
                record_id = ""
                for key in ("id", "router_id", "device_id", "position_id"):
                    if key in data:
                        record_id = data[key]
                        break

                if record_id == "":
                    print(f"OK (status {resp.status_code}) - no obvious ID key found in response.")
                else:
                    print(f"OK (status {resp.status_code}, id={record_id})")

                # Always show the raw response for the first few live rows so we can confirm the
                # real response shape before trusting the rest of the batch.
                if i <= 3:
                    print(f"  RAW RESPONSE: {json.dumps(data)[:500]}")

                results.append({"name": name, "status": "success", "position_id": record_id, "error": ""})
            else:
                print(f"FAILED (status {resp.status_code})")
                results.append({"name": name, "status": f"http_{resp.status_code}", "position_id": "", "error": resp.text[:300]})
        except requests.exceptions.RequestException as e:
            print(f"ERROR: {e}")
            results.append({"name": name, "status": "exception", "position_id": "", "error": str(e)})

        time.sleep(REQUEST_DELAY_SECONDS)

    # Write summary CSV
    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "status", "position_id", "error"])
        writer.writeheader()
        writer.writerows(results)

    print(f"\nDone. Results written to {RESULTS_CSV}")
    success_count = sum(1 for r in results if r["status"] == "success")
    print(f"Summary: {success_count}/{len(results)} succeeded" if not DRY_RUN else f"Summary: {len(results)} payload(s) built (dry run)")


if __name__ == "__main__":
    main()
