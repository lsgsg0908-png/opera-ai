"""스킬: 이메일 작성·발송"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json, os
from pathlib import Path

SKILL_INFO = {"id": "skill_email_send", "name": "이메일 작성·발송", "description": "SMTP 이메일 작성 및 발송", "category": "communication"}

CONFIG_FILE = Path(__file__).parent.parent / "data" / "email_config.json"

def register():
    return SKILL_INFO

def execute(action="send", to=None, subject="", body="", cc=None, bcc=None, smtp_config=None, **kwargs):
    if action == "config":
        if smtp_config:
            with open(CONFIG_FILE, "w") as f:
                json.dump(smtp_config, f)
            return {"status": "ok", "message": "이메일 설정이 저장되었습니다"}
        return {"error": "설정 정보가 필요합니다"}

    if action == "send":
        if not to:
            return {"error": "수신자가 필요합니다"}
        # 설정 로드
        config = {}
        if CONFIG_FILE.exists():
            config = json.load(open(CONFIG_FILE))

        if smtp_config:
            config.update(smtp_config)

        if not config.get("smtp_server") or not config.get("smtp_user"):
            return {"error": "SMTP 설정이 필요합니다. 먼저 이메일 설정을 완료해주세요."}

        try:
            msg = MIMEMultipart()
            msg["From"] = config.get("smtp_user", "")
            msg["To"] = to
            msg["Subject"] = subject
            if cc:
                msg["Cc"] = cc
            msg.attach(MIMEText(body, "plain", "utf-8"))

            recipients = [to]
            if cc:
                recipients += [cc]

            with smtplib.SMTP(config["smtp_server"], config.get("smtp_port", 587)) as server:
                server.starttls()
                server.login(config["smtp_user"], config.get("smtp_pass", ""))
                server.send_message(msg)

            return {"status": "ok", "to": to, "subject": subject, "message": "이메일이 발송되었습니다"}
        except Exception as e:
            return {"error": f"이메일 발송 실패: {str(e)}"}

    return {"error": f"지원하지 않는 동작: {action}"}
