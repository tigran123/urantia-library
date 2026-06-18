import bcrypt
import jwt
import uuid
from datetime import datetime, timedelta, timezone
import os

SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "super-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 14 # 14 days

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(
            plain_password.encode('utf-8'),
            hashed_password.encode('utf-8')
        )
    except Exception:
        return False

def get_password_hash(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def create_access_token(data: dict, jti: str | None = None) -> tuple[str, str, datetime]:
    """Issue a JWT and return (encoded_token, jti, expires_at).

    The `jti` claim is the key the in-memory session map uses to gate
    revocation: a missing jti — or a jti that's no longer in the map —
    means the token is treated as terminated regardless of signature/exp.

    Pass an existing `jti` to re-issue ("slide") a session in place: the session
    identity stays stable (same Sessions-panel row, created_at, last_seen) and
    only `exp` moves forward. Omit it at login to mint a fresh session."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    if jti is None:
        jti = str(uuid.uuid4())
    to_encode.update({"exp": expire, "jti": jti})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt, jti, expire

