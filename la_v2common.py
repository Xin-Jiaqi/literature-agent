#!/usr/bin/env python3
"""literature-agent V2 共享工具：HTTP(cache+rate limit+容错)、DOI/arXiv 归一化、JSON 缓存。"""
import hashlib
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import CACHE_ROOT
CACHE_DIR = CACHE_ROOT
MAILTO = "literature-agent@example.com"  # OpenAlex polite pool

_SSL_CTX = None


def ssl_ctx():
    global _SSL_CTX
    if _SSL_CTX is None:
        _SSL_CTX = ssl.create_default_context()
        try:
            _SSL_CTX.check_hostname = False
            _SSL_CTX.verify_mode = ssl.CERT_NONE
        except Exception:
            pass
    return _SSL_CTX


def http_get_json(url, timeout=30, params=None, headers=None, retries=2):
    """GET JSON with cache. Returns (ok, data_or_error_string)."""
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    key = hashlib.sha256(url.encode()).hexdigest()
    os.makedirs(CACHE_DIR, exist_ok=True)
    cpath = os.path.join(CACHE_DIR, "http_" + key + ".json")
    if os.environ.get("LITAGENT_NO_CACHE") == "1":
        cpath = os.devnull
    elif os.path.exists(cpath):
        try:
            with open(cpath) as f:
                return True, json.load(f)
        except Exception:
            pass
    hdrs = {"User-Agent": "literature-agent/2.0 (metadata discovery; mailto:%s)" % MAILTO}
    hdrs.update(headers or {})
    last_err = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx()) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            with open(cpath, "w") as f:
                json.dump(data, f)
            return True, data
        except Exception as e:
            last_err = str(e)[:300]
            time.sleep(0.8 * (i + 1))
    return False, last_err


class RateLimiter:
    def __init__(self, min_gap=1.0):
        self.min_gap = min_gap
        self._last = [0.0]

    def wait(self):
        gap = time.time() - self._last[0]
        if gap < self.min_gap:
            time.sleep(self.min_gap - gap)
        self._last[0] = time.time()


def norm_doi(doi):
    if not doi:
        return ""
    d = str(doi).strip().lower()
    m = re.search(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", d, re.I)
    return m.group(0).rstrip(".,;") if m else ""


def norm_arxiv(s):
    if not s:
        return ""
    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", str(s))
    return m.group(0) if m else ""


def doi_eq(a, b):
    a, b = norm_doi(a), norm_doi(b)
    return bool(a and b and a == b)


def clean_title(t):
    if not t:
        return ""
    t = re.sub(r"<[^>]+>", "", str(t))
    return re.sub(r"\s+", " ", t).strip()


def write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def now_iso():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def today_iso():
    return time.strftime("%Y-%m-%d")


def compact(s, n=400):
    return clean_title(s)[:n]
