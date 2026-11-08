"""Tokenise structured personal identifiers before they reach audit logs (always) and, if a profile asks, the model.

Detected: email, Indian mobile numbers (+91), PAN, Aadhaar (Verhoeff-valid), payment cards (Luhn-valid).
Tokens are one-way keyed HMACs (`<EMAIL_1a2b3c4d5e6f>`), stable for a given HUB_TOKEN_KEY. Names and addresses are
not detected: use the 5.5 DPDP framework's detectors through the `on_result` hook for those.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from typing import Any

_KEY = (os.environ.get("HUB_TOKEN_KEY") or secrets.token_hex(32)).encode()

_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
      [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
      [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
      [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
      [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
      [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def _verhoeff(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def _luhn(num: str) -> bool:
    total, alt = 0, False
    for ch in reversed(num):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total, alt = total + d, not alt
    return total % 10 == 0


_PATTERNS = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), None),
    ("PAN", re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"), None),
    ("AADHAAR", re.compile(r"\b[2-9][0-9]{3}\s?[0-9]{4}\s?[0-9]{4}\b"), lambda s: _verhoeff(re.sub(r"\s", "", s))),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b"), lambda s: _luhn(re.sub(r"[ -]", "", s))),
    ("PHONE", re.compile(r"(?:\+91[\s-]?|\b0)?[6-9]\d{9}\b"), None),
]


def token_for(kind: str, value: str) -> str:
    return f"<{kind}_{hmac.new(_KEY, value.encode(), hashlib.sha256).hexdigest()[:12]}>"


def tokenize_text(text: str) -> str:
    for kind, rx, check in _PATTERNS:
        text = rx.sub(lambda m, k=kind, c=check: token_for(k, m.group(0)) if c is None or c(m.group(0)) else m.group(0), text)
    return text


def tokenize(obj: Any) -> Any:
    if isinstance(obj, str):
        return tokenize_text(obj)
    if isinstance(obj, dict):
        return {k: tokenize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [tokenize(v) for v in obj]
    return obj
