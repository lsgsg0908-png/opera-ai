"""
결제 시스템 — 요금제, PayPal, 연결제 할인
"""
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
SUBSCRIPTION_FILE = DATA_DIR / "subscriptions.json"
LICENSE_FILE = DATA_DIR / "license.json"

# 요금제 정의
PLANS = {
    "basic": {
        "id": "basic",
        "name": "Basic",
        "price_monthly": 69,
        "price_yearly": 690,   # 17% 할인
        "extra_pc_price": 34.5,
    },
    "pro": {
        "id": "pro",
        "name": "Pro",
        "price_monthly": 109,
        "price_yearly": 1090,  # 17% 할인
        "extra_pc_price": 54.5,
    },
    "enterprise": {
        "id": "enterprise",
        "name": "Enterprise",
        "price_monthly": 199,
        "price_yearly": 1990,  # 17% 할인
        "extra_pc_price": 99.5,
    },
}

# 벌크 할인
BULK_DISCOUNT = {
    5: 0.12,   # 5대 이상 → 12% 할인
    10: 0.18,  # 10대 이상 → 18% 할인
    25: 0.25,  # 25대 이상 → 25% 할인
}


def _load_subs():
    if SUBSCRIPTION_FILE.exists():
        return json.load(open(SUBSCRIPTION_FILE))
    return {"subscriptions": []}


def _save_subs(data):
    SUBSCRIPTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SUBSCRIPTION_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_plans():
    """요금제 목록"""
    return list(PLANS.values())


def calculate_price(plan_id, billing="monthly", quantity=1, bulk=False):
    """가격 계산"""
    plan = PLANS.get(plan_id)
    if not plan:
        return None

    if billing == "yearly":
        base_price = plan["price_yearly"]
    else:
        base_price = plan["price_monthly"]

    total = base_price * quantity

    # 벌크 할인
    if bulk and quantity > 1:
        discount = 0
        for q, d in sorted(BULK_DISCOUNT.items(), reverse=True):
            if quantity >= q:
                discount = d
                break
        total = round(total * (1 - discount), 2)

    return {
        "plan": plan_id,
        "plan_name": plan["name"],
        "billing": billing,
        "quantity": quantity,
        "base_price": base_price,
        "total": total,
        "bulk_discount": bulk,
    }


def create_subscription(plan_id, billing, quantity=1, user_id="local"):
    """구독 생성"""
    data = _load_subs()
    pricing = calculate_price(plan_id, billing, quantity)
    if not pricing:
        return {"error": "Invalid plan"}

    now = datetime.now()
    if billing == "yearly":
        expires = now + timedelta(days=365)
    else:
        expires = now + timedelta(days=30)

    sub = {
        "id": f"sub_{uuid.uuid4().hex[:8]}",
        "user_id": user_id,
        "plan": plan_id,
        "billing": billing,
        "quantity": quantity,
        "price": pricing["total"],
        "status": "active",
        "created_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "auto_renew": True,
        "payments": [],
    }
    data["subscriptions"].append(sub)
    _save_subs(data)

    # 라이선스 생성
    from core.token_manager import PLANS as TOKEN_PLANS
    plan_cfg = TOKEN_PLANS.get(plan_id, TOKEN_PLANS["basic"])
    license_key = f"OPERA-{uuid.uuid4().hex[:12].upper()}"
    with open(LICENSE_FILE, "w") as f:
        json.dump({
            "key": license_key,
            "plan": plan_id,
            "valid": True,
            "created_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "pc_count": quantity,
        }, f)

    return {
        "status": "created",
        "subscription_id": sub["id"],
        "plan": plan_id,
        "price": pricing["total"],
        "expires_at": sub["expires_at"],
        "license_key": license_key,
    }


def get_subscription(user_id="local"):
    """구독 정보 조회"""
    data = _load_subs()
    active = [s for s in data["subscriptions"] if s.get("user_id") == user_id and s["status"] == "active"]
    if not active:
        return None
    return sorted(active, key=lambda s: s["created_at"], reverse=True)[0]


def cancel_subscription(sub_id):
    """구독 취소"""
    data = _load_subs()
    for s in data["subscriptions"]:
        if s["id"] == sub_id:
            s["status"] = "cancelled"
            s["auto_renew"] = False
            _save_subs(data)
            return {"status": "cancelled"}
    return {"error": "Subscription not found"}


def renew_subscription(user_id="local"):
    """구독 갱신 (자동)"""
    sub = get_subscription(user_id)
    if not sub:
        return {"error": "No active subscription"}

    if not sub.get("auto_renew"):
        return {"error": "Auto-renew disabled"}

    now = datetime.now()
    expires = datetime.fromisoformat(sub["expires_at"])
    if now < expires:
        return {"status": "still_active", "expires_at": sub["expires_at"]}

    # 갱신
    if sub["billing"] == "yearly":
        new_expires = now + timedelta(days=365)
    else:
        new_expires = now + timedelta(days=30)

    sub["expires_at"] = new_expires.isoformat()
    sub["payments"].append({
        "date": now.isoformat(),
        "amount": sub["price"],
        "type": "auto_renew",
    })
    _save_subs(_load_subs())

    return {"status": "renewed", "expires_at": sub["expires_at"]}


def get_bulk_discount_tiers():
    """벌크 할인 안내"""
    return [{"min_qty": q, "discount": f"{int(d*100)}%"} for q, d in sorted(BULK_DISCOUNT.items())]
