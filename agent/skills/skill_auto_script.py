"""스킬: 반복 작업 자동화"""
import json, datetime
from pathlib import Path

SKILL_INFO = {"id": "skill_auto_script", "name": "반복 작업 자동화", "description": "매크로 녹화/재생/스크립트 생성", "category": "automation"}

SCRIPTS_FILE = Path(__file__).parent.parent / "data" / "auto_scripts.json"

def _load():
    if SCRIPTS_FILE.exists():
        return json.load(open(SCRIPTS_FILE))
    return {"scripts": []}

def _save(data):
    SCRIPTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    json.dump(data, open(SCRIPTS_FILE, "w"), indent=2)

def register():
    return SKILL_INFO

def execute(action="list", **kwargs):
    data = _load()
    if action == "list":
        return {"status": "ok", "scripts": [s["name"] for s in data.get("scripts", [])]}
    if action == "create":
        script = {
            "name": kwargs.get("name", f"script_{len(data['scripts'])+1}"),
            "steps": kwargs.get("steps", []),
            "interval": kwargs.get("interval", "manual"),
            "created": datetime.datetime.now().isoformat()
        }
        data["scripts"].append(script)
        _save(data)
        return {"status": "ok", "script": script["name"]}
    if action == "delete" and kwargs.get("name"):
        data["scripts"] = [s for s in data["scripts"] if s["name"] != kwargs["name"]]
        _save(data)
        return {"status": "ok", "deleted": kwargs["name"]}
    return {"error": f"지원 동작: list, create, delete"}
