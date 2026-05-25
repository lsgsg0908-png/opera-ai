"""스킬: 메신저 발송"""
import requests, json
from pathlib import Path

SKILL_INFO = {"id": "skill_messenger", "name": "메신저 발송", "description": "TG/Slack/카톡 메시지 발송", "category": "communication"}
CONFIG_FILE = Path(__file__).parent.parent / "data" / "messenger_config.json"

def register():
    return SKILL_INFO

def _get_config():
    if CONFIG_FILE.exists():
        return json.load(open(CONFIG_FILE))
    return {}

def execute(action="send", platform="", to="", message="", **kwargs):
    if action == "config":
        with open(CONFIG_FILE, "w") as f:
            json.dump(kwargs, f)
        return {"status": "ok", "message": "메신저 설정이 저장되었습니다"}

    if action == "send" and platform and message:
        config = _get_config()
        if platform == "telegram":
            bot_token = config.get("tg_bot_token") or kwargs.get("bot_token")
            chat_id = to or config.get("tg_chat_id")
            if bot_token and chat_id:
                resp = requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": message},
                    timeout=10
                )
                if resp.ok:
                    return {"status": "ok", "platform": "telegram"}
                return {"error": resp.text}
        if platform == "slack":
            webhook = config.get("slack_webhook") or kwargs.get("webhook")
            if webhook:
                resp = requests.post(webhook, json={"text": message}, timeout=10)
                return {"status": "ok" if resp.ok else "error", "detail": resp.text[:200]}
        return {"error": f"메신저 설정이 필요합니다 ({platform})"}
    return {"error": "메시지 또는 플랫폼이 필요합니다"}
