"""
악용 방지 — 3중 방어 체계 (Layer 1-3)
"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
STRIKE_FILE = DATA_DIR / "strikes.json"
RATE_FILE = DATA_DIR / "ratelimit.json"

# 블랙리스트 명령어
BLOCKED_COMMANDS = [
    "rm -rf /", "rm -rf /*", "format", "dd if=", "mkfs",
    "shutdown -r", "shutdown -h", "poweroff", "reboot",
    "chmod 777 /", "chown -R",
]

# 금지 패턴
BLOCKED_PATTERNS = [
    "해킹", "크래킹", "불법", "탈취", "침투", "서버 털어",
    "비밀번호 뚫어", "우회", "무단", "도용",
    "crack", "hack", "exploit", "ransomware", "malware",
    "강제 업그레이드", "플랜 우회", "라이선스 위조",
]

# 스트라이크 설정
STRIKE_CONFIG = {
    "warn_threshold": 3,    # 3회 경고 → 24h 중지
    "ban_threshold": 5,     # 5회 → 계정 정지
    "cooldown_hours": 24,   # 중지 시간
}


def _load_strikes():
    if STRIKE_FILE.exists():
        return json.load(open(STRIKE_FILE))
    return {"strikes": [], "blocked_until": None, "banned": False}


def _save_strikes(data):
    STRIKE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STRIKE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _load_rate():
    if RATE_FILE.exists():
        return json.load(open(RATE_FILE))
    return {"requests": []}


def _save_rate(data):
    with open(RATE_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ── Layer 1: 실행 차단 ──

def is_command_blocked(command):
    """차단된 명령어인지 확인"""
    if not command:
        return False
    cmd_lower = str(command).lower()
    for blocked in BLOCKED_COMMANDS:
        if blocked in cmd_lower:
            return True
    return False


def is_pattern_blocked(text):
    """금지 패턴 탐지"""
    if not text:
        return False
    text_lower = str(text).lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern in text_lower:
            return True
    return False


def is_path_blocked(path):
    """차단된 경로 확인"""
    import os
    BLOCKED_DIRS = [
        "/System", "/etc", "/usr", "/bin", "/sbin", "/boot",
        "C:\\Windows", "C:\\System32",
        "/proc", "/sys", "/dev",
    ]
    if not path:
        return False
    abs_path = os.path.abspath(str(path))
    for blocked in BLOCKED_DIRS:
        if abs_path.startswith(blocked):
            return True
    return False


# ── Layer 2: AI 판단 차단 / 경고 ──

def check_request_safety(prompt, action, params):
    """요청 안전성 검사"""
    violations = []

    # 블랙리스트 체크
    if is_command_blocked(prompt):
        violations.append("blocked_command")

    if is_pattern_blocked(prompt):
        violations.append("blocked_pattern")

    if is_path_blocked(params.get("path", "")):
        violations.append("blocked_path")

    # 강제 업그레이드 시도
    if any(w in str(prompt).lower() for w in ["플랜 변경", "업그레이드 강제", "라이선스 우회",
                                                "plan bypass", "force upgrade", "license hack"]):
        violations.append("forced_upgrade_attempt")

    # 정상
    if not violations:
        return {"safe": True}

    # 경고 기록
    if violations:
        data = _load_strikes()
        data["strikes"].append({
            "time": datetime.now().isoformat(),
            "violations": violations,
            "prompt": str(prompt)[:200],
        })
        _save_strikes(data)

    return {"safe": False, "violations": violations, "strikes": len(data.get("strikes", []))}


# ── Layer 3: 운영 차단 ──

def get_strike_count():
    """현재 스트라이크 수"""
    data = _load_strikes()
    return len(data.get("strikes", []))


def check_if_blocked():
    """계정 차단 상태 확인"""
    data = _load_strikes()

    if data.get("banned"):
        return {
            "blocked": True,
            "reason": "계정이 영구 정지되었습니다",
            "level": "permanent_ban",
        }

    blocked_until = data.get("blocked_until")
    if blocked_until:
        until = datetime.fromisoformat(blocked_until)
        if datetime.now() < until:
            remaining = (until - datetime.now()).seconds // 3600
            return {
                "blocked": True,
                "reason": f"일시 중지됨 (잔여 {remaining}시간)",
                "level": "temporary",
                "until": blocked_until,
            }
        else:
            data["blocked_until"] = None
            _save_strikes(data)

    return {"blocked": False}


def apply_strike(violations):
    """스트라이크 적용 + 차단 처리"""
    data = _load_strikes()
    total = len(data["strikes"])

    result = {"strike": total, "action": "warning", "message": f"경고 {total}회"}

    if total >= STRIKE_CONFIG["ban_threshold"]:
        # 5회 → 영구 정지
        data["banned"] = True
        _save_strikes(data)
        return {"strike": total, "action": "banned", "message": "악용 반복으로 계정이 영구 정지되었습니다"}

    if total >= STRIKE_CONFIG["warn_threshold"]:
        # 3회 → 24시간 중지
        until = (datetime.now() + timedelta(hours=STRIKE_CONFIG["cooldown_hours"])).isoformat()
        data["blocked_until"] = until
        _save_strikes(data)
        return {
            "strike": total,
            "action": "temporary_block",
            "message": f"경고 {total}회 누적으로 24시간 동안 사용이 중지됩니다",
            "until": until,
        }

    return result


# ── Rate Limit ──

def check_rate_limit(max_per_minute=10):
    """Rate Limit 확인"""
    data = _load_rate()
    now = time.time()
    cutoff = now - 60

    # 오래된 기록 정리
    data["requests"] = [t for t in data["requests"] if t > cutoff]

    if len(data["requests"]) >= max_per_minute:
        return {"allowed": False, "retry_after": 60 - (now - data["requests"][0])}

    data["requests"].append(now)
    _save_rate(data)
    return {"allowed": True, "remaining": max_per_minute - len(data["requests"])}


def get_security_status():
    """보안 상태 요약"""
    data = _load_strikes()
    return {
        "strikes": len(data.get("strikes", [])),
        "last_strike": data["strikes"][-1]["time"] if data.get("strikes") else None,
        "blocked": data.get("blocked_until") is not None or data.get("banned", False),
        "banned": data.get("banned", False),
        "warn_threshold": STRIKE_CONFIG["warn_threshold"],
        "ban_threshold": STRIKE_CONFIG["ban_threshold"],
    }
