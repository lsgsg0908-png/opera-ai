"""
텔레그램 봇 — Opera AI 제어용
"""
import json
import requests
import threading
import time
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
TG_CONFIG_FILE = DATA_DIR / "tg_config.json"

# 명령어 정의
COMMANDS = {
    "/start": "Opera AI 봇을 시작합니다",
    "/pc-on": "PC를 켭니다 (WOL)",
    "/pc-off": "PC를 종료합니다",
    "/pc-status": "PC 상태를 확인합니다",
    "/run": "작업을 지시합니다",
    "/tokens": "토큰 잔여량을 확인합니다",
    "/plan": "현재 요금제를 확인합니다",
    "/skills": "내 스킬 목록을 확인합니다",
    "/history": "최근 작업 이력을 확인합니다",
    "/help": "명령어 목록을 표시합니다",
}


def _load_config():
    if TG_CONFIG_FILE.exists():
        return json.load(open(TG_CONFIG_FILE))
    return {"bot_token": "", "chat_id": "", "enabled": False}


def _save_config(data):
    TG_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TG_CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)


class TGBot:
    def __init__(self):
        self._config = _load_config()
        self._offset = 0
        self._running = False

    def configure(self, bot_token, chat_id):
        """봇 설정"""
        self._config["bot_token"] = bot_token
        self._config["chat_id"] = chat_id
        self._config["enabled"] = True
        _save_config(self._config)
        return {"status": "configured"}

    def send_message(self, text, parse_mode=None):
        """메시지 발송"""
        if not self._config.get("enabled"):
            return {"error": "TG 봇이 설정되지 않았습니다"}

        url = f"https://api.telegram.org/bot{self._config['bot_token']}/sendMessage"
        payload = {"chat_id": self._config["chat_id"], "text": str(text)[:4000]}
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            resp = requests.post(url, json=payload, timeout=10)
            return {"status": "sent" if resp.ok else "error", "detail": resp.text[:200]}
        except Exception as e:
            return {"error": str(e)}

    def start_polling(self):
        """폴링 시작 (별도 스레드)"""
        if not self._config.get("enabled"):
            return {"error": "TG 봇이 설정되지 않았습니다"}

        self._running = True
        thread = threading.Thread(target=self._poll_loop, daemon=True)
        thread.start()
        return {"status": "polling_started"}

    def stop_polling(self):
        """폴링 중지"""
        self._running = False

    def _poll_loop(self):
        """메시지 폴링 루프"""
        while self._running:
            try:
                self._process_updates()
            except Exception:
                pass
            time.sleep(2)

    def _process_updates(self):
        """업데이트 처리"""
        url = f"https://api.telegram.org/bot{self._config['bot_token']}/getUpdates"
        resp = requests.get(url, params={
            "offset": self._offset,
            "timeout": 10,
        }, timeout=15)

        if not resp.ok:
            return

        updates = resp.json().get("result", [])
        for update in updates:
            self._offset = update["update_id"] + 1
            message = update.get("message", {})
            text = message.get("text", "")
            chat_id = message.get("chat", {}).get("id")

            if text.startswith("/"):
                self._handle_command(text, chat_id)

    def _handle_command(self, text, chat_id):
        """명령어 처리"""
        cmd = text.split()[0].lower()

        if cmd == "/start":
            msg = "🎵 Opera AI 봇입니다.\n명령어 목록은 /help 를 입력하세요."
        elif cmd == "/help":
            msg = "명령어 목록:\n" + "\n".join([f"{k} — {v}" for k, v in COMMANDS.items()])
        elif cmd == "/tokens":
            from core.token_manager import get_token_status
            status = get_token_status()
            msg = (
                f"📊 토큰 현황\n"
                f"  일일 허용량: {status['daily_allowance']:,}\n"
                f"  오늘 사용: {status['today_used']:,}\n"
                f"  오늘 남음: {status['today_remaining']:,}\n"
                f"  구매 토큰: {status['purchased_pool']:,}"
            )
        elif cmd == "/plan":
            from core.token_manager import _load_config
            cfg = _load_config()
            plan = cfg.get("plan", "trial")
            from core.token_manager import PLANS
            plan_cfg = PLANS.get(plan, PLANS["trial"])
            msg = f"💳 현재 요금제: {plan_cfg.get('label', plan)}\n"
            if plan_cfg.get("price"):
                msg += f"  가격: ${plan_cfg['price']}/월\n"
            msg += f"  일일 토큰: {plan_cfg['daily_tokens']:,}\n"
            msg += f"  스킬 제한: {plan_cfg['skill_limit']}개\n"
            msg += f"  동시 작업: {plan_cfg['max_concurrent']}개"
        elif cmd == "/skills":
            from core.skill_manager import get_registry
            from core.token_manager import _load_config
            cfg = _load_config()
            skills = get_registry().get_all_skills()
            selected = cfg.get("skills", [])
            names = [s["name"] for s in skills if s["id"] in selected]
            msg = "🎯 내 스킬:\n" + ("\n".join([f"  • {n}" for n in names]) if names else "  선택된 스킬 없음")
        elif cmd == "/history":
            from core.task_queue import get_queue
            history = get_queue().get_history(5)
            if history:
                msg = "📋 최근 작업:\n" + "\n".join([f"  • {h.get('action','')} ({h.get('status','')})" for h in history])
            else:
                msg = "📋 최근 작업 없음"
        elif cmd == "/run":
            msg = "🤖 작업을 입력하세요.\n예: 문서작성, 엑셀정리, 검색..."
        elif cmd in ("/pc-on", "/pc-off", "/pc-status"):
            msg = f"⚡ {cmd}: 이 기능은 Opera AI가 PC에 설치된 환경에서 동작합니다"
        else:
            msg = f"알 수 없는 명령어입니다. /help 를 입력하세요."

        # 메시지 발송
        url = f"https://api.telegram.org/bot{self._config['bot_token']}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": msg}, timeout=10)


# 싱글톤
_bot = None


def get_bot():
    global _bot
    if _bot is None:
        _bot = TGBot()
    return _bot
