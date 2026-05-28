# User management -- register, login, JWT, session
"""User management module."""
import json
import uuid
import hashlib
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
import jwt

DATA_DIR = Path(__file__).parent.parent / "data"
USER_FILE = DATA_DIR / "users.json"
SESSION_FILE = DATA_DIR / "sessions.json"

JWT_EXPIRY_HOURS = 24
_JWT_SECRET = None


def _get_jwt_secret():
    """JWT_SECRET lazy load — must be called after .env loading"""
    global _JWT_SECRET
    if _JWT_SECRET is None:
        _JWT_SECRET = os.getenv("JWT_SECRET", "") or hashlib.sha256(os.urandom(64)).hexdigest()
    return _JWT_SECRET


def _load_users():
    if USER_FILE.exists():
        return json.load(open(USER_FILE))
    return {"users": []}


def _save_users(data):
    USER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(USER_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _load_sessions():
    if SESSION_FILE.exists():
        return json.load(open(SESSION_FILE))
    return {"sessions": []}


def _save_sessions(data):
    with open(SESSION_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _hash_password(password, salt=None):
    if not salt:
        salt = uuid.uuid4().hex[:16]
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex()
    return f"{salt}${hashed}"


def _verify_password(password, stored):
    salt, hashed = stored.split("$")
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex() == hashed


def _generate_token(user_id, plan="trial"):
    payload = {
        "user_id": user_id,
        "plan": plan,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXPIRY_HOURS * 3600,
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm="HS256")


def _verify_token(token):
    try:
        return jwt.decode(token, _get_jwt_secret(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def _validate_password(password):
    """Password policy validation"""
    errors = []
    if len(password) < 8:
        errors.append("at least 8 characters")
    if len(password) > 128:
        errors.append("at most 128 characters")
    if not any(c.isupper() for c in password):
        errors.append("at least one uppercase letter")
    if not any(c.islower() for c in password):
        errors.append("at least one lowercase letter")
    if not any(c.isdigit() for c in password):
        errors.append("at least one number")
    return errors


def _validate_email(email):
    """Email format validation"""
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def register(email, password, username=""):
    """Register a new user"""
    # Input validation
    if not email or not password:
        return {"error": "Email and password are required."}
    if not _validate_email(email):
        return {"error": "Invalid email format."}
    pw_errors = _validate_password(password)
    if pw_errors:
        return {"error": "Password policy: " + ", ".join(pw_errors)}
    
    data = _load_users()
    if any(u["email"] == email for u in data["users"]):
        return {"error": "An account with this email already exists."}
    if any(u["username"] == username for u in data["users"]):
        return {"error": "Username already taken."}

    now = datetime.now()
    user_id = f"u_{uuid.uuid4().hex[:8]}"
    user = {
        "id": user_id,
        "email": email,
        "username": username or email.split("@")[0],
        "password_hash": _hash_password(password),
        "plan": "trial",
        "role": "user",
        "trial_start": now.isoformat(),
        "trial_end": (now + timedelta(days=3)).isoformat(),
        "pc_count": 1,
        "created_at": now.isoformat(),
        "verified": True,
    }
    data["users"].append(user)
    _save_users(data)

    # Auto license generation
    from core.payment import create_subscription
    lic = create_subscription("trial", "monthly", 1, user_id)

    token = _generate_token(user_id, "trial")
    return {"status": "registered", "user_id": user_id, "token": token, "license": lic.get("license_key", "")}


def login(email, password):
    """Log in"""
    data = _load_users()
    user = next((u for u in data["users"] if u["email"] == email), None)
    if not user:
        return {"error": "Invalid email or password."}
    if not _verify_password(password, user["password_hash"]):
        return {"error": "Invalid email or password."}

    token = _generate_token(user["id"], user["plan"])
    return {
        "ok": True,
        "status": "logged_in",
        "user_id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "plan": user["plan"],
        "role": user.get("role", "user"),
        "token": token,
    }


def get_user(user_id):
    """Get user info"""
    data = _load_users()
    user = next((u for u in data["users"] if u["id"] == user_id), None)
    if not user:
        return None
    return {
        "id": user["id"],
        "email": user["email"],
        "username": user["username"],
        "plan": user["plan"],
        "role": user.get("role", "user"),
        "pc_count": user.get("pc_count", 1),
        "verified": user.get("verified", False),
        "trial_start": user.get("trial_start", ""),
        "trial_end": user.get("trial_end", ""),
        "created_at": user["created_at"],
    }


def authenticate(request):
    """Authenticate user from request"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    else:
        token = request.args.get("token", "")

    payload = _verify_token(token)
    if not payload:
        return None

    # Session expiry check (logged out token)
    sessions = _load_sessions()
    if token in [s.get("token") for s in sessions.get("blacklist", [])]:
        return None

    user = get_user(payload["user_id"])
    if not user:
        return None

    # Plan sync
    from core.token_manager import _load_config, _save_config
    cfg = _load_config()
    cfg["plan"] = user["plan"]
    cfg["pc_count"] = user.get("pc_count", 1)
    _save_config(cfg)

    return user


def logout(token):
    """Logout (token blacklist)"""
    sessions = _load_sessions()
    if "blacklist" not in sessions:
        sessions["blacklist"] = []
    sessions["blacklist"].append({
        "token": token,
        "invalidated_at": datetime.now().isoformat(),
    })
    # Clean up old entries if over 100
    if len(sessions["blacklist"]) > 100:
        sessions["blacklist"] = sessions["blacklist"][-100:]
    _save_sessions(sessions)
    return {"status": "logged_out"}


def force_logout_all(user_id):
    """Force logout all sessions for a user"""
    data = _load_users()
    user = next((u for u in data["users"] if u["id"] == user_id), None)
    if not user:
        return {"error": "user_not_found"}

    # Password hash change (invalidates all existing tokens)
    user["password_hash"] = _hash_password(
        user["password_hash"].split("$")[0],
        uuid.uuid4().hex[:16]
    )
    _save_users(data)

    # Special tag in session blacklist
    sessions = _load_sessions()
    if "force_logouts" not in sessions:
        sessions["force_logouts"] = []
    sessions["force_logouts"].append({
        "user_id": user_id,
        "forced_at": datetime.now().isoformat(),
    })
    _save_sessions(sessions)

    return {"status": "force_logged_out", "user_id": user_id}
