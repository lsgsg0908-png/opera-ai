"""
PayPal 결제 게이트웨이 — Opera AI 구독 결제
"""
import json
import base64
import uuid
import hashlib
import hmac
from datetime import datetime, timedelta
from pathlib import Path

import requests

DATA_DIR = Path(__file__).parent.parent / "data"
PAYPAL_CONFIG_FILE = DATA_DIR / "paypal_config.json"

# PayPal API 엔드포인트
PAYPAL_SANDBOX = "https://api-m.sandbox.paypal.com"
PAYPAL_LIVE = "https://api-m.paypal.com"

# ── 설정 ──

_ENV = {}
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().strip().split("\n"):
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            _ENV[_k.strip()] = _v.strip()

_DEFAULT_CONFIG = {
    "client_id": _ENV.get("PAYPAL_CLIENT_ID", ""),
    "secret": _ENV.get("PAYPAL_SECRET", ""),
    "webhook_id": _ENV.get("PAYPAL_WEBHOOK_ID", ""),
    "mode": "live",
    "enabled": True,
}


def _load_config():
    if PAYPAL_CONFIG_FILE.exists():
        data = json.load(open(PAYPAL_CONFIG_FILE))
        for k, v in _DEFAULT_CONFIG.items():
            data.setdefault(k, v)
        return data
    return dict(_DEFAULT_CONFIG)


def _save_config(data):
    PAYPAL_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PAYPAL_CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _get_api_base():
    config = _load_config()
    return PAYPAL_LIVE if config.get("mode") == "live" else PAYPAL_SANDBOX


def _get_access_token():
    """PayPal OAuth2 액세스 토큰 발급"""
    config = _load_config()
    if not config.get("enabled"):
        return None, "PayPal disabled"

    url = f"{_get_api_base()}/v1/oauth2/token"
    auth = base64.b64encode(
        f"{config['client_id']}:{config['secret']}".encode()
    ).decode()

    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    body = "grant_type=client_credentials"

    try:
        resp = requests.post(url, headers=headers, data=body, timeout=15)
        if resp.ok:
            data = resp.json()
            return data["access_token"], None
        return None, f"PayPal auth failed: {resp.status_code} {resp.text[:200]}"
    except Exception as e:
        return None, str(e)


# ── 주문 생성 ──

def create_order(plan_id, billing="monthly", quantity=1, bulk=False, return_url=None, cancel_url=None):
    """PayPal 주문 생성
    
    Args:
        plan_id: basic / pro / enterprise
        billing: monthly / yearly
        quantity: PC 수
        bulk: 벌크 할인 여부
        return_url: 결제 성공 후 리디렉션 URL
        cancel_url: 결제 취소 시 리디렉션 URL
    
    Returns:
        PayPal 주문 정보 (approval URL 포함)
    """
    from core.payment import calculate_price, PLANS

    pricing = calculate_price(plan_id, billing, quantity, bulk)
    if not pricing:
        return {"error": "Invalid plan"}

    plan_name = PLANS.get(plan_id, {}).get("name", plan_id)
    amount_usd = f"{pricing['total']:.2f}"
    description = f"OPERA AI {plan_name} ({billing}) - {quantity}PC"

    if not return_url:
        return_url = "https://opera.workbotai.net/payment/success"
    if not cancel_url:
        cancel_url = "https://opera.workbotai.net/payment/cancel"

    token, err = _get_access_token()
    if err:
        # 토큰 실패 시 로컬 구독 생성 (fallback)
        return _fallback_subscription(plan_id, billing, quantity, pricing, description)

    url = f"{_get_api_base()}/v2/checkout/orders"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "reference_id": f"op_{plan_id}_{billing}_{uuid.uuid4().hex[:8]}",
            "description": description,
            "amount": {
                "currency_code": "USD",
                "value": amount_usd,
                "breakdown": {
                    "item_total": {
                        "currency_code": "USD",
                        "value": amount_usd,
                    }
                }
            },
            "items": [{
                "name": f"OPERA AI {plan_name}",
                "description": f"{billing} subscription, {quantity} PC(s)",
                "quantity": str(quantity),
                "unit_amount": {
                    "currency_code": "USD",
                    "value": f"{pricing['base_price']:.2f}",
                },
                "category": "DIGITAL_GOODS",
            }],
        }],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "payment_method_preference": "IMMEDIATE_PAYMENT_REQUIRED",
                    "brand_name": "OPERA AI",
                    "locale": "ko-KR",
                    "landing_page": "LOGIN",
                    "user_action": "PAY_NOW",
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                }
            }
        },
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.ok:
            order = resp.json()
            # approval URL 찾기
            approval_url = None
            for link in order.get("links", []):
                if link.get("rel") == "payer-action":
                    approval_url = link["href"]
                    break

            return {
                "status": "created",
                "paypal_order_id": order["id"],
                "approval_url": approval_url,
                "amount": amount_usd,
                "plan": plan_id,
                "billing": billing,
                "description": description,
                "fallback": False,
            }
        else:
            return _fallback_subscription(plan_id, billing, quantity, pricing, description,
                                          f"PayPal: {resp.status_code}")
    except Exception as e:
        return _fallback_subscription(plan_id, billing, quantity, pricing, description,
                                      str(e))


def capture_order(paypal_order_id):
    """PayPal 결제 캡처 (승인 후 최종 결제)
    
    Args:
        paypal_order_id: PayPal 주문 ID
    
    Returns:
        캡처 결과 + 구독 정보
    """
    token, err = _get_access_token()
    if err:
        return {"error": f"PayPal auth: {err}"}

    url = f"{_get_api_base()}/v2/checkout/orders/{paypal_order_id}/capture"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(url, headers=headers, json={}, timeout=15)
        if resp.ok:
            capture = resp.json()
            status = capture.get("status")

            if status == "COMPLETED":
                # 결제 정보 추출
                purchase_unit = capture.get("purchase_units", [{}])[0]
                payments = purchase_unit.get("payments", {})
                captures = payments.get("captures", [{}])[0]
                payer = capture.get("payer", {})

                payer_email = payer.get("email_address", "")
                payer_name = payer.get("name", {}).get("given_name", "")
                capture_id = captures.get("id", "")
                amount = captures.get("amount", {}).get("value", "0")
                currency = captures.get("amount", {}).get("currency_code", "USD")

                return {
                    "status": "completed",
                    "paypal_order_id": paypal_order_id,
                    "capture_id": capture_id,
                    "payer_email": payer_email,
                    "payer_name": payer_name,
                    "amount": float(amount),
                    "currency": currency,
                    "create_time": capture.get("create_time"),
                    "update_time": capture.get("update_time"),
                    "raw": capture,
                }
            else:
                return {
                    "status": "pending",
                    "paypal_order_id": paypal_order_id,
                    "paypal_status": status,
                }
        else:
            return {"error": f"Capture failed: {resp.status_code} {resp.text[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def verify_webhook(headers, body):
    """PayPal 웹훅 서명 검증
    
    Args:
        headers: 요청 헤더 dict
        body: 요청 본문 (raw string)
    
    Returns:
        (verified: bool, event_type: str)
    """
    config = _load_config()
    webhook_id = config.get("webhook_id", "")

    transmission_id = headers.get("PAYPAL-TRANSMISSION-ID", "")
    transmission_time = headers.get("PAYPAL-TRANSMISSION-TIME", "")
    cert_url = headers.get("PAYPAL-CERT-URL", "")
    auth_algo = headers.get("PAYPAL-AUTH-ALGO", "")
    transmission_sig = headers.get("PAYPAL-TRANSMISSION-SIG", "")
    webhook_event = body if isinstance(body, str) else json.dumps(body)

    if not all([transmission_id, transmission_time, cert_url, auth_algo, transmission_sig]):
        return False, None

    # 서명 검증 문자열
    sig_data = f"{transmission_id}|{transmission_time}|{webhook_id}|{hashlib.md5(webhook_event.encode()).hexdigest()}"

    # 인증서 URL에서 공개키 가져오기
    try:
        cert_resp = requests.get(cert_url, timeout=10)
        if not cert_resp.ok:
            return False, None

        from cryptography import x509
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        cert = x509.load_pem_x509_certificate(cert_resp.content)
        public_key = cert.public_key()
        signature = base64.b64decode(transmission_sig)

        public_key.verify(
            signature,
            sig_data.encode(),
            padding.PKCS1v15(),
            getattr(hashes, auth_algo.replace("-", ""))() if hasattr(hashes, auth_algo.replace("-", "")) else hashes.SHA256(),
        )

        event_data = json.loads(webhook_event) if isinstance(webhook_event, str) else webhook_event
        event_type = event_data.get("event_type")

        return True, event_type
    except Exception:
        # HMAC 방식으로 fallback 검증
        return _verify_hmac_fallback(webhook_id, sig_data, transmission_sig)


def _verify_hmac_fallback(webhook_id, sig_data, signature_b64):
    """HMAC-SHA256 fallback 검증"""
    try:
        expected = hmac.new(
            webhook_id.encode(),
            sig_data.encode(),
            hashlib.sha256,
        ).digest()
        actual = base64.b64decode(signature_b64)
        return hmac.compare_digest(expected, actual), None
    except Exception:
        return False, None


def _fallback_subscription(plan_id, billing, quantity, pricing, description, reason=""):
    """PayPal 실패 시 로컬 구독 fallback"""
    from core.payment import create_subscription

    sub = create_subscription(plan_id, billing, quantity)

    return {
        "status": "created",
        "paypal_order_id": None,
        "approval_url": None,
        "amount": f"{pricing['total']:.2f}",
        "plan": plan_id,
        "billing": billing,
        "description": description,
        "subscription": sub,
        "fallback": True,
        "fallback_reason": reason,
        "message": "PayPal 일시 불가 — 로컬 구독으로 처리됨",
    }


def configure(mode="live", client_id=None, secret=None, webhook_id=None):
    """PayPal 설정 업데이트"""
    config = _load_config()
    if mode:
        config["mode"] = mode
    if client_id:
        config["client_id"] = client_id
    if secret:
        config["secret"] = secret
    if webhook_id:
        config["webhook_id"] = webhook_id
    config["enabled"] = True
    _save_config(config)
    return {"status": "configured", "mode": config["mode"]}


def get_config_status():
    """PayPal 설정 상태 확인"""
    config = _load_config()
    return {
        "enabled": config.get("enabled", False),
        "mode": config.get("mode", "sandbox"),
        "client_id_configured": bool(config.get("client_id")),
        "secret_configured": bool(config.get("secret")),
        "webhook_id_configured": bool(config.get("webhook_id")),
    }
