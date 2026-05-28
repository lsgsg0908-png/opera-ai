#!/usr/bin/env python3
"""
OPERA AI — Main Server
실행: python3 server.py
"""
import os, sys, json, datetime, hashlib, hmac, threading, time, re
import secrets as _secrets
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from core.token_manager import (
    PLANS, TOPUP_PACKS, check_token_available, consume_tokens,
    add_purchased_tokens, get_token_status, get_actual_cost, estimate_tokens,
    get_daily_allowance
)
from core.executor import (
    mouse_move, mouse_click, mouse_double_click, mouse_scroll, get_mouse_position,
    keyboard_type, keyboard_press, keyboard_hotkey,
    screenshot, screen_size, locate_on_screen,
    window_list, window_activate, window_minimize, get_active_window,
    file_read, file_write, file_list, file_delete, file_copy, file_search,
    process_list, process_run, process_kill,
    system_info, clipboard_get, clipboard_set, ocr_image,
    PERMISSION_SAFE, PERMISSION_ADVANCED, PERMISSION_DANGEROUS, PERMISSION_SYSTEM,
    _get_permission_level, _get_function_by_action,
    detect_gpu, get_gpu_install_message,
)
from core.skill_manager import get_registry
from core.task_queue import get_queue, MAX_CONCURRENT
from core.scheduler import get_scheduler
from core.payment import (
    get_plans, calculate_price, create_subscription,
    get_subscription, cancel_subscription, get_bulk_discount_tiers
)
from core.user_manager import register, login, authenticate, get_user, logout, force_logout_all
from core.tg_bot import get_bot, COMMANDS
from core.paypal import (
    create_order as paypal_create_order,
    capture_order as paypal_capture_order,
    verify_webhook as paypal_verify_webhook,
    configure as paypal_configure,
    get_config_status as paypal_status,
)
from core.security import (
    check_request_safety, check_if_blocked, apply_strike,
    check_rate_limit, get_security_status, is_path_blocked
)

app = Flask(__name__)
CORS(app)

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# ── 설정 ──
CONFIG_PATH = DATA_DIR / "config.json"
LICENSE_PATH = DATA_DIR / "license.json"
HISTORY_DIR = DATA_DIR / "history"
HISTORY_DIR.mkdir(exist_ok=True)

# ── 보안 미들웨어 ──

# CSRF: POST 요청은 반드시 Origin/Referer 검증
_CSRF_EXEMPT = {"/api/deepseek/chat", "/api/paypal/webhook", "/api/devices"}

@app.before_request
def csrf_check():
    """CSRF 보호 — POST 요청 Origin/Referer 검증 (webhook 제외)"""
    if request.method != "POST":
        return None
    if request.path in _CSRF_EXEMPT or request.path.startswith("/api/agent/") or request.path.startswith("/api/devices/"):
        return None
    if request.path.startswith("/api/auth/"):
        return None  # auth는 Origin 다양함
    
    origin = request.headers.get("Origin", "")
    referer = request.headers.get("Referer", "")
    
    # 둘 다 없으면 차단
    if not origin and not referer:
        return jsonify({"error": "CSRF: Origin/Referer required"}), 403
    
    # 허용된 출처 목록
    for h in [origin, referer]:
        if h:
            host = h.split("//")[-1].split("/")[0]
            if not any(a in host for a in ["localhost", "127.0.0.1", "opera.workbotai.net",
                                             "lsgsg0908-png.github.io", "opera-ai.net", "operaai.net"]):
                return jsonify({"error": "CSRF: Invalid origin"}), 403
    return None


# 제한된 경로 (인증 필요)
PROTECTED_PATHS = [
    "/api/tokens/purchase", "/api/subscribe", "/api/me", "/api/config",
    "/api/skills/select", "/api/execute", "/api/agent",
]

# 속도 제한 설정 (경로별 분/최대 요청)
RATE_LIMITS = {
    "default": 60,         # 일반 요청: 60/분
    "/api/execute": 15,    # 실행 요청: 15/분
    "/api/auth/login": 10, # 로그인 시도: 10/분
    "/api/auth/register": 5, # 회원가입: 5/분
}

# 전역 Rate Limit 저장소
_rate_store = {}


@app.before_request
def input_sanitize():
    """입력값 검증 — JSON 요청 필드 sanitize"""
    if request.content_type == "application/json":
        try:
            data = request.get_json(silent=True) or {}
            for key, val in list(data.items()):
                if isinstance(val, str):
                    # null byte 제거
                    data[key] = val.replace("\x00", "")
                    # 스크립트 태그 strip (XSS 방어)
                    data[key] = data[key][:5000]  # 최대 길이
            request._cached_json = (data, data)
        except:
            pass
    return None


@app.before_request
def global_rate_limit():
    """전역 Rate Limit 미들웨어"""
    path = request.path
    if path.startswith("/static") or path == "/":
        return None
    
    # 경로별 제한 확인
    limit = RATE_LIMITS.get("default", 60)
    for p, l in RATE_LIMITS.items():
        if path.startswith(p):
            limit = l
            break
    
    # IP 기반 레이트 리밋
    ip = request.remote_addr or "unknown"
    key = f"{ip}:{path.split('/')[1]}"
    now = time.time()
    
    if key not in _rate_store:
        _rate_store[key] = []
    
    # 1분 이내 요청만 유지
    _rate_store[key] = [t for t in _rate_store[key] if now - t < 60]
    
    if len(_rate_store[key]) >= limit:
        return jsonify({
            "error": "rate_limit_exceeded",
            "message": f"request_too_fast_max_{limit}_per_minute",
            "retry_after": 60 - int(now - _rate_store[key][0])
        }), 429
    
    _rate_store[key].append(now)
    return None


@app.after_request
def add_security_headers(response):
    """보안 응답 헤더 추가"""
    # HSTS (HTTP Strict Transport Security)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # XSS 방지
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    # Referrer Policy
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # Content Security Policy (API 전용)
    if request.path.startswith("/api/"):
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'none'; connect-src 'self'"
    return response

# ── 스킬 레지스트리 ──
skill_registry = get_registry()


def _load_config():
    if CONFIG_PATH.exists():
        return json.load(open(CONFIG_PATH))
    return {"plan": "trial", "pc_count": 1, "trial_start": str(datetime.date.today()),
            "skills": [], "mode": "deep"}


def _save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def _check_license():
    """라이선스 검증 (서버 검증)"""
    if LICENSE_PATH.exists():
        lic = json.load(open(LICENSE_PATH))
        # TODO: 실제 서버 검증 로직
        return lic.get("valid", True)
    return True


def _log_history(user_id, action, detail, tokens_used=0, permission_level=None):
    """작업 히스토리 저장 (타임라인용)"""
    entry = {
        "time": datetime.datetime.now().isoformat(),
        "user": user_id,
        "action": action,
        "detail": str(detail)[:200],
        "tokens": tokens_used,
        "permission": permission_level
    }
    today = str(datetime.date.today())
    log_file = HISTORY_DIR / f"{today}.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    # 10000 라인 초과 시 오래된 항목 제거
    _rotate_history_if_needed(log_file)


def _rotate_history_if_needed(log_file):
    """로그 파일 10000라인 초과 시 오래된 절반 제거"""
    if log_file.exists():
        lines = log_file.read_text().splitlines()
        if len(lines) > 10000:
            log_file.write_text("\n".join(lines[-5000:]) + "\n")


@app.route("/api/history/timeline", methods=["GET"])
def api_history_timeline():
    """실행 타임라인 조회 (최근 100건)"""
    days = request.args.get("days", 1, type=int)
    limit = request.args.get("limit", 100, type=int)
    result = []
    for i in range(days):
        d = (datetime.date.today() - datetime.timedelta(days=i)).isoformat()
        f = HISTORY_DIR / f"{d}.jsonl"
        if f.exists():
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            result.append(json.loads(line))
                        except:
                            pass
    result.sort(key=lambda x: x.get("time", ""), reverse=True)
    return jsonify({"events": result[:limit], "total": len(result), "history_days": days})


@app.route("/api/health", methods=["GET"])
def api_health():
    return jsonify({"ok": True, "status": "healthy", "timestamp": datetime.datetime.now().isoformat()})

# ── API: 사용자 ──

@app.route("/api/auth/register", methods=["POST"])
def api_register():
    import time as _time
    data = request.get_json() or {}
    email_addr = data.get("email", "").strip().lower()

    # 이메일 인증 확인
    verified_expires = _verified_emails.get(email_addr)
    if not verified_expires or _time.time() > verified_expires:
        if email_addr in _verified_emails:
            _verified_emails.pop(email_addr, None)
        return jsonify({"ok": False, "error": "Email verification required."}), 403

    # 인증 완료 — 사용 가능, 정리 후 가입 진행
    _verified_emails.pop(email_addr, None)

    result = register(
        email=data.get("email", ""),
        password=data.get("password", ""),
        username=data.get("username", "")
    )

    # register가 dict 반환 가정, 오류 형식 통일
    if isinstance(result, dict):
        if "error" in result:
            result["ok"] = False
        else:
            result["ok"] = True

    return jsonify(result)


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    result = login(email=data.get("email", ""), password=data.get("password", ""))
    return jsonify(result)


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    """로그아웃 (토큰 무효화)"""
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "")
    if token:
        result = logout(token)
        return jsonify(result)
    return jsonify({"error": "no_token"}), 400


    result = force_logout_all(target_user_id)
    return jsonify(result)


@app.route("/api/me", methods=["GET"])
def api_me():
    user = authenticate(request)
    if not user:
        return jsonify({"ok": False, "error": "authentication_required"}), 401
    return jsonify({"ok": True, "user": user})

# ── 이메일 인증 저장소 ──
# {email: {hash, expires_at, failures, cooldown_until}}
_verify_store = {}
# 인증 완료된 이메일 (10분 유효)
_verified_emails = {}  # {email: expires_at}

@app.route("/api/auth/send-verification", methods=["POST"])
def api_send_verification():
    import hashlib, time as _time, random as _random, smtplib, email.mime.text
    data = request.get_json() or {}
    email_addr = data.get("email", "").strip().lower()
    if not email_addr or "@" not in email_addr:
        return jsonify({"ok": False, "error": "Valid email required."}), 400

    now = _time.time()
    existing = _verify_store.get(email_addr)

    # 60초 재요청 제한
    if existing and existing.get("cooldown_until", 0) > now:
        remaining = int(existing["cooldown_until"] - now)
        return jsonify({
            "ok": False,
            "error": f"Please wait {remaining} seconds before requesting again."
        }), 429

    # 6자리 코드 생성
    code = str(_random.randint(100000, 999999))
    expires_at = now + 600  # 10분
    code_hash = hashlib.sha256(code.encode()).hexdigest()

    _verify_store[email_addr] = {
        "hash": code_hash,
        "expires_at": expires_at,
        "failures": 0,
        "cooldown_until": now + 60
    }

    # HTML 이메일 발송
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    smtp_from = os.environ.get("SMTP_FROM", "OPERA AI <noreply@opera-ai.net>")

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:system-ui,-apple-system,sans-serif;background:#11100e;padding:32px">
<div style="max-width:480px;margin:0 auto;background:#1a1815;border-radius:20px;padding:36px;border:1px solid #2a2824">
<div style="width:40px;height:40px;border-radius:10px;background:#4ade80;display:grid;place-items:center;font-size:20px;font-weight:900;color:#11100e;margin-bottom:16px">O</div>
<h2 style="margin:0 0 8px;font-size:22px;color:#f0ece4;font-weight:800">Verify your email</h2>
<p style="color:#b5ab9d;line-height:1.6;margin:0 0 24px;font-size:14px">Enter this code to complete your OPERA AI registration. It expires in 10 minutes.</p>
<div style="background:#22201c;border-radius:14px;padding:24px;text-align:center;border:1px solid #2a2824">
<div style="font-size:42px;letter-spacing:12px;font-weight:900;color:#4ade80;font-family:monospace">{code}</div>
</div>
<p style="font-size:12px;color:#7a7162;margin-top:20px;text-align:center">If you did not request this, you can safely ignore this email.</p>
</div>
</body>
</html>"""

    sent = False
    try:
        msg = email.mime.multipart.MIMEMultipart("alternative")
        msg["Subject"] = "Your OPERA AI verification code"
        msg["From"] = smtp_from
        msg["To"] = email_addr
        msg.attach(email.mime.text.MIMEText(f"Your verification code: {code}\n\nExpires in 10 minutes.", "plain"))
        msg.attach(email.mime.text.MIMEText(html, "html"))
        with smtplib.SMTP(smtp_host, int(os.environ.get("SMTP_PORT", 587))) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.send_message(msg)
        sent = True
        print(f"[verify] Code sent to {email_addr}")
    except Exception as ex:
        print(f"[verify] SMTP FAILED for {email_addr}: {ex}")

    if sent:
        return jsonify({"ok": True, "message": "Verification code sent."})
    else:
        return jsonify({"ok": False, "error": "Email send failed. Please try again later."}), 500


@app.route("/api/auth/verify-code", methods=["POST"])
def api_verify_code():
    import hashlib, time as _time
    data = request.get_json() or {}
    email_addr = data.get("email", "").strip().lower()
    code = data.get("code", "").strip()
    if not email_addr or not code:
        return jsonify({"ok": False, "error": "Email and code required"}), 400

    stored = _verify_store.get(email_addr)
    if not stored:
        return jsonify({"ok": False, "error": "No verification code found. Request a new one."}), 400

    now = _time.time()
    if now > stored["expires_at"]:
        _verify_store.pop(email_addr, None)
        return jsonify({"ok": False, "error": "Verification code expired. Request a new one."}), 400

    # 실패 횟수 제한 (최대 5회)
    if stored["failures"] >= 5:
        _verify_store.pop(email_addr, None)
        return jsonify({"ok": False, "error": "Too many failed attempts. Request a new code."}), 429

    input_hash = hashlib.sha256(code.encode()).hexdigest()
    if stored["hash"] != input_hash:
        stored["failures"] += 1
        remaining = 5 - stored["failures"]
        return jsonify({"ok": False, "error": f"Invalid code. {remaining} attempts remaining."}), 400

    # 성공 — 인증 완료 등록 (10분 유효)
    _verify_store.pop(email_addr, None)
    _verified_emails[email_addr] = now + 600
    return jsonify({"ok": True, "message": "Email verified."})

    return jsonify({"user": user})


# ── API: 결제 ──

@app.route("/api/plans", methods=["GET"])
def api_plans():
    return jsonify({"plans": get_plans(), "bulk": get_bulk_discount_tiers()})


@app.route("/api/price", methods=["POST"])
def api_price():
    data = request.get_json() or {}
    result = calculate_price(
        plan_id=data.get("plan", "basic"),
        billing=data.get("billing", "monthly"),
        quantity=data.get("quantity", 1),
        bulk=data.get("bulk", False)
    )
    return jsonify(result or {"error": "Invalid plan"})


@app.route("/api/subscribe", methods=["POST"])
def api_subscribe():
    data = request.get_json() or {}
    result = create_subscription(
        plan_id=data.get("plan", "basic"),
        billing=data.get("billing", "monthly"),
        quantity=data.get("quantity", 1),
        user_id=data.get("user_id", "local")
    )
    return jsonify(result)


@app.route("/api/subscription", methods=["GET"])
def api_subscription():
    sub = get_subscription()
    return jsonify(sub or {"status": "no_active_subscription"})


@app.route("/api/subscription/cancel", methods=["POST"])
def api_cancel():
    data = request.get_json() or {}
    return jsonify(cancel_subscription(data.get("id", "")))


# ── API: PayPal ──

@app.route("/api/paypal/config", methods=["GET", "POST"])
def api_paypal_config():
    """PayPal 설정 조회/변경"""
    if request.method == "POST":
        data = request.get_json() or {}
        return jsonify(paypal_configure(
            mode=data.get("mode"),
            client_id=data.get("client_id"),
            secret=data.get("secret"),
            webhook_id=data.get("webhook_id"),
        ))
    return jsonify(paypal_status())


@app.route("/api/paypal/create-order", methods=["POST"])
def api_paypal_create_order():
    """PayPal 주문 생성 → 결제 URL 반환"""
    data = request.get_json() or {}
    result = paypal_create_order(
        plan_id=data.get("plan", "pro"),
        billing=data.get("billing", "monthly"),
        quantity=data.get("quantity", 1),
        bulk=data.get("bulk", False),
        return_url=data.get("return_url"),
        cancel_url=data.get("cancel_url"),
    )
    return jsonify(result)


@app.route("/api/paypal/capture-order", methods=["POST"])
def api_paypal_capture():
    """PayPal 결제 캡처 (사용자 승인 후)"""
    data = request.get_json() or {}
    order_id = data.get("paypal_order_id", "")
    if not order_id:
        token = request.args.get("token", "")  # PayPal redirects with ?token=XXX
        if token:
            order_id = token
    
    result = paypal_capture_order(order_id)
    
    # 캡처 성공 시 구독 생성
    if result.get("status") == "completed":
        plan_id = data.get("plan", "pro")
        billing = data.get("billing", "monthly")
        quantity = data.get("quantity", 1)
        sub = create_subscription(plan_id, billing, quantity)
        result["subscription"] = sub
    
    return jsonify(result)


@app.route("/api/paypal/webhook", methods=["POST"])
def api_paypal_webhook():
    """PayPal 웹훅 수신 (결제 완료/취소/환불 통지)"""
    body = request.get_data(as_text=True)
    verified, event_type = paypal_verify_webhook(dict(request.headers), body)
    
    if not verified:
        return jsonify({"error": "webhook verification failed"}), 403
    
    event = request.get_json(silent=True) or {}
    resource = event.get("resource", {})
    
    if event_type == "CHECKOUT.ORDER.APPROVED":
        # 자동 캡처
        order_id = resource.get("id", "")
        if order_id:
            capture = paypal_capture_order(order_id)
            if capture.get("status") == "completed":
                plan_id = "pro"
                sub = create_subscription(plan_id, "monthly", 1)
                capture["subscription"] = sub
    
    return jsonify({"status": "received", "event_type": event_type})


# ── API: 텔레그램 ──

@app.route("/api/tg/config", methods=["GET", "POST"])
def api_tg_config():
    if request.method == "POST":
        data = request.get_json() or {}
        return jsonify(get_bot().configure(data.get("bot_token", ""), data.get("chat_id", "")))
    from core.tg_bot import _load_config
    cfg = _load_config()
    return jsonify({"enabled": cfg.get("enabled", False), "commands": [
        {"cmd": k, "desc": v} for k, v in COMMANDS.items()
    ]})


@app.route("/api/tg/send", methods=["POST"])
def api_tg_send():
    data = request.get_json() or {}
    return jsonify(get_bot().send_message(data.get("text", "")))


@app.route("/api/tg/start", methods=["POST"])
def api_tg_start():
    return jsonify(get_bot().start_polling())


# ── API: 보안 ──

@app.route("/api/security", methods=["GET"])
def api_security():
    return jsonify(get_security_status())


# ── API: 상태 ──

@app.route("/api/status", methods=["GET"])
def api_status():
    """서버 상태"""
    config = _load_config()
    tokens = get_token_status()
    return jsonify({
        "status": "running",
        "version": "0.1.0",
        "plan": config["plan"],
        "mode": config.get("mode", "fast"),
        "tokens": tokens,
        "skills": config.get("skills", []),
        "pc_count": config.get("pc_count", 1),
    })


# ── API: 실행 ──

@app.route("/api/execute", methods=["POST"])
def api_execute():
    """명령 실행 (보안 적용)"""
    config = _load_config()
    data = request.get_json() or {}

    # ── Layer 1: 라이선스 체크 ──
    if not _check_license():
        return jsonify({"error": "license_verification_failed"})

    # ── Layer 2: 계정 차단 체크 ──
    blocked = check_if_blocked()
    if blocked.get("blocked"):
        return jsonify({"error": blocked["reason"], "security": blocked})

    # ── Layer 2: 요청 안전 검사 ──
    action = data.get("action", "")
    params = data.get("params", {})
    prompt = data.get("prompt", "") or json.dumps(data)

    safety = check_request_safety(prompt, action, params)
    if not safety.get("safe"):
        strike_result = apply_strike(safety.get("violations", []))
        _log_history("security_violation", str(safety["violations"]), strike_result, 0)
        return jsonify({
            "error": safety.get("violations", []),
            "message": "security_violation_detected",
            "strike": strike_result
        })

    # ── Layer 2: Rate Limit ──
    rate = check_rate_limit(10)
    if not rate.get("allowed"):
        return jsonify({"error": "request_too_fast_try_again_later", "retry_after": rate.get("retry_after", 0)})

    # ── Layer 1: 경로 차단 ──
    check_path = params.get("path", "")
    if check_path and is_path_blocked(check_path):
        _log_history("blocked_path", check_path, {}, 0)
        return jsonify({"error": "access_to_path_is_blocked"})

    # 토큰 예상 소비량
    est_tokens = estimate_tokens(len(json.dumps(data)), params.get("complexity", "normal"))

    # 토큰 체크
    available, source, remaining = check_token_available(est_tokens)
    if not available:
        return jsonify({
            "error": "daily_token_limit_exceeded",
            "token_status": get_token_status(),
            "suggestion": "purchase_additional_tokens_or_try_tomorrow"
        })

    # ── Layer 3: 권한 레벨 확인 + 승인 ──
    permission = _get_permission_level(action)
    preview = data.get("preview", False)
    confirmed = data.get("confirmed", False)

    if preview:
        return jsonify({
            "preview": True,
            "action": action,
            "params": params,
            "permission_level": permission,
            "expected_tokens": est_tokens,
            "requires_confirmation": permission in (PERMISSION_DANGEROUS, PERMISSION_SYSTEM),
            "message": _get_preview_message(action, permission),
        })

    # DANGEROUS/SYSTEM: confirmed 필수
    if permission in (PERMISSION_DANGEROUS, PERMISSION_SYSTEM) and not confirmed:
        return jsonify({
            "error": f"{permission} 등급 작업은 confirmed=true가 필요합니다",
            "permission_level": permission,
            "action": action,
            "hint": "preview=true로 먼저 예상 영향을 확인하세요",
        })

    # 실행
    result = _route_action(action, params)
    result["tokens_used"] = est_tokens
    result["permission_level"] = permission

    # 토큰 소비
    consume_tokens(est_tokens)
    _log_history(config.get("user_id", "local"), action, result, est_tokens)

    return jsonify(result)


def _get_preview_message(action, permission):
    """preview 모드에서 실행 전 예상 영향 메시지"""
    messages = {
        "process_run": "[DANGEROUS] 명령어를 실행합니다. 시스템이 변경될 수 있습니다.",
        "shutdown_pc": "[DANGEROUS] PC를 종료합니다. 저장하지 않은 작업이 손실될 수 있습니다.",
        "file_delete": "[ADVANCED] 파일을 삭제합니다. 복구가 불가능할 수 있습니다.",
        "file_write": "[ADVANCED] 파일을 생성/수정합니다.",
        "wake_on_lan": "[ADVANCED] 네트워크를 통해 PC를 켭니다.",
        "clipboard_set": "[ADVANCED] 클립보드 내용을 변경합니다.",
        "process_kill": "[ADVANCED] 프로세스를 강제 종료합니다.",
        "window_activate": "[ADVANCED] 창을 활성화합니다.",
        "window_minimize": "[ADVANCED] 창을 최소화합니다.",
    }
    return messages.get(action, f"[{permission}] {action} 작업을 실행합니다.")


def _route_action(action, params):
    """액션 라우팅"""
    actions = {
        # 마우스
        "mouse_move": lambda: mouse_move(params.get("x", 0), params.get("y", 0), params.get("duration", 0.2)),
        "mouse_click": lambda: mouse_click(params.get("x"), params.get("y"), params.get("button", "left")),
        "mouse_double_click": lambda: mouse_double_click(params.get("x"), params.get("y")),
        "mouse_scroll": lambda: mouse_scroll(params.get("clicks", 0)),
        "mouse_position": get_mouse_position,
        # 키보드
        "keyboard_type": lambda: keyboard_type(params.get("text", ""), params.get("interval", 0.01)),
        "keyboard_press": lambda: keyboard_press(params.get("key", "")),
        "keyboard_hotkey": lambda: keyboard_hotkey(*params.get("keys", [])),
        # 화면
        "screenshot": lambda: screenshot(params.get("file"), params.get("region")),
        "screen_size": screen_size,
        # 윈도우
        "window_list": window_list,
        "window_activate": lambda: window_activate(params.get("title", "")),
        "window_minimize": lambda: window_minimize(params.get("title", "")),
        "active_window": get_active_window,
        # 파일
        "file_read": lambda: file_read(params.get("path", "")),
        "file_write": lambda: file_write(params.get("path", ""), params.get("content", "")),
        "file_list": lambda: file_list(params.get("path", ".")),
        "file_delete": lambda: file_delete(params.get("path", "")),
        "file_search": lambda: file_search(params.get("query", ""), params.get("root", "~")),
        # 프로세스
        "process_list": process_list,
        "process_run": lambda: process_run(params.get("command", ""), params.get("timeout", 30)),
        # 시스템
        "system_info": system_info,
        # 클립보드
        "clipboard_get": clipboard_get,
        "clipboard_set": lambda: clipboard_set(params.get("text", "")),
        # OCR
        "ocr": lambda: ocr_image(params.get("image_path", ""), params.get("lang", "kor+eng")),
        # 스킬
        "skill_execute": lambda: skill_registry.execute(
            params.get("skill_id", ""), **params.get("skill_params", {})
        ),
        "skill_list": skill_registry.get_all_skills,
        # AI 처리
        "ai_chat": lambda: _ai_process(params.get("prompt", ""), params.get("mode", "fast")),
    }

    handler = actions.get(action)
    if not handler:
        return {"error": f"알 수 없는 액션: {action}"}

    # 권한 레벨 확인
    permission = _get_permission_level(action)

    try:
        result = handler()
        if isinstance(result, dict):
            result["permission_level"] = permission
        return result
    except Exception as e:
        return {"error": str(e), "permission_level": permission}


# ── AI 처리 ──

# ── DeepSeek API (서버 전용, 키 보호) ──
import os as _os
_ENV = {}
_env_path = BASE_DIR / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().strip().split("\n"):
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            _k = _k.strip()
            _v = _v.strip()
            _ENV[_k] = _v
            os.environ[_k] = _v  # subprocess 등에서 접근 가능하도록

# Production: JWT_SECRET 없으면 시작 실패
if not os.environ.get("JWT_SECRET"):
    print("FATAL: JWT_SECRET is not set in .env")
    print("Generate: openssl rand -hex 32")
    sys.exit(1)

# Runtime Integrity Check: 룰 파일 checksum
_RULES_CHECKSUMS = {}
_RULES_DIR = Path(__file__).parent.parent.parent / ".openclaw" / "workspace"
for _fname in ["SOUL.md", "REPORTING.md", "RULES_EXECUTION_VERIFY.md"]:
    _fpath = _RULES_DIR / _fname
    if _fpath.exists():
        _RULES_CHECKSUMS[_fname] = hashlib.md5(_fpath.read_bytes()).hexdigest()
        print(f"  [INTEGRITY] {_fname}: {_RULES_CHECKSUMS[_fname][:12]}...")
    else:
        print(f"  [INTEGRITY] {_fname}: NOT FOUND (non-critical)")

_DEEPSEEK_KEY = _ENV.get("DEEPSEEK_API_KEY", "")
_DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
_DS_MODELS = {"fast": "deepseek-v4-flash", "deep": "deepseek-v4-flash", "expert": "deepseek-v4-flash"}
_DS_MAX_TOKENS = {"fast": 1024, "deep": 4096, "expert": 8192}
_DS_TEMPS = {"fast": 0.3, "deep": 0.1, "expert": 0.05}

# ── THINKING 모드 (토큰 비용 최적화) ──
# Obujang 설계 반영: NONE/LOW/MEDIUM/HIGH/CRITICAL
_THINKING_MODES = {
    "none":     {"max_tokens": 256,  "temperature": 0.5,  "desc": "기본 확인/상태 체크/인사 — 최소 토큰"},
    "low":      {"max_tokens": 1024, "temperature": 0.3,  "desc": "가벼운 작업 (짧은 문서/빠른 응답)"},
    "medium":   {"max_tokens": 4096, "temperature": 0.1,  "desc": "일반 작업 (중간 문서/분석)"},
    "high":     {"max_tokens": 8192, "temperature": 0.05, "desc": "복잡한 작업 (코드/분석/전략)"},
    "critical": {"max_tokens": 16384,"temperature": 0.01, "desc": "고난이도 작업 (대규모 분석/설계)"},
}
# backward compatibility: fast→low, deep→medium, expert→high
_THINKING_MODE_ALIAS = {"fast": "low", "deep": "medium", "expert": "high"}


def _resolve_thinking_mode(mode):
    """THINKING 모드 이름을 실제 설정으로 변환"""
    mode = mode.lower()
    # alias 매핑
    mode = _THINKING_MODE_ALIAS.get(mode, mode)
    config = _THINKING_MODES.get(mode)
    if config:
        return config["max_tokens"], config["temperature"]
    # fallback: medium
    return _THINKING_MODES["medium"]["max_tokens"], _THINKING_MODES["medium"]["temperature"]


def _list_thinking_modes():
    """사용 가능한 THINKING 모드 목록 (API용)"""
    return [{"id": k, "max_tokens": v["max_tokens"], "temperature": v["temperature"], "desc": v["desc"]}
            for k, v in _THINKING_MODES.items()]


@app.route("/api/thinking/modes", methods=["GET"])
def api_thinking_modes():
    """THINKING 모드 목록 조회"""
    return jsonify({"modes": _list_thinking_modes(), "default": "medium"})

# -- GPU detection API --
@app.route("/api/system/gpu", methods=["GET"])
def api_gpu_detect():
    """Detect local PC GPU"""
    gpu = detect_gpu()
    message = get_gpu_install_message(gpu)
    return jsonify({
        "gpu": gpu,
        "install_message": message,
        "consent_required": gpu.get("grade") not in ("none",),
    })


# -- User consent management --
CONSENT_FILE = Path(__file__).parent / "data" / "consent.json"


@app.route("/api/system/consent", methods=["GET", "POST"])
def api_consent():
    """User consent management"""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        consent_type = data.get("type", "general")
        agreed = data.get("agreed", False)
        consents = {}
        if CONSENT_FILE.exists():
            consents = json.loads(CONSENT_FILE.read_text())
        if agreed:
            consents[consent_type] = {
                "agreed": True,
                "agreed_at": datetime.datetime.now().isoformat(),
                "version": "1.0"
            }
        else:
            consents[consent_type] = {"agreed": False}
        CONSENT_FILE.write_text(json.dumps(consents, indent=2, ensure_ascii=False))
        return jsonify({"status": "saved", "consent": consents.get(consent_type)})
    
    consents = {}
    if CONSENT_FILE.exists():
        consents = json.loads(CONSENT_FILE.read_text())
    
    disclaimer = (
        "OPERA AI executes tasks directly on your PC.\n\n"
        "1. We ask for consent before modifying or deleting important files.\n"
        "2. You are solely responsible for any data loss or system changes\n"
        "   resulting from misuse or lack of understanding.\n"
        "3. DANGEROUS-level actions always require additional confirmation.\n"
        "4. Automatic backups are created but do not guarantee recovery in all cases.\n\n"
        "Do you agree to these terms?"
    )
    
    return jsonify({
        "consents": consents,
        "disclaimer": disclaimer,
        "requires_consent": not consents.get("general", {}).get("agreed", False),
    })



_DS_SYSTEM = (
    "You are OPERA AI (Riche), an execution-type AI assistant. "
    "Your tasks: analyze user commands, execute actions, and provide verified results. "
    "Rules: RESPOND IN ENGLISH. No speculation. No unverified claims. "
    "Every report MUST include a status tag: [CONFIRMED], [OBSERVED], [INFERRED], [UNVERIFIED], or [FAILED]. "
    "See RULES_EXECUTION_VERIFY.md for full verification rules."
)


def _ds_chat(messages, mode="fast"):
    """DeepSeek API 호출 (재시도 + graceful degradation 적용)"""
    import requests as _req
    import time as _t
    
    max_retries = 2
    retry_delay = 1.0  # 초
    last_err = None
    
    for attempt in range(max_retries + 1):
        try:
            # THINKING 모드로 max_tokens/temperature 결정
            _mt, _tmp = _resolve_thinking_mode(mode)
            resp = _req.post(_DEEPSEEK_URL, json={
                "model": "deepseek-v4-flash",
                "messages": messages,
                "max_tokens": _mt,
                "temperature": _tmp,
                "stream": False,
            }, headers={
                "Authorization": f"Bearer {_DEEPSEEK_KEY}",
                "Content-Type": "application/json",
            }, timeout=30)
            if resp.ok:
                data = resp.json()
                usage = data.get("usage", {})
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return content, usage.get("total_tokens", 0), None
            # 5xx 에러만 재시도
            if resp.status_code >= 500 and attempt < max_retries:
                last_err = f"DeepSeek {resp.status_code}: retrying..."
                _t.sleep(retry_delay * (attempt + 1))
                continue
            return None, 0, f"DeepSeek {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            last_err = str(e)
            if attempt < max_retries:
                _t.sleep(retry_delay * (attempt + 1))
                continue
            return None, 0, f"ai_service_unavailable_after_{max_retries}_retries: {last_err}"


@app.route("/api/deepseek/chat", methods=["POST"])
def api_deepseek_proxy():
    """DeepSeek API 프록시 — 클라이언트가 호출 (API 키 서버에만 보관)"""
    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt", "")
    mode = data.get("mode", "fast")
    messages = data.get("messages")
    
    if not prompt and not messages:
        return jsonify({"error": "prompt required"}), 400
    
    # 사용량 체크 (인증된 사용자)
    user = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            from core.user_manager import authenticate
            user = authenticate(request)
        except:
            pass
    
    if not messages:
        messages = [
            {"role": "system", "content": _DS_SYSTEM},
            {"role": "user", "content": prompt},
        ]
    
    content, tokens, err = _ds_chat(messages, mode)
    if err:
        _log_history("ds_proxy_error", err[:100], {}, 0)
        return jsonify({"error": err}), 502
    
    # 토큰 추적 (공통 함수)
    cost = _deduct_and_log(prompt, tokens, len(content), "ds_proxy")
    
    # 작업 명령 파싱
    commands = _parse_ai_commands(content, prompt)
    
    # 응답 검증 미들웨어 (Runtime 레벨)
    validated_content, warnings, blocked = _validate_agent_response(content, commands)
    
    if blocked:
        return jsonify({
            "status": "blocked",
            "response": validated_content,
            "reason": "validation_failed",
            "validation_warnings": warnings,
        }), 422
    
    return jsonify({
        "status": "ok",
        "response": validated_content,
        "commands": commands,
        "tokens_used": tokens,
        "actual_cost": cost,
        "model": "deepseek-v4-flash",
        "validation_warnings": warnings if warnings else None,
    })


def _ai_process(prompt, mode="fast"):
    """로컬 AI 명령 처리 (서버 자체용, 프록시 재사용)"""
    messages = [
        {"role": "system", "content": _DS_SYSTEM},
        {"role": "user", "content": prompt},
    ]
    content, tokens, err = _ds_chat(messages, mode)
    if err:
        _log_history("ai_error", err[:100], {}, 0)
        return {"status": "error", "error": f"ai_error_{err}"}
    
    cost = _deduct_and_log(prompt, tokens, len(content), "ai_chat")
    commands = _parse_ai_commands(content, prompt)
    
    # 응답 검증 미들웨어 (Runtime 레벨)
    validated_content, warnings, blocked = _validate_agent_response(content, commands)
    if blocked:
        validated_content = {
            "error": "blocked_by_validation",
            "reason": warnings,
        }
    if warnings:
        _log_history("validation_warning", str(warnings), {}, 0)
    
    return {
        "status": "ok",
        "response": content,
        "commands": commands,
        "tokens_used": tokens,
        "actual_cost": cost,
        "model": "deepseek-v4-flash",
        "mode": mode,
    }


def _parse_ai_commands(ai_response, original_prompt):
    """AI 응답에서 실행 명령 추출 (최적화: 정규식 최소화)"""
    import re
    commands = []
    # 형식: [ACTION:액션명:파라미터]
    pattern = r'\[ACTION:([a-z_]+):([^\]]+)\]'
    for match in re.finditer(pattern, ai_response):
        action = match.group(1)
        params_str = match.group(2)
        try:
            import json
            params = json.loads(params_str)
        except:
            params = {"text": params_str}
        commands.append({"action": action, "params": params})
    return commands


# ── 검증 시스템: 추정 표현 패턴 ──
_SPECULATIVE_EN = r'\b(probably|maybe|likely|seems|appears|might|could|possibly|presumably|arguably)\b'
_SPECULATIVE_KO = r'\b(추정|가능성|예상|아마|약|쯤|대략|거의|대충|~)\b'
_SPECULATIVE_PATTERNS = [_SPECULATIVE_EN, _SPECULATIVE_KO]


def _validate_agent_response(content, commands):
    """
    AI 응답 검증 미들웨어 (Runtime 레벨 강제)
    - 상태 태그 존재 확인
    - 추정 표현 탐지 + [CONFIRMED] 충돌 시 BLOCKED
    - 증거/명령어 실행 결과 검증
    - 검증 실패 시 REPORT BLOCKED
    
    Returns: (validated_content, status, warnings, blocked)
    """
    import re
    warnings = []
    blocked = False
    
    # 1. 상태 태그 존재 확인
    valid_tags = ["[CONFIRMED]", "[OBSERVED]", "[INFERRED]", "[UNVERIFIED]", "[FAILED]"]
    has_tag = any(tag in content for tag in valid_tags)
    if not has_tag:
        content = f"[UNVERIFIED] (auto-downgraded: missing status tag)\n{content}"
        warnings.append("missing_status_tag")
    
    # 2. [CONFIRMED] 상태인데 추정 표현이 있는지 확인
    if "[CONFIRMED]" in content:
        has_speculative = False
        for pattern in _SPECULATIVE_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                has_speculative = True
                break
        if has_speculative:
            # [CONFIRMED] + 추정표현 = INVALID REPORT → BLOCKED
            warnings.append("speculative_in_confirmed_blocked")
            blocked = True
            # 차단 메시지로 대체
            content = (
                "[FAILED] REPORT BLOCKED: [CONFIRMED] status contains speculative language.\n"
                "The model claimed confirmed results while using uncertain language.\n"
                "Re-run with proper verification."
            )
    
    # 3. [OBSERVED] 상태인데 추정 표현이 있는지 확인
    if "[OBSERVED]" in content:
        for pattern in _SPECULATIVE_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                content = content.replace("[OBSERVED]", "[UNVERIFIED]")
                warnings.append("speculative_in_observed_downgraded")
                break
    
    # 4. 명령어 실행 결과 검증 (명령어가 있고 실제 실행 결과가 없는 경우)
    if commands and "error" not in content.lower():
        has_evidence = any(kw in content.lower() for kw in ["result:", "output:", "response:", "✅", "✓"])
        if not has_evidence:
            content += "\n[UNVERIFIED] (auto-downgraded: commands without evidence)"
            warnings.append("commands_without_evidence")
    
    # 5. 실제 증거 검증 (evidence keywords in response body)
    if "[CONFIRMED]" in content:
        has_real_evidence = any(kw in content.lower() for kw in [
            "stdout:", "stderr:", "returncode:", "file:", "response:",
            "✅", "✓", "status: ok", "passed", "verified"
        ])
        if not has_real_evidence:
            warnings.append("confirmed_without_evidence")
    
    return content, warnings, blocked


def _check_speculative_language(text):
    """추정 표현 탐지 (정실장 시스템용)"""
    import re
    patterns = [
        (_SPECULATIVE_EN, 'en'),
        (_SPECULATIVE_KO, 'ko'),
    ]
    findings = []
    for pattern, lang in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            findings.append({"word": match.group(), "lang": lang, "position": match.start()})
    return findings


# ── API: 토큰 ──
@app.route("/api/tokens", methods=["GET"])
def api_tokens():
    return jsonify(get_token_status())


@app.route("/api/tokens/purchase", methods=["POST"])
def api_purchase_tokens():
    data = request.get_json() or {}
    pack_id = data.get("pack_id")
    if pack_id is not None and 0 <= pack_id < len(TOPUP_PACKS):
        pack = TOPUP_PACKS[pack_id]
        remaining = add_purchased_tokens(pack["tokens"])
        _log_history("purchase", f"purchased_{pack['tokens']}_tokens", {}, 0)
        return jsonify({"status": "ok", "tokens_added": pack["tokens"], "total_purchased": remaining})
    return jsonify({"error": "invalid_token_pack"})


# ── API: 설정 ──

@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        return jsonify(_load_config())
    data = request.get_json() or {}
    cfg = _load_config()
    for key in ["plan", "skills", "mode", "pc_count"]:
        if key in data:
            cfg[key] = data[key]
    _save_config(cfg)
    return jsonify({"status": "ok", "config": cfg})


# ── API: 스킬 ──

@app.route("/api/skills", methods=["GET"])
def api_skills():
    limit = None
    cfg = _load_config()
    plan = cfg.get("plan", "trial")
    plan_cfg = PLANS.get(plan, PLANS["trial"])
    limit = plan_cfg["skill_limit"]

    all_skills = skill_registry.get_all_skills()
    if limit and limit < 999:
        return jsonify({"skills": all_skills, "limit": limit, "selected": cfg.get("skills", [])})
    return jsonify({"skills": all_skills, "selected": cfg.get("skills", [])})


@app.route("/api/skills/select", methods=["POST"])
def api_select_skills():
    data = request.get_json() or {}
    selected = data.get("skill_ids", [])
    cfg = _load_config()
    plan = cfg.get("plan", "trial")
    plan_cfg = PLANS.get(plan, PLANS["trial"])
    limit = plan_cfg["skill_limit"]

    if limit and limit < 999 and len(selected) > limit:
        return jsonify({"error": f"{plan} 플랜은 최대 {limit}개 스킬만 선택 가능합니다"})

    cfg["skills"] = selected
    _save_config(cfg)
    return jsonify({"status": "ok", "skills": selected})


# ── API: 작업 큐 ──

@app.route("/api/tasks", methods=["GET"])
def api_tasks():
    queue = get_queue()
    return jsonify(queue.get_status())


@app.route("/api/tasks/enqueue", methods=["POST"])
def api_enqueue():
    data = request.get_json() or {}
    queue = get_queue()
    result = queue.enqueue(
        action=data.get("action", ""),
        params=data.get("params", {}),
        priority=data.get("priority", "normal"),
        description=data.get("description", "")
    )
    # 토큰 소비
    est = estimate_tokens(len(json.dumps(data)))
    consume_tokens(est)
    result["tokens_used"] = est
    return jsonify(result)


@app.route("/api/tasks/history", methods=["GET"])
def api_task_history():
    queue = get_queue()
    return jsonify({"tasks": queue.get_history(50)})


# ── API: 루틴 ──

@app.route("/api/routines", methods=["GET"])
def api_routines():
    sched = get_scheduler()
    config = _load_config()
    return jsonify({
        "routines": sched.get_routines(),
        "limit": sched.get_limit(config.get("plan", "trial"))
    })


@app.route("/api/routines/add", methods=["POST"])
def api_add_routine():
    data = request.get_json() or {}
    config = _load_config()
    sched = get_scheduler()
    result = sched.add_routine(
        name=data.get("name", "루틴"),
        steps=data.get("steps", []),
        interval_minutes=data.get("interval_minutes", 60),
        plan=config.get("plan", "trial")
    )
    return jsonify(result)


@app.route("/api/routines/remove", methods=["POST"])
def api_remove_routine():
    data = request.get_json() or {}
    sched = get_scheduler()
    return jsonify(sched.remove_routine(data.get("id", "")))


# ── 토큰 처리 공통 함수 (중복 제거) ──

def _deduct_and_log(prompt, tokens, response_len, source="ai_chat"):
    """토큰 차감 + 히스토리 기록 (단일 진입점)"""
    from core.token_manager import consume_tokens, get_actual_cost
    cost = get_actual_cost(tokens)
    consume_tokens(tokens)
    _log_history(source, str(prompt)[:100], {
        "response_len": response_len,
        "tokens": tokens,
        "cost": cost,
    }, tokens)
    return cost


# ── API: 히스토리 ──

@app.route("/api/history", methods=["GET"])
def api_history():
    days = request.args.get("days", 7, type=int)
    entries = []
    for i in range(days):
        d = (datetime.date.today() - datetime.timedelta(days=i))
        log_file = HISTORY_DIR / f"{d}.jsonl"
        if log_file.exists():
            with open(log_file) as f:
                for line in f:
                    if line.strip():
                        entries.append(json.loads(line))
    return jsonify({"entries": entries[:200]})


# ── Agent API (HMAC 검증) ──

AGENT_HMAC_KEY = DATA_DIR / "agent_hmac.key"
if not AGENT_HMAC_KEY.exists():
    AGENT_HMAC_KEY.write_text(hashlib.sha256(os.urandom(64)).hexdigest())

def _verify_hmac(request):
    """HMAC 서명 검증"""
    try:
        secret = AGENT_HMAC_KEY.read_text().strip()
        signature = request.headers.get("X-HMAC-Signature", "")
        timestamp = request.headers.get("X-HMAC-Timestamp", "")
        body = request.get_data(as_text=True) or ""
        msg = f"{timestamp}:{body}"
        expected = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
        # 시간 검증 (5분 이내)
        now = int(datetime.datetime.utcnow().timestamp())
        if abs(now - int(timestamp)) > 300:
            return None, "HMAC timestamp expired"
        if not hmac.compare_digest(signature, expected):
            return None, "HMAC signature mismatch"
        return True, None
    except Exception as e:
        return None, str(e)


def _agent_auth_required(f):
    """Agent API 인증 데코레이터"""
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        ok, err = _verify_hmac(request)
        if not ok:
            return jsonify({"error": "auth_failed", "detail": err}), 401
        return f(*args, **kwargs)
    return wrapper


@app.route("/api/agent/auth", methods=["POST"])
def agent_auth():
    """에이전트 인증 - 라이선스 키 검증"""
    data = request.get_json(silent=True) or {}
    license_key = data.get("license_key", "")
    mac_addr = data.get("mac_address", "")
    hw_id = data.get("hardware_id", "")
    
    # 라이선스 검증
    lic = DATA_DIR / "license.json"
    if not lic.exists():
        return jsonify({"error": "no_license"}), 403
    
    license_data = json.loads(lic.read_text())
    if license_data.get("key") != license_key or not license_data.get("valid", False):
        return jsonify({"error": "invalid_license"}), 403
    
    # 만료 확인
    expires = license_data.get("expires_at")
    if expires and datetime.datetime.fromisoformat(expires) < datetime.datetime.now():
        return jsonify({"error": "license_expired"}), 403
    
    # HMAC 시크릿 교환 (임시 토큰 발급)
    import uuid
    session_token = uuid.uuid4().hex
    agent_sessions = DATA_DIR / "agent_sessions.json"
    sessions = {}
    if agent_sessions.exists():
        sessions = json.loads(agent_sessions.read_text())
    sessions[session_token] = {
        "license_key": license_key,
        "mac": mac_addr,
        "hardware_id": hw_id,
        "authenticated_at": datetime.datetime.utcnow().isoformat(),
        "plan": license_data.get("plan", "trial"),
    }
    agent_sessions.write_text(json.dumps(sessions, indent=2))
    
    return jsonify({
        "status": "authenticated",
        "session_token": session_token,
        "plan": license_data.get("plan", "trial"),
        "expires_at": license_data.get("expires_at"),
    })


@app.route("/api/agent/tasks", methods=["GET"])
@_agent_auth_required
def agent_tasks():
    """에이전트 작업 큐 조회 (Polling)"""
    status_filter = request.args.get("status", "pending")
    tasks = []
    queue_file = DATA_DIR / "tasks.json"
    if queue_file.exists():
        try:
            data = json.loads(queue_file.read_text())
            all_items = data.get("tasks", []) + data.get("queue", []) + data.get("running", [])
            tasks = [t for t in all_items if isinstance(t, dict) and t.get("status") == status_filter]
        except Exception:
            pass
    return jsonify({
        "tasks": tasks[:20],
        "count": len(tasks),
        "plan": _load_config().get("plan", "trial"),
    })


@app.route("/api/agent/result", methods=["POST"])
@_agent_auth_required
def agent_result():
    """작업 결과 제출"""
    data = request.get_json(silent=True) or {}
    task_id = data.get("task_id", "")
    result = data.get("result", {})
    status = data.get("status", "completed")
    tokens_used = data.get("tokens_used", 0)
    
    queue_file = DATA_DIR / "tasks.json"
    if queue_file.exists():
        try:
            all_data = json.loads(queue_file.read_text())
            for key in ["tasks", "queue", "running"]:
                for t in all_data.get(key, []):
                    if isinstance(t, dict) and t.get("id") == task_id:
                        t["status"] = status
                        t["result"] = result
                        t["completed_at"] = datetime.datetime.utcnow().isoformat()
                        break
            queue_file.write_text(json.dumps(all_data, indent=2))
        except Exception:
            pass
    
    # 토큰 소비
    if tokens_used > 0:
        consume_tokens(tokens_used)
    
    return jsonify({"status": "received", "task_id": task_id})


@app.route("/api/agent/status", methods=["POST"])
@_agent_auth_required
def agent_status_report():
    """에이전트 상태 보고"""
    data = request.get_json(silent=True) or {}
    report = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "hostname": data.get("hostname", "unknown"),
        "cpu": data.get("cpu", 0),
        "memory": data.get("memory", 0),
        "uptime": data.get("uptime", 0),
        "version": data.get("version", "unknown"),
        "connected_pc": data.get("connected_pc", False),
    }
    
    # 상태 저장
    status_file = DATA_DIR / "agent_status.json"
    status_file.write_text(json.dumps(report, indent=2))
    
    return jsonify({"status": "recorded", "plan": _load_config().get("plan", "trial")})


@app.route("/api/agent/command", methods=["POST"])
def agent_command():
    """긴급 명령 발송 (WOL, 종료)"""
    data = request.get_json(silent=True) or {}
    command = data.get("command", "")
    params = data.get("params", {})
    
    if command == "wol":
        mac = params.get("mac_address", "")
        if not mac:
            return jsonify({"error": "MAC address required"}), 400
        from core.executor import wake_on_lan
        result = wake_on_lan(mac)
        return jsonify(result)
    elif command == "shutdown":
        delay = params.get("delay", 0)
        from core.executor import shutdown_pc
        result = shutdown_pc(delay)
        return jsonify(result)
    elif command == "pc-status":
        from core.executor import pc_status
        result = pc_status()
        return jsonify(result)
    else:
        return jsonify({"error": f"Unknown command: {command}"}), 400


@app.route("/api/agent/tokens", methods=["GET"])
def agent_token_status():
    """토큰 잔여량 조회"""
    status = get_token_status()
    return jsonify(status)


@app.route("/api/agent/hmac_key", methods=["POST"])
def agent_generate_hmac():
    """HMAC 키 재생성 (인증된 사용자만)"""
    auth = request.headers.get("Authorization", "")
    user = authenticate(auth.replace("Bearer ", ""))
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    
    new_key = hashlib.sha256(os.urandom(64)).hexdigest()
    AGENT_HMAC_KEY.write_text(new_key)
    return jsonify({"status": "ok", "hmac_key": new_key})


@app.route("/api/agent/sessions", methods=["GET"])
def agent_active_sessions():
    """활성 에이전트 세션 목록"""
    auth = request.headers.get("Authorization", "")
    user = authenticate(auth.replace("Bearer ", ""))
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    
    sess_file = DATA_DIR / "agent_sessions.json"
    sessions = {}
    if sess_file.exists():
        sessions = json.loads(sess_file.read_text())
    return jsonify({"sessions": sessions, "count": len(sessions)})


# ── 통계/모니터링 API ──

@app.route("/api/monitor", methods=["GET"])
def api_monitor():
    """서비스 통계 대시보드"""
    # 기본 정보 (비인증 가능)
    
    # 사용자 수
    users_file = DATA_DIR / "users.json"
    user_count = 0
    if users_file.exists():
        users = json.loads(users_file.read_text())
        if isinstance(users, dict):
            user_count = len(users.get("users", users))
        elif isinstance(users, list):
            user_count = len(users)
    
    # 구독 통계
    sub_file = DATA_DIR / "subscriptions.json"
    active_subs = 0
    total_revenue = 0
    if sub_file.exists():
        subs = json.loads(sub_file.read_text())
        for s in subs.get("subscriptions", []):
            if s.get("status") == "active":
                active_subs += 1
                total_revenue += s.get("price", 0)
    
    # 작업 통계
    tasks_file = DATA_DIR / "tasks.json"
    completed_tasks = 0
    pending_tasks = 0
    if tasks_file.exists():
        try:
            tdata = json.loads(tasks_file.read_text())
            for key in ["tasks", "queue", "running"]:
                for t in tdata.get(key, []):
                    s = t.get("status", "")
                    if s == "completed":
                        completed_tasks += 1
                    elif s == "pending":
                        pending_tasks += 1
        except Exception:
            pass
    
    # 토큰 통계
    token_file = DATA_DIR / "tokens.json"
    total_tokens = 0
    if token_file.exists():
        try:
            tdata = json.loads(token_file.read_text())
            total_tokens = tdata.get("purchased_pool", 0) + tdata.get("daily_pool", 0)
        except Exception:
            pass
    
    return jsonify({
        "users": user_count,
        "active_subscriptions": active_subs,
        "estimated_monthly_revenue": total_revenue,
        "tasks_completed": completed_tasks,
        "tasks_pending": pending_tasks,
        "total_tokens_remaining": total_tokens,
        "agent_status_file": str(DATA_DIR / "agent_status.json"),
        "agent_connected": agent_sessions_file_exists(),
        "uptime": int(process_uptime()),
    })


def agent_sessions_file_exists():
    return (DATA_DIR / "agent_sessions.json").exists()


def process_uptime():
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])



# ── 웹 대시보드 (랜딩페이지) ──

@app.route("/")
def index():
    return send_from_directory(str(BASE_DIR.parent), "index.html")


@app.route("/<path:path>")
def static_files(path):
    # Try pages/ dir first (with .html extension if missing)
    candidates = [
        BASE_DIR.parent / "pages" / path,
        BASE_DIR.parent / "pages" / (path + ".html"),
        BASE_DIR.parent / path,
        BASE_DIR.parent / (path + ".html"),
    ]
    for cp in candidates:
        if cp.exists() and cp.is_file():
            parent = cp.parent
            fname = cp.name
            return send_from_directory(str(parent), fname)
    return send_from_directory(str(BASE_DIR.parent), path)


# ── Devices (Workstation 관리) ──
DEVICES_FILE = DATA_DIR / "devices.json"

def _load_devices():
    if DEVICES_FILE.exists():
        return json.loads(DEVICES_FILE.read_text())
    return {"devices": []}

def _save_devices(data):
    DEVICES_FILE.write_text(json.dumps(data, indent=2))

# Plan별 최대 workstation 수
MAX_WORKSTATIONS = {
    "basic": 1, "trial": 1,
    "pro": 3, "pro_plus": 5,
    "enterprise": 10, "company_pro": 999
}

def get_plan_key(plan):
    mapping = {"basic":"basic","trial":"basic","pro":"pro","pro_plus":"pro_plus","enterprise":"enterprise","company_pro":"company_pro"}
    return mapping.get(plan, "basic")

@app.route("/api/devices", methods=["GET"])
def api_get_devices():
    user = authenticate(request)
    if not user:
        return jsonify({"ok": False, "error": "authentication_required"}), 401
    data = _load_devices()
    user_devices = [d for d in data["devices"] if d["user_id"] == user["id"]]

    # Mark inactive devices (90 days no contact)
    import datetime as _dt
    now = _dt.datetime.now()
    for d in user_devices:
        if d.get("active", True):
            last = d.get("last_seen", d.get("created_at", ""))
            if last:
                try:
                    last_dt = _dt.datetime.fromisoformat(last)
                    if (now - last_dt).days >= 90:
                        d["active"] = False
                except:
                    pass
    _save_devices(data)

    return jsonify({"ok": True, "devices": user_devices, "max_workstations": MAX_WORKSTATIONS.get(get_plan_key(user["plan"]), 1)})


# ── Capability Refresh API ──

@app.route("/api/agent/capabilities", methods=["GET"])
def api_agent_capabilities():
    """Return full capability object for the current agent session"""
    # Try agent session auth first
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else ""
    plan = "trial"

    # Check agent session
    sessions_file = DATA_DIR / "agent_sessions.json"
    if sessions_file.exists():
        sessions = json.loads(sessions_file.read_text())
        if token in sessions:
            plan = sessions[token].get("plan", "trial")
        else:
            # Fallback to user auth
            user = authenticate(request)
            if user:
                plan = user.get("plan", "trial")
    else:
        user = authenticate(request)
        if user:
            plan = user.get("plan", "trial")
        if not user:
            return jsonify({"ok": False, "error": "authentication_required"}), 401

    plan_key = get_plan_key(plan)
    plan_cfg = PLANS.get(plan, PLANS.get("trial", {}))
    max_ws = MAX_WORKSTATIONS.get(plan_key, 1)
    max_conc = MAX_CONCURRENT.get(plan, 1)

    capabilities = {
        "plan": plan,
        "gpu_enabled": plan_key in ("pro", "pro_plus", "enterprise", "company_pro"),
        "max_concurrent_tasks": max_conc,
        "background_execution": plan_key in ("pro_plus", "enterprise", "company_pro"),
        "team_enabled": plan_key in ("enterprise", "company_pro"),
        "workstation_limit": max_ws,
        "execution_credits_daily": plan_cfg.get("daily_tokens", 0),
        "automation_enabled": plan_key != "basic",
        "custom_skills_enabled": plan_key in ("pro_plus", "enterprise", "company_pro"),
    }
    return jsonify({"ok": True, "capabilities": capabilities})


@app.route("/api/auth/sync-plan", methods=["POST"])
def api_sync_plan():
    """Sync user plan to agent config and license"""
    user = authenticate(request)
    if not user:
        return jsonify({"ok": False, "error": "authentication_required"}), 401

    # Update config.json with plan
    cfg = _load_config()
    cfg["plan"] = user["plan"]
    cfg["pc_count"] = user.get("pc_count", 1)
    _save_config(cfg)

    # Update license.json
    lic = DATA_DIR / "license.json"
    if lic.exists():
        license_data = json.loads(lic.read_text())
        license_data["plan"] = user["plan"]
        lic.write_text(json.dumps(license_data, indent=2))

    # Update agent sessions
    sessions_file = DATA_DIR / "agent_sessions.json"
    if sessions_file.exists():
        sessions = json.loads(sessions_file.read_text())
        for s in sessions.values():
            s["plan"] = user["plan"]
        sessions_file.write_text(json.dumps(sessions, indent=2))

    plan_key = get_plan_key(user["plan"])
    plan_cfg = PLANS.get(user["plan"], PLANS.get("trial", {}))
    max_ws = MAX_WORKSTATIONS.get(plan_key, 1)
    max_conc = MAX_CONCURRENT.get(user["plan"], 1)

    return jsonify({
        "ok": True,
        "message": "Plan synced. Capability reload recommended.",
        "capabilities": {
            "plan": user["plan"],
            "gpu_enabled": plan_key in ("pro", "pro_plus", "enterprise", "company_pro"),
            "max_concurrent_tasks": max_conc,
            "background_execution": plan_key in ("pro_plus", "enterprise", "company_pro"),
            "team_enabled": plan_key in ("enterprise", "company_pro"),
            "workstation_limit": max_ws,
            "automation_enabled": plan_key != "basic",
        }
    })


# Update device register to accept fingerprint
@app.route("/api/devices/register", methods=["POST"])
def api_register_device():
    import uuid
    user = authenticate(request)
    if not user:
        return jsonify({"ok": False, "error": "authentication_required"}), 401
    body = request.get_json() or {}
    dev_id = body.get("device_id", "") or uuid.uuid4().hex[:12]
    dev_name = body.get("device_name", "Unnamed PC")
    gpu_name = body.get("gpu_name", "")
    os_name = body.get("os_name", "")
    mac_addr = body.get("mac_address", "")
    disk_serial = body.get("disk_serial", "")
    hostname = body.get("hostname", "")

    # Build fingerprint (MAC + disk + hostname hash)
    import hashlib
    fp_raw = f"{mac_addr}:{disk_serial}:{hostname}"
    fingerprint = hashlib.sha256(fp_raw.encode()).hexdigest()[:16] if fp_raw.strip(":") else dev_id

    data = _load_devices()
    user_devices = [d for d in data["devices"] if d["user_id"] == user["id"]]
    max_ws = MAX_WORKSTATIONS.get(get_plan_key(user["plan"]), 1)

    # Check if same fingerprint already registered (before limit check — allows re-registration)
    for d in user_devices:
        if d.get("fingerprint") == fingerprint:
            d["last_seen"] = datetime.datetime.now().isoformat()
            d["device_name"] = dev_name
            d["gpu_name"] = gpu_name
            d["os_name"] = os_name
            _save_devices(data)
            return jsonify({"ok": True, "device": d, "reused": True})

    if len(user_devices) >= max_ws:
        return jsonify({"ok": False, "error": "This account has reached its active workstation limit."}), 403

    device = {
        "id": dev_id,
        "user_id": user["id"],
        "device_id": dev_id,
        "fingerprint": fingerprint,
        "device_name": dev_name,
        "gpu_name": gpu_name,
        "os_name": os_name,
        "mac_address": mac_addr,
        "hostname": hostname,
        "last_seen": datetime.datetime.now().isoformat(),
        "created_at": datetime.datetime.now().isoformat(),
        "active": True
    }
    data["devices"].append(device)
    _save_devices(data)
    return jsonify({"ok": True, "device": device, "reused": False})


# ── 실행 ──

if __name__ == "__main__":
    # Add datetime import if not present
    try: datetime
    except NameError: from datetime import datetime

    import argparse
    import argparse
    parser = argparse.ArgumentParser(description="Opera AI Agent")
    parser.add_argument("--port", type=int, default=5000, help="포트 번호")
    parser.add_argument("--host", default="127.0.0.1", help="바인딩 주소")
    args = parser.parse_args()

    print(f"""
╔══════════════════════════════════════╗
║        OPERA AI v0.1.0               ║
║        Running on {args.host}:{args.port}        ║
╚══════════════════════════════════════╝

Plan: {_load_config().get('plan', 'trial')}
Skills loaded: {len(skill_registry.get_all_skills())}
Data dir: {DATA_DIR}
""")
    app.run(host=args.host, port=args.port, debug=False)
