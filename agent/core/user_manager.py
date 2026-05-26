"""
사용자 관리 — 회원가입, 로그인, JWT, 세션
"""
import json
import uuid
import hashlib
import hmac
import time
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
USER_FILE = DATA_DIR / "users.json"
SESSION_FILE = DATA_DIR / "sessions.json"

JWT_SECRET = hashlib.sha256(b"OPERA_AI_2026_LOCAL_SECRET").hexdigest()
JWT_EXPIRY_HOURS = 24


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
    payload_str = json.dumps(payload, sort_keys=True)
    sig = hmac.new(JWT_SECRET.encode(), payload_str.encode(), hashlib.sha256).hexdigest()
    return f"{payload_str}.{sig}"


def _verify_token(token):
    try:
        payload_str, sig = token.rsplit(".", 1)
        expected = hmac.new(JWT_SECRET.encode(), payload_str.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(payload_str)
        if payload["exp"] < time.time():
            return None
        return payload
    except Exception:
        return None


def register(email, password, username=""):
    """회원가입"""
    data = _load_users()
    if any(u["email"] == email for u in data["users"]):
        return {"error": "이미 등록된 이메일입니다"}
    if any(u["username"] == username for u in data["users"]):
        return {"error": "이미 사용 중인 사용자명입니다"}

    user_id = f"u_{uuid.uuid4().hex[:8]}"
    user = {
        "id": user_id,
        "email": email,
        "username": username or email.split("@")[0],
        "password_hash": _hash_password(password),
        "plan": "trial",
        "trial_start": datetime.now().isoformat(),
        "pc_count": 1,
        "created_at": datetime.now().isoformat(),
        "verified": True,
    }
    data["users"].append(user)
    _save_users(data)

    # 자동 라이선스 발급
    from core.payment import create_subscription
    lic = create_subscription("trial", "monthly", 1, user_id)

    token = _generate_token(user_id, "trial")
    return {"status": "registered", "user_id": user_id, "token": token, "license": lic.get("license_key", "")}


def login(email, password):
    """로그인"""
    data = _load_users()
    user = next((u for u in data["users"] if u["email"] == email), None)
    if not user:
        return {"error": "이메일 또는 비밀번호가 올바르지 않습니다"}
    if not _verify_password(password, user["password_hash"]):
        return {"error": "이메일 또는 비밀번호가 올바르지 않습니다"}

    token = _generate_token(user["id"], user["plan"])
    return {
        "status": "logged_in",
        "user_id": user["id"],
        "username": user["username"],
        "plan": user["plan"],
        "token": token,
    }


def get_user(user_id):
    """사용자 정보"""
    data = _load_users()
    user = next((u for u in data["users"] if u["id"] == user_id), None)
    if not user:
        return None
    return {
        "id": user["id"],
        "email": user["email"],
        "username": user["username"],
        "plan": user["plan"],
        "pc_count": user.get("pc_count", 1),
        "created_at": user["created_at"],
    }


def authenticate(request):
    """요청에서 사용자 인증"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    else:
        token = request.args.get("token", "")

    payload = _verify_token(token)
    if not payload:
        return None

    user = get_user(payload["user_id"])
    if not user:
        return None

    # plan 동기화
    from core.token_manager import _load_config, _save_config
    cfg = _load_config()
    cfg["plan"] = user["plan"]
    cfg["pc_count"] = user.get("pc_count", 1)
    _save_config(cfg)

    return user
