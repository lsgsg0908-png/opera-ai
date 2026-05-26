#!/usr/bin/env python3
"""
OPERA AI — Main Server
실행: python3 server.py
"""
import os, sys, json, datetime, hashlib, hmac, threading, time
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
    system_info, clipboard_get, clipboard_set, ocr_image
)
from core.skill_manager import get_registry
from core.task_queue import get_queue
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
            "message": f"요청이 너무 빠릅니다. {limit}회/분 제한",
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


def _log_history(user_id, action, detail, tokens_used=0):
    """작업 히스토리 저장"""
    entry = {
        "time": datetime.datetime.now().isoformat(),
        "user": user_id,
        "action": action,
        "detail": str(detail)[:200],
        "tokens": tokens_used
    }
    today = str(datetime.date.today())
    log_file = HISTORY_DIR / f"{today}.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── API: 사용자 ──

@app.route("/api/auth/register", methods=["POST"])
def api_register():
    data = request.get_json() or {}
    result = register(
        email=data.get("email", ""),
        password=data.get("password", ""),
        username=data.get("username", "")
    )
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


@app.route("/api/auth/force-logout", methods=["POST"])
def api_force_logout():
    """사용자 강제 로그아웃 (모든 세션)"""
    user = authenticate(request)
    if not user:
        return jsonify({"error": "인증 필요"}), 401
    data = request.get_json() or {}
    target_user_id = data.get("user_id", user["id"])
    result = force_logout_all(target_user_id)
    return jsonify(result)


@app.route("/api/me", methods=["GET"])
def api_me():
    user = authenticate(request)
    if not user:
        return jsonify({"error": "인증 필요", "user": None})
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
        return jsonify({"error": "라이선스 검증 실패"})

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
            "message": "보안 위반이 감지되었습니다",
            "strike": strike_result
        })

    # ── Layer 2: Rate Limit ──
    rate = check_rate_limit(10)
    if not rate.get("allowed"):
        return jsonify({"error": "요청이 너무 빠릅니다. 잠시 후 다시 시도해주세요", "retry_after": rate.get("retry_after", 0)})

    # ── Layer 1: 경로 차단 ──
    check_path = params.get("path", "")
    if check_path and is_path_blocked(check_path):
        _log_history("blocked_path", check_path, {}, 0)
        return jsonify({"error": "접근이 차단된 경로입니다"})

    # 토큰 예상 소비량
    est_tokens = estimate_tokens(len(json.dumps(data)), params.get("complexity", "normal"))

    # 토큰 체크
    available, source, remaining = check_token_available(est_tokens)
    if not available:
        return jsonify({
            "error": "일일 토큰 한도를 초과했습니다",
            "token_status": get_token_status(),
            "suggestion": "정량제 토큰을 추가 구매하거나 내일 다시 시도해주세요"
        })

    # 실행
    result = _route_action(action, params)
    result["tokens_used"] = est_tokens

    # 토큰 소비
    consume_tokens(est_tokens)
    _log_history(config.get("user_id", "local"), action, result, est_tokens)

    return jsonify(result)


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

    try:
        return handler()
    except Exception as e:
        return {"error": str(e)}


# ── AI 처리 ──

# ── DeepSeek API (서버 전용, 키 보호) ──
import os as _os
_ENV = {}
_env_path = BASE_DIR / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().strip().split("\n"):
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            _ENV[_k.strip()] = _v.strip()

_DEEPSEEK_KEY = _ENV.get("DEEPSEEK_API_KEY", "")
_DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
_DS_MODELS = {"fast": "deepseek-v4-flash", "deep": "deepseek-v4-flash", "expert": "deepseek-v4-flash"}
_DS_MAX_TOKENS = {"fast": 1024, "deep": 4096, "expert": 8192}
_DS_TEMPS = {"fast": 0.3, "deep": 0.1, "expert": 0.05}
_DS_SYSTEM = "당신은 OPERA AI(리치)입니다. PC 작업을 자동화하는 AI 비서입니다. 사용자의 명령을 분석하고 적절히 응답하거나 작업을 실행합니다. 한국어로 응답하고, 불필요한 설명 없이 핵심만 전달합니다."


def _ds_chat(messages, mode="fast"):
    """DeepSeek API 호출 (내부용)"""
    import requests as _req
    try:
        resp = _req.post(_DEEPSEEK_URL, json={
            "model": _DS_MODELS.get(mode, "deepseek-chat"),
            "messages": messages,
            "max_tokens": _DS_MAX_TOKENS.get(mode, 1024),
            "temperature": _DS_TEMPS.get(mode, 0.3),
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
        return None, 0, f"DeepSeek {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return None, 0, str(e)


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
    
    # 토큰 추적
    from core.token_manager import consume_tokens, get_actual_cost
    cost = get_actual_cost(tokens)
    consume_tokens(tokens)
    _log_history("ds_proxy", prompt[:100], {"tokens": tokens, "cost": cost}, tokens)
    
    # 작업 명령 파싱
    commands = _parse_ai_commands(content, prompt)
    
    return jsonify({
        "status": "ok",
        "response": content,
        "commands": commands,
        "tokens_used": tokens,
        "actual_cost": cost,
        "model": _DS_MODELS.get(mode),
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
        return {"status": "error", "error": f"AI 오류: {err}"}
    
    from core.token_manager import consume_tokens, get_actual_cost
    cost = get_actual_cost(tokens)
    consume_tokens(tokens)
    commands = _parse_ai_commands(content, prompt)
    _log_history("ai_chat", prompt[:100], {"tokens": tokens, "cost": cost}, tokens)
    
    return {
        "status": "ok",
        "response": content,
        "commands": commands,
        "tokens_used": tokens,
        "actual_cost": cost,
        "model": _DS_MODELS.get(mode),
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
    return jsonify({"error": "유효하지 않은 토큰팩"})


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


# ── 실행 ──

if __name__ == "__main__":
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
