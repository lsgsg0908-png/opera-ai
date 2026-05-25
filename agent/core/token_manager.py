"""
토큰 관리 모듈 — 일일 지급, 한도 초과 차단, 정량제 추가, 이월
"""
import json
import os
from datetime import datetime, date, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
TOKEN_FILE = DATA_DIR / "tokens.json"
CONFIG_FILE = DATA_DIR / "config.json"

# DeepSeek V4 Flash 공식 요금 (2026-04-26 기준)
# cache hit input: $0.0028/1M tokens
# cache miss input: $0.14/1M tokens
# output: $0.28/1M tokens
DEEPSEEK_PRICES = {
    "cache_hit_input": 0.0028 / 1_000_000,
    "cache_miss_input": 0.14 / 1_000_000,
    "output": 0.28 / 1_000_000,
    "cache_hit_rate": 0.7,  # 최적화 후 예상 캐시 히트율
}

# 고객 노출용 표준 토큰 단가 (최적화 전 기준)
STANDARD_TOKEN_PRICE = 0.10 / 1000  # $0.10 per 1000 tokens

# 플랜별 설정
PLANS = {
    "trial": {
        "label": "3일 무료 체험 (Pro)",
        "monthly_tokens": 0,
        "daily_tokens": 15_000,
        "trial_days": 3,
        "max_concurrent": 3,
        "skill_limit": 10,
        "mode": "deep",
        "history_days": 7,
    },
    "basic": {
        "label": "Basic",
        "price": 69,
        "monthly_tokens": 345_000,  # $34.50 worth at $0.10/1K
        "daily_tokens": 11_500,     # 345000/30
        "trial_days": 0,
        "max_concurrent": 1,
        "skill_limit": 3,
        "mode": "fast",
        "history_days": 7,
        "extra_pc_price": 34.5,
        "extra_pc_token_boost": 0.5,
    },
    "pro": {
        "label": "Pro",
        "price": 109,
        "monthly_tokens": 545_000,
        "daily_tokens": 18_167,
        "trial_days": 0,
        "max_concurrent": 3,
        "skill_limit": 10,
        "mode": "deep",
        "history_days": 30,
        "extra_pc_price": 54.5,
        "extra_pc_token_boost": 0.5,
    },
    "enterprise": {
        "label": "Enterprise",
        "price": 199,
        "monthly_tokens": 995_000,
        "daily_tokens": 33_167,
        "trial_days": 0,
        "max_concurrent": 10,
        "skill_limit": 999,
        "mode": "expert",
        "history_days": 365,
        "extra_pc_price": 99.5,
        "extra_pc_token_boost": 0.5,
    },
}

# 정량제 추가 토큰팩
TOPUP_PACKS = [
    {"tokens": 10_000, "price": 3},
    {"tokens": 50_000, "price": 14},
    {"tokens": 100_000, "price": 25},
    {"tokens": 500_000, "price": 110},
]


def _load_tokens():
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE) as f:
            return json.load(f)
    return {"daily_used": {}, "purchased_pool": 0, "last_reset": str(date.today())}


def _save_tokens(data):
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {"plan": "trial", "pc_count": 1, "trial_start": str(date.today()), "skills": []}


def _save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_plan_config(plan=None):
    if plan is None:
        config = _load_config()
        plan = config.get("plan", "trial")
    return PLANS.get(plan, PLANS["trial"])


def get_daily_allowance(plan=None, pc_count=1):
    """일일 토큰 허용량 계산"""
    plan_cfg = get_plan_config(plan)
    daily = plan_cfg["daily_tokens"]
    # 추가 PC당 기본+50%
    if pc_count > 1:
        extra = pc_count - 1
        daily = daily + (daily * extra * plan_cfg.get("extra_pc_token_boost", 0.5))
    return int(daily)


def get_today_used():
    """오늘 사용한 토큰"""
    data = _load_tokens()
    today = str(date.today())
    # 날짜 변경 시 리셋
    if data["last_reset"] != today:
        data["daily_used"] = {}
        data["last_reset"] = today
        _save_tokens(data)
    return data["daily_used"].get(today, 0)


def check_token_available(tokens_needed):
    """토큰 사용 가능 여부 체크"""
    config = _load_config()
    daily_allowance = get_daily_allowance(config["plan"], config["pc_count"])
    today_used = get_today_used()
    daily_remaining = daily_allowance - today_used

    if daily_remaining >= tokens_needed:
        return True, "daily", daily_remaining

    # 일일 한도 소진 → 구매 토큰 풀 확인
    data = _load_tokens()
    if data["purchased_pool"] >= tokens_needed:
        return True, "purchased", data["purchased_pool"]

    return False, "insufficient", max(0, daily_remaining) + data["purchased_pool"]


def consume_tokens(tokens_used):
    """토큰 소비"""
    config = _load_config()
    data = _load_tokens()
    today = str(date.today())

    # 날짜 리셋 체크
    if data["last_reset"] != today:
        data["daily_used"] = {}
        data["last_reset"] = today

    daily_allowance = get_daily_allowance(config["plan"], config["pc_count"])
    today_used = data["daily_used"].get(today, 0)
    daily_remaining = daily_allowance - today_used

    used_from_daily = min(tokens_used, max(0, daily_remaining))
    used_from_purchased = tokens_used - used_from_daily

    if used_from_daily > 0:
        data["daily_used"][today] = today_used + used_from_daily

    if used_from_purchased > 0:
        data["purchased_pool"] -= used_from_purchased

    _save_tokens(data)
    return used_from_daily, used_from_purchased


def add_purchased_tokens(tokens):
    """구매 토큰 추가 (이월 가능)"""
    data = _load_tokens()
    data["purchased_pool"] += tokens
    _save_tokens(data)
    return data["purchased_pool"]


def get_token_status():
    """현재 토큰 상태"""
    config = _load_config()
    data = _load_tokens()
    daily_allowance = get_daily_allowance(config["plan"], config["pc_count"])
    today_used = get_today_used()
    today_str = str(date.today())

    if data["last_reset"] != today_str:
        data["daily_used"] = {}
        data["last_reset"] = today_str
        today_used = 0
        _save_tokens(data)

    return {
        "plan": config["plan"],
        "daily_allowance": daily_allowance,
        "today_used": today_used,
        "today_remaining": daily_allowance - today_used,
        "purchased_pool": data["purchased_pool"],
        "total_available": (daily_allowance - today_used) + data["purchased_pool"],
        "packs": TOPUP_PACKS,
    }


def get_actual_cost(tokens_used):
    """실제 API 비용 계산 (내부용)"""
    # 평균: 70% cache hit, 30% cache miss, output ~30% of total
    cache_hit = tokens_used * 0.7 * 0.7  # 70% input, 70% cache hit
    cache_miss = tokens_used * 0.7 * 0.3  # 70% input, 30% cache miss
    output = tokens_used * 0.3  # 30% output

    cost = (
        cache_hit * DEEPSEEK_PRICES["cache_hit_input"]
        + cache_miss * DEEPSEEK_PRICES["cache_miss_input"]
        + output * DEEPSEEK_PRICES["output"]
    )
    return round(cost, 6)


def estimate_tokens(text_length, task_complexity="normal"):
    """작업별 예상 토큰 수 추정"""
    base = text_length * 1.5  # 한국어는 토큰 비율 높음
    multipliers = {"simple": 1.0, "normal": 2.0, "complex": 5.0}
    multiplier = multipliers.get(task_complexity, 2.0)
    return int(base * multiplier)
