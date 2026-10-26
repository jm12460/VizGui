"""SmartCast control (TV remote keys + status) and pairing.

Uses the active SmartCast target via ``config.HOST`` / ``config.TOK``.
"""
import json
import ssl
import urllib.request

from . import config

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

# SmartCast key codes (codeset, code).
KEYS = {
    "power": (11, 2), "input": (7, 1),
    "volup": (5, 1), "voldown": (5, 0), "mute": (5, 4),
    "up": (3, 8), "down": (3, 0), "left": (3, 1), "right": (3, 7),
    "ok": (3, 2), "back": (4, 0), "exit": (9, 0),
    "home": (4, 15), "menu": (4, 8), "info": (4, 6),
}


def _req(method, path, body=None):
    if not config.HOST:
        raise RuntimeError("no SmartCast TV configured")
    url = f"https://{config.HOST}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json", "AUTH": config.TOK})
    with urllib.request.urlopen(r, context=_SSL, timeout=6) as resp:
        return json.load(resp)


def sc_key(codeset, code):
    return _req("PUT", "/key_command/",
                {"KEYLIST": [{"CODESET": codeset, "CODE": code, "ACTION": "KEYPRESS"}]})


def sc_status():
    """-> dict(power,input,vol,mute) or {'error': str}."""
    try:
        pw = _req("GET", "/state/device/power_mode")["ITEMS"][0]["VALUE"]
        inp = _req("GET", "/menu_native/dynamic/tv_settings/devices/current_input")["ITEMS"][0]["VALUE"]
        vol = mute = "?"
        for i in _req("GET", "/menu_native/dynamic/tv_settings/audio")["ITEMS"]:
            if i.get("CNAME") == "volume":
                vol = i["VALUE"]
            if i.get("CNAME") == "mute":
                mute = i["VALUE"]
        return {"power": "on" if pw else "off", "input": inp, "vol": vol, "mute": mute}
    except Exception as e:
        return {"error": str(e)}


def _sc_put(ip, port, path, body):
    url = f"https://{ip}:{port}{path}"
    r = urllib.request.Request(url, data=json.dumps(body).encode(), method="PUT",
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, context=_SSL, timeout=8) as resp:
        return json.load(resp)


def sc_pair_start(ip, port="7345", device_id="vizgui"):
    it = _sc_put(ip, port, "/pairing/start",
                 {"DEVICE_ID": device_id, "DEVICE_NAME": device_id})["ITEM"]
    return it["PAIRING_REQ_TOKEN"], it.get("CHALLENGE_TYPE", 1)


def sc_pair_finish(ip, port, req_token, code, challenge=1, device_id="vizgui"):
    return _sc_put(ip, port, "/pairing/pair",
                   {"DEVICE_ID": device_id, "CHALLENGE_TYPE": challenge,
                    "RESPONSE_VALUE": str(code), "PAIRING_REQ_TOKEN": req_token})["ITEM"]["AUTH_TOKEN"]
