"""Ekbar chalao:  python generate_vapid_keys.py
Output-er 3 ta line Render -> Environment-e boshao. PRIVATE key kauke dekhaba na, git-e rakhba na."""
import base64
from py_vapid import Vapid
from cryptography.hazmat.primitives import serialization

v = Vapid()
v.generate_keys()
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
priv = b64(v.private_key.private_numbers().private_value.to_bytes(32, "big"))
pub = b64(v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
print("VAPID_PUBLIC_KEY=" + pub)
print("VAPID_PRIVATE_KEY=" + priv)
print("VAPID_SUBJECT=mailto:you@example.com   # nijer email dao")
