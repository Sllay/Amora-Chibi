"""Short-lived, read-only PRISM capability URLs, separate from all Core credentials."""
import base64
import hashlib
import hmac
import re
import secrets
from pathlib import Path
from amora.dev_auth import configured_dev_master_key

TTL = 12 * 3600
PREFIX = "/avatar/v5/prism/"
USER = "bkxd"
def basic_ok(header: bytes, password: str) -> bool:
    if len(password.encode("utf-8")) < 32:
        return False
    candidates = [password]
    master = configured_dev_master_key()
    if master and 32 <= len(master.encode("utf-8")) <= 512:
        candidates.append(master)
    # Keep the two passwords distinct from general API authorization.
    return any(hmac.compare_digest(header, b"Basic " + base64.b64encode((USER+":"+value).encode("utf-8"))) for value in candidates)

def signature(payload, password):
    return hmac.new(password.encode("utf-8"), ("amora.prism.overlay.v1:"+payload).encode("ascii"), hashlib.sha256).hexdigest()

def issue(password, now):
    payload = str(int(now)+TTL)+"."+secrets.token_hex(16)
    return payload+"."+signature(payload,password)

def remaining(token, password, now):
    if not re.fullmatch(r"[0-9]{1,12}\.[a-f0-9]{32}\.[a-f0-9]{64}", token):
        return 0
    expiry, nonce, supplied = token.split(".")
    if not hmac.compare_digest(supplied,signature(expiry+"."+nonce,password)):
        return 0
    left=int(expiry)-int(now)
    return left if 0 < left <= TTL else 0

def asset(root: Path, name: str):
    if name == "live-overlay/":
        name = "live-overlay/index.html"
    allowed = name in {"live-overlay/index.html","presentation.js","presentation.css","identity.json","vendor/live2dcubismcore.min.js"} or (
        name.startswith(("model/","shaders/")) and re.fullmatch(r"[A-Za-z0-9_./-]+",name)
    )
    if not allowed or any(part in {"",".",".."} for part in name.split("/")):
        return None
    p=root/name
    return p if p.is_file() and p.resolve().is_relative_to(root) else None
