import ipaddress
import os
import socket
import time
from urllib.parse import urlparse

import jwt
from cryptography.fernet import Fernet

_fernet = Fernet(os.environ["ENCRYPTION_KEY"].encode())
_JWT_SECRET = os.environ["JWT_SECRET"]


def encrypt(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _fernet.decrypt(value.encode()).decode()


def make_session_token(account_id: str, ttl: int = 7 * 24 * 3600) -> str:
    now = int(time.time())
    return jwt.encode({"sub": account_id, "iat": now, "exp": now + ttl, "typ": "session"}, _JWT_SECRET, "HS256")


def read_session_token(token: str) -> str:
    data = jwt.decode(token, _JWT_SECRET, algorithms=["HS256"])
    if data.get("typ") != "session":
        raise jwt.InvalidTokenError("wrong token type")
    return data["sub"]


def make_oauth_state() -> str:
    now = int(time.time())
    return jwt.encode({"iat": now, "exp": now + 600, "typ": "oauth_state"}, _JWT_SECRET, "HS256")


def check_oauth_state(state: str) -> bool:
    try:
        return jwt.decode(state, _JWT_SECRET, algorithms=["HS256"]).get("typ") == "oauth_state"
    except jwt.PyJWTError:
        return False


def is_safe_public_https_url(url: str) -> bool:
    """Block SSRF: only https, and only hosts that resolve exclusively to public IPs."""
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            return False
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
        return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)
    except Exception:
        return False
