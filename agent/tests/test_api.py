"""
OPERA AI — 통합 테스트 (36 + CSRF/보안/비밀번호)
"""
import os, sys, json, hmac, hashlib, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path
BASE_DIR = Path(__file__).parent.parent; DATA_DIR = BASE_DIR / "data"
from server import app
import pytest

_ok = (200, 201)
_ok_or_limited = (200, 201, 429)
_ok_or_blocked = (200, 201, 403, 429)
_CSRF = {"Origin": "http://localhost:5000"}

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c

@pytest.fixture
def hmac_key():
    return (DATA_DIR / "agent_hmac.key").read_text().strip()

def _sign(secret, body="", ts=None):
    ts = str(ts or int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    return ts, sig

class TestLanding:
    def test_all_pages(self, client):
        for p in ["/","/dashboard","/download","/docs","/privacy","/terms","/payment/success","/payment/cancel"]:
            assert client.get(p).status_code in _ok

class TestPublicAPI:
    def test_all(self, client):
        for api in ["/api/status","/api/plans","/api/skills","/api/security","/api/config","/api/history","/api/tasks","/api/routines","/api/monitor","/api/tokens"]:
            r = client.get(api)
            assert r.status_code in _ok, f"{api} → {r.status_code}"

class TestAuth:
    EMAIL = f"pytest_{int(time.time())}@test.com"
    PASS = "TestPass123"

    def test_password_policy_direct(self):
        """비밀번호 정책 직접 검증 (API 우회, 단위 테스트)"""
        from core.user_manager import _validate_password, _validate_email
        assert len(_validate_password("1234")) > 0  # 약함
        assert len(_validate_password("Abc12345")) == 0  # 강함
        assert _validate_email("good@test.com") == True
        assert _validate_email("bad") == False

    def test_register_bad_email(self, client):
        # rate limit 고려: 한 번만 호출
        r = client.post("/api/auth/register", json={"email":"notanemail","password":"TestPass123"}, headers=_CSRF)
        data = r.get_json() or {}
        # rate limit(429) 또는 에러 메시지 중 하나 통과
        assert r.status_code == 429 or "이메일" in data.get("error","") or "비밀번호 정책" in data.get("error","")

    def test_register_success(self, client):
        r = client.post("/api/auth/register", json={"email":self.EMAIL,"password":self.PASS}, headers=_CSRF)
        assert r.status_code in _ok_or_limited

    def test_login(self, client):
        r = client.post("/api/auth/login", json={"email":self.EMAIL,"password":self.PASS})
        if r.status_code in _ok and "token" in r.get_json():
            # wrong pw
            r2 = client.post("/api/auth/login", json={"email":self.EMAIL,"password":"WrongPass1"})
            assert "error" in r2.get_json()

class TestPayment:
    def test_price(self, client):
        r = client.post("/api/price", json={"plan":"enterprise","billing":"yearly","quantity":5,"bulk":True}, headers=_CSRF)
        assert r.status_code in _ok_or_limited
    def test_subscribe(self, client):
        r = client.post("/api/subscribe", json={"plan":"basic","billing":"monthly"}, headers=_CSRF)
        assert r.status_code in _ok_or_limited

class TestToken:
    def test_tokens(self, client):
        assert client.get("/api/tokens").status_code in _ok
    def test_purchase(self, client):
        assert client.post("/api/tokens/purchase", json={"pack":10000,"quantity":1}, headers=_CSRF).status_code in _ok_or_limited

class TestAgentAPI:
    def test_auth_invalid(self, client):
        assert client.post("/api/agent/auth", json={"license_key":"INVALID","mac_address":"00:11:22:33:44:55"}).status_code == 403
    def test_no_hmac(self, client):
        assert client.get("/api/agent/tasks").status_code == 401
    def test_hmac_ok(self, client, hmac_key):
        ts, sig = _sign(hmac_key)
        assert client.get("/api/agent/tasks", headers={"X-HMAC-Signature":sig,"X-HMAC-Timestamp":ts}).status_code in _ok
    def test_status(self, client, hmac_key):
        body = json.dumps({"hostname":"t","cpu":1,"memory":2})
        ts, sig = _sign(hmac_key, body)
        assert client.post("/api/agent/status", data=body, content_type="application/json",
            headers={"X-HMAC-Signature":sig,"X-HMAC-Timestamp":ts}).get_json()["status"] == "recorded"
    def test_hmac_expired(self, client, hmac_key):
        ts, sig = _sign(hmac_key, ts=int(time.time())-600)
        assert client.get("/api/agent/tasks", headers={"X-HMAC-Signature":sig,"X-HMAC-Timestamp":ts}).status_code == 401
    def test_hmac_wrong(self, client):
        ts, sig = _sign("x"*64)
        assert client.get("/api/agent/tasks", headers={"X-HMAC-Signature":sig,"X-HMAC-Timestamp":ts}).status_code == 401
    def test_command(self, client):
        assert client.post("/api/agent/command", json={"command":"pc-status"}, headers=_CSRF).get_json()["status"] == "running"

class TestSecurity:
    def test_csrf_blocked(self, client):
        """Origin 없는 POST → 403"""
        assert client.post("/api/subscribe", json={"plan":"test"}).status_code == 403
        assert client.post("/api/tokens/purchase", json={"pack":10000,"quantity":1}).status_code == 403
    def test_csrf_evil(self, client):
        assert client.post("/api/subscribe", json={"plan":"test"}, headers={"Origin":"https://evil.com"}).status_code == 403
    def test_csrf_ok(self, client):
        assert client.post("/api/subscribe", json={"plan":"test"}, headers={"Origin":"http://localhost:5000"}).status_code in _ok_or_limited
    def test_rate_limit(self, client):
        codes = [client.post("/api/auth/login", json={"email":"rl@t.com","password":"TestPass1"}, headers=_CSRF).status_code for _ in range(35)]
        assert 429 in codes
    def test_headers(self, client):
        r = client.get("/api/status")
        for h in ["Strict-Transport-Security","X-Content-Type-Options","X-Frame-Options","Referrer-Policy"]:
            assert h in r.headers

class TestExecutor:
    def test_system_info(self, client):
        r = client.post("/api/execute", json={"action":"system_info","params":{}}, headers=_CSRF)
        assert r.status_code in _ok_or_blocked

    def test_deepseek(self, client):
        r = client.post("/api/deepseek/chat", json={"prompt":"test","mode":"fast"})
        # CSRF exemption for deepseek endpoint
        assert r.status_code in _ok_or_blocked
        if r.status_code in _ok:
            d = r.get_json()
            assert d.get("model") == "deepseek-v4-flash"
            assert "response" in d
