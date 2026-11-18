import json
import re
import urllib.parse
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

# ── Netflix API constants ──────────────────────────────────────────────────
API_URL = "https://ios.prod.ftl.netflix.com/iosui/user/15.48"

QUERY_PARAMS = {
    "appVersion": "15.48.1",
    "config": '{"gamesInTrailersEnabled":"false","isTrailersEvidenceEnabled":"false","cdsMyListSortEnabled":"true","kidsBillboardEnabled":"true","addHorizontalBoxArtToVideoSummariesEnabled":"false","skOverlayTestEnabled":"false","homeFeedTestTVMovieListsEnabled":"false","baselineOnIpadEnabled":"true","trailersVideoIdLoggingFixEnabled":"true","postPlayPreviewsEnabled":"false","bypassContextualAssetsEnabled":"false","roarEnabled":"false","useSeason1AltLabelEnabled":"false","disableCDSSearchPaginationSectionKinds":["searchVideoCarousel"],"cdsSearchHorizontalPaginationEnabled":"true","searchPreQueryGamesEnabled":"true","kidsMyListEnabled":"true","billboardEnabled":"true","useCDSGalleryEnabled":"true","contentWarningEnabled":"true","videosInPopularGamesEnabled":"true","avifFormatEnabled":"false","sharksEnabled":"true"}',
    "device_type": "NFAPPL-02-",
    "esn": "NFAPPL-02-IPHONE8%3D1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "idiom": "phone",
    "iosVersion": "15.8.5",
    "isTablet": "false",
    "languages": "en-US",
    "locale": "en-US",
    "maxDeviceWidth": "375",
    "model": "saget",
    "modelType": "IPHONE8-1",
    "odpAware": "true",
    "path": '["account","token","default"]',
    "pathFormat": "graph",
    "pixelDensity": "2.0",
    "progressive": "false",
    "responseFormat": "json",
}

ACCOUNT_PARAMS = {**QUERY_PARAMS, "path": '["account","customer"]'}

BASE_HEADERS = {
    "User-Agent": "Argo/15.48.1 (iPhone; iOS 15.8.5; Scale/2.00)",
    "x-netflix.request.attempt": "1",
    "x-netflix.request.client.user.guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.context.profile-guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.request.routing": '{"path":"/nq/mobile/nqios/~15.48.0/user","control_tag":"iosui_argo"}',
    "x-netflix.context.app-version": "15.48.1",
    "x-netflix.argo.translated": "true",
    "x-netflix.context.form-factor": "phone",
    "x-netflix.context.sdk-version": "2012.4",
    "x-netflix.client.appversion": "15.48.1",
    "x-netflix.context.max-device-width": "375",
    "x-netflix.context.ab-tests": "",
    "x-netflix.tracing.cl.useractionid": "4DC655F2-9C3C-4343-8229-CA1B003C3053",
    "x-netflix.client.type": "argo",
    "x-netflix.client.ftl.esn": "NFAPPL-02-IPHONE8=1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "x-netflix.context.locales": "en-US",
    "x-netflix.context.top-level-uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.client.iosversion": "15.8.5",
    "accept-language": "en-US;q=1",
    "x-netflix.argo.abtests": "",
    "x-netflix.context.os-version": "15.8.5",
    "x-netflix.request.client.context": '{"appState":"foreground"}',
    "x-netflix.context.ui-flavor": "argo",
    "x-netflix.argo.nfnsm": "9",
    "x-netflix.context.pixel-density": "2.0",
    "x-netflix.request.toplevel.uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.request.client.timezoneid": "Asia/Dhaka",
}

COOKIE_KEYS     = ("NetflixId", "SecureNetflixId", "nfvdid", "OptanonConsent")
REQUIRED_COOKIE = "NetflixId"
NETSCAPE_MIN_COLS = 7


# ── Cookie parsing ─────────────────────────────────────────────────────────

def _decode_value(v: str) -> str:
    if "%" in v:
        try:
            return urllib.parse.unquote(v)
        except Exception:
            pass
    return v


def extract_cookie_dict(text: str) -> dict:
    out = {}

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= NETSCAPE_MIN_COLS:
            out[parts[5]] = parts[6]

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, list):
        for c in data:
            n, v = c.get("name"), c.get("value")
            if n in COOKIE_KEYS and isinstance(v, str):
                out[n] = _decode_value(v)
    elif isinstance(data, dict):
        if any(k in data for k in COOKIE_KEYS):
            for k in COOKIE_KEYS:
                v = data.get(k)
                if isinstance(v, str):
                    out[k] = _decode_value(v)
        elif isinstance(data.get("cookies"), list):
            for c in data["cookies"]:
                n, v = c.get("name"), c.get("value")
                if n in COOKIE_KEYS and isinstance(v, str):
                    out[n] = _decode_value(v)

    for key in COOKIE_KEYS:
        if key in out:
            continue
        m = re.search(rf"(?<!\w){re.escape(key)}=([^;,\s]+)", text)
        if m:
            out[key] = _decode_value(m.group(1))

    return out


# ── Netflix API calls ──────────────────────────────────────────────────────

def _fetch_token(nid: str) -> tuple[str, int | None]:
    headers = {**BASE_HEADERS, "Cookie": f"NetflixId={nid}"}
    resp = requests.get(API_URL, params=QUERY_PARAMS, headers=headers, timeout=20, verify=False)
    resp.raise_for_status()
    data = resp.json()
    td = (((data.get("value") or {}).get("account") or {}).get("token") or {}).get("default") or {}
    token   = td.get("token")
    expires = td.get("expires")
    if not token:
        raise ValueError("No token in response")
    if isinstance(expires, int) and len(str(expires)) == 13:
        expires //= 1000
    return token, expires


def _fetch_account(nid: str) -> dict:
    empty = {
        "email": "N/A", "membership": "N/A", "country": "N/A",
        "member_since": "N/A", "num_profiles": "N/A",
        "can_stream": "N/A", "max_streams": "N/A", "subscription_price": "N/A",
    }
    headers = {**BASE_HEADERS, "Cookie": f"NetflixId={nid}"}
    try:
        resp = requests.get(API_URL, params=ACCOUNT_PARAMS, headers=headers, timeout=20, verify=False)
        resp.raise_for_status()
        raw  = resp.json()
        acct = ((raw.get("value") or {}).get("account") or {})
        cust = acct.get("customer") or {}
        if isinstance(cust, dict) and "primaryEmail" not in cust:
            cust = cust.get("customer") or cust

        def g(key):
            v = cust.get(key)
            return str(v) if v not in (None, "", {}) else "N/A"

        streaming = cust.get("currentMembership") or cust.get("membership") or {}
        return {
            "email":              g("primaryEmail"),
            "membership":         g("membershipStatus"),
            "country":            g("countryOfSignup"),
            "member_since":       g("memberSince"),
            "num_profiles":       g("numProfiles"),
            "can_stream":         g("canStream"),
            "max_streams":        str(streaming.get("maxStreams", "N/A")),
            "subscription_price": str(streaming.get("billingMonthlyTotalPrice", "N/A")),
        }
    except Exception:
        return empty


def _fmt_expiry(ts) -> str:
    if not isinstance(ts, (int, float)):
        return "Unknown"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def _validity_label(ts) -> str:
    if not isinstance(ts, (int, float)):
        return "Unknown"
    delta = int(ts) - int(datetime.now(timezone.utc).timestamp())
    if delta <= 0:
        return "Expired"
    d, rem  = divmod(delta, 86400)
    h, rem  = divmod(rem, 3600)
    m       = rem // 60
    if d > 0: return f"{d}d {h}h {m}m"
    if h > 0: return f"{h}h {m}m"
    return f"{m}m"


# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/check", methods=["POST"])
def api_check():
    """
    POST JSON: { "netflix_id": "..." }
    Returns full result object.
    """
    body = request.get_json(force=True, silent=True) or {}
    nid  = (body.get("netflix_id") or "").strip()

    if not nid:
        return jsonify({"error": "Missing netflix_id"}), 400

    try:
        token, expires = _fetch_token(nid)
        account        = _fetch_account(nid)
        return jsonify({
            "status":             "live",
            "netflix_id":         nid,
            "token":              token,
            "token_url":          f"https://netflix.com/?nftoken={token}",
            "expires":            expires,
            "expires_fmt":        _fmt_expiry(expires),
            "valid_for":          _validity_label(expires),
            **account,
        })
    except requests.HTTPError as e:
        code   = e.response.status_code
        status = "dead" if code in (401, 403) else "error"
        return jsonify({"status": status, "error": f"HTTP {code}"}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:200]}), 200


@app.route("/api/check_bulk", methods=["POST"])
def api_check_bulk():
    """
    POST JSON: { "netflix_ids": ["...", "..."] }
    Returns list of result objects in same order.
    """
    body = request.get_json(force=True, silent=True) or {}
    ids  = body.get("netflix_ids") or []

    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "Missing netflix_ids list"}), 400

    ids = [str(i).strip() for i in ids if i][:5000]
    results = []

    for nid in ids:
        try:
            token, expires = _fetch_token(nid)
            account        = _fetch_account(nid)
            results.append({
                "status":             "live",
                "netflix_id":         nid,
                "token":              token,
                "token_url":          f"https://netflix.com/?nftoken={token}",
                "expires":            expires,
                "expires_fmt":        _fmt_expiry(expires),
                "valid_for":          _validity_label(expires),
                **account,
            })
        except requests.HTTPError as e:
            code   = e.response.status_code
            status = "dead" if code in (401, 403) else "error"
            results.append({"status": status, "netflix_id": nid, "error": f"HTTP {code}"})
        except Exception as e:
            results.append({"status": "error", "netflix_id": nid, "error": str(e)[:200]})

    return jsonify(results)


@app.route("/api/parse_cookie", methods=["POST"])
def api_parse_cookie():
    """
    POST JSON: { "text": "raw cookie string" }
    Returns { "netflix_id": "..." } or error.
    Used by frontend to extract NetflixId before sending to /api/check.
    """
    body = request.get_json(force=True, silent=True) or {}
    text = body.get("text", "")
    cd   = extract_cookie_dict(text)
    nid  = cd.get(REQUIRED_COOKIE)
    if not nid:
        return jsonify({"error": "No NetflixId found"}), 400
    return jsonify({"netflix_id": nid})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
