#!/usr/bin/env python3
"""
OPERA AI — Main Server
실행: python3 server.py
"""
import os, sys, json, datetime, hashlib, hmac, threading
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
from core.user_manager import register, login, authenticate, get_user
from core.tg_bot import get_bot, COMMANDS
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

def _ai_process(prompt, mode="fast"):
    """AI 명령 처리"""
    return {
        "status": "ai_request",
        "prompt": prompt[:500],
        "mode": mode,
        "note": "DeepSeek API 호출 (workbot-ai 연동 필요)"
    }


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


# ── 웹 대시보드 (랜딩페이지) ──

@app.route("/")
def index():
    return send_from_directory(str(BASE_DIR.parent), "index.html")


@app.route("/<path:path>")
def static_files(path):
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
