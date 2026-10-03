import os, hmac, hashlib, base64, json, time
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from app.db import Session, User

SECRET = os.getenv("SECRET_KEY", "dev-secret").encode()
oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/token")

def hash_pw(p, salt=None):
    salt = salt or os.urandom(8).hex()
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", p.encode(), salt.encode(), 100_000).hex()

def check_pw(p, stored):
    return hmac.compare_digest(hash_pw(p, stored.split("$")[0]), stored)

def make_token(user_id):
    body = base64.urlsafe_b64encode(json.dumps({"u": user_id, "e": int(time.time()) + 86400}).encode()).decode()
    return body + "." + hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()

def current_user(token: str = Depends(oauth2)):
    try:
        body, sig = token.split(".")
        assert hmac.compare_digest(sig, hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest())
        d = json.loads(base64.urlsafe_b64decode(body))
        assert d["e"] > time.time()
    except Exception:
        raise HTTPException(401, "Invalid or expired token")
    with Session() as s:
        u = s.get(User, d["u"])
    if not u:
        raise HTTPException(401, "User not found")
    return u
