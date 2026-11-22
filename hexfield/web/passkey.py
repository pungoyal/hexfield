"""Passkey (WebAuthn) checks, standard library only.

Just what signing in needs: ES256 (ECDSA on P-256 with SHA-256), which every passkey
provider supports, and no attestation. The browser hands over the public key as DER
(`getPublicKey()`), so there is no CBOR to read. Only signatures are checked here, never
made: every input is public, so plain arithmetic is fine (there is no secret to leak
through timing).
"""

import base64
import binascii
import hashlib
import json

P = 2**256 - 2**224 + 2**192 + 2**96 - 1
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
G = (
    0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
    0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5,
)
SPKI_PREFIX = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")  # P-256, uncompressed
ES256 = -7
UP, UV, AT = 0x01, 0x04, 0x40  # authenticator data flags: user present, verified, key included


class Invalid(ValueError):
    pass


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text):
    try:
        return base64.urlsafe_b64decode(str(text) + "=" * (-len(str(text)) % 4))
    except (binascii.Error, ValueError):
        raise Invalid("bad base64") from None


# ------------------------------------------------------------------ P-256
def _add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    (x1, y1), (x2, y2) = a, b
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None
        slope = (3 * x1 * x1 - 3) * pow(2 * y1, -1, P) % P
    else:
        slope = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (slope * slope - x1 - x2) % P
    return x3, (slope * (x1 - x3) - y1) % P


def _mul(k, point):
    out = None
    while k:
        if k & 1:
            out = _add(out, point)
        point = _add(point, point)
        k >>= 1
    return out


def public_key(spki):
    """(x, y) from a DER SubjectPublicKeyInfo holding a P-256 key."""
    if len(spki) != 91 or not spki.startswith(SPKI_PREFIX) or spki[26] != 4:
        raise Invalid("not a P-256 public key")
    x, y = int.from_bytes(spki[27:59]), int.from_bytes(spki[59:])
    if not (x < P and y < P and (y * y - x**3 + 3 * x - B) % P == 0):
        raise Invalid("the public key is not on the curve")
    return x, y


def _signature(der):
    """(r, s) from a DER ECDSA signature."""

    def integer(i):
        if i + 2 > len(der) or der[i] != 2 or not 1 <= der[i + 1] <= 33 or i + 2 + der[i + 1] > len(der):
            raise Invalid("bad signature")
        return int.from_bytes(der[i + 2 : i + 2 + der[i + 1]]), i + 2 + der[i + 1]

    if len(der) < 8 or der[0] != 0x30 or der[1] != len(der) - 2:
        raise Invalid("bad signature")
    r, i = integer(2)
    s, i = integer(i)
    if i != len(der):
        raise Invalid("bad signature")
    return r, s


def verify(key, message, der):
    """Is `der` an ES256 signature of `message` by the key (x, y)?"""
    r, s = _signature(der)
    if not (0 < r < N and 0 < s < N):
        return False
    e = int.from_bytes(hashlib.sha256(message).digest())
    w = pow(s, -1, N)
    point = _add(_mul(e * w % N, G), _mul(r * w % N, key))
    return point is not None and point[0] % N == r


# ------------------------------------------------------------------ WebAuthn
def _client_data(raw, kind, origin):
    """The challenge the browser signed, once the rest of clientDataJSON checks out."""
    try:
        d = json.loads(raw)
    except ValueError:
        raise Invalid("bad client data") from None
    if not isinstance(d, dict) or d.get("type") != kind:
        raise Invalid("wrong kind of passkey response")
    if d.get("origin") != origin or d.get("crossOrigin"):
        raise Invalid("the passkey answered for another site")
    if not isinstance(d.get("challenge"), str):
        raise Invalid("no challenge")
    return d["challenge"]


def _auth_data(raw, rp_id):
    """(flags, sign count) once the site and the user's presence check out."""
    if len(raw) < 37 or raw[:32] != hashlib.sha256(rp_id.encode()).digest():
        raise Invalid("the passkey belongs to another site")
    if not raw[32] & UP:
        raise Invalid("no one touched the passkey")
    return raw[32], int.from_bytes(raw[33:37])


def registration(client_data, auth_data, spki, alg, rp_id, origin):
    """Check a new passkey (from navigator.credentials.create).
    -> (challenge, credential id, (x, y), sign count)."""
    if alg != ES256:
        raise Invalid("this passkey uses an algorithm the server doesn't support")
    challenge = _client_data(client_data, "webauthn.create", origin)
    flags, count = _auth_data(auth_data, rp_id)
    if not flags & AT or len(auth_data) < 55:
        raise Invalid("the passkey sent no key")
    size = int.from_bytes(auth_data[53:55])
    cred = auth_data[55 : 55 + size]
    if not 1 <= size <= 1023 or len(cred) != size:
        raise Invalid("bad credential id")
    return challenge, cred, public_key(spki), count


def assertion(client_data, auth_data, signature, key, rp_id, origin):
    """Check a sign-in (from navigator.credentials.get) by the passkey with this key.
    -> (challenge, sign count)."""
    challenge = _client_data(client_data, "webauthn.get", origin)
    _, count = _auth_data(auth_data, rp_id)
    if not verify(key, auth_data + hashlib.sha256(client_data).digest(), signature):
        raise Invalid("the passkey's signature doesn't match")
    return challenge, count
