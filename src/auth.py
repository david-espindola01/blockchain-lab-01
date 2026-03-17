import json, hmac, hashlib, socket, binascii
from flask import request
import requests as http
from config import SHARED_SECRET
from secretsharing import SecretSharer


# ─── HMAC ─────────────────────────────────────────────────────────────────────

def _sign(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True).encode()
    return hmac.new(SHARED_SECRET, raw, hashlib.sha256).hexdigest()

def _verify_sig(payload: dict, sig: str) -> bool:
    return hmac.compare_digest(_sign(payload), sig)

def _signed_post(url: str, data: dict, timeout: int = 3):
    try:
        body = {**data, "_sig": _sign(data)}
        r = http.post(url, json=body, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def _get(url: str, timeout: int = 3):
    try:
        r = http.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def _extract_and_verify() -> tuple[dict | None, str]:
    data = request.get_json(force=True, silent=True)
    if not data:
        return None, "empty body"
    sig = data.pop("_sig", None)
    if not sig or not _verify_sig(data, sig):
        return None, "invalid HMAC"
    return data, ""


# ─── IP ───────────────────────────────────────────────────────────────────────

def my_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]; s.close()
        if not ip.startswith("127."): return ip
    except Exception:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if not ip.startswith("127."): return ip
    except Exception:
        pass
    return "127.0.0.1"

def my_addr(app) -> str:
    override = app.config.get("HOST_OVERRIDE")
    ip = override if override else my_ip()
    return f"{ip}:{app.config['PORT']}"


# ─── Shamir's Secret Sharing ──────────────────────────────────────────────────

def split_secret(message: str, n: int, k: int) -> list[str]:
    hex_secret = binascii.hexlify(message.encode()).decode()
    return SecretSharer.split_secret(hex_secret, k, n)


def recover_secret(shares: list[str]) -> str:
    hex_secret = SecretSharer.recover_secret(shares)
    return binascii.unhexlify(hex_secret).decode()