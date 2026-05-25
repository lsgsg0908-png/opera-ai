"""스킬: 일정·할일 관리"""
import json, datetime
from pathlib import Path

SKILL_INFO = {"id": "skill_schedule_mgmt", "name": "일정·할일 관리", "description": "캘린더 일정 등록 및 관리", "category": "utility"}

DATA_FILE = Path(__file__).parent.parent / "data" / "schedule.json"

def _load():
    if DATA_FILE.exists():
        return json.load(open(DATA_FILE))
    return {"events": [], "todos": []}

def _save(data):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    json.dump(data, open(DATA_FILE, "w"), indent=2, ensure_ascii=False)

def register():
    return SKILL_INFO

def execute(action="list", **kwargs):
    data = _load()
    if action == "list":
        return {"status": "ok", "events": data.get("events", [])[:50], "todos": data.get("todos", [])[:50]}
    if action == "add_event":
        evt = {"title": kwargs.get("title", ""), "date": kwargs.get("date", ""), "time": kwargs.get("time", ""),
               "note": kwargs.get("note", ""), "created": datetime.datetime.now().isoformat()}
        data["events"].append(evt)
        _save(data)
        return {"status": "ok", "event": evt}
    if action == "add_todo":
        todo = {"task": kwargs.get("task", ""), "due": kwargs.get("due", ""), "done": False,
                "created": datetime.datetime.now().isoformat()}
        data["todos"].append(todo)
        _save(data)
        return {"status": "ok", "todo": todo}
    return {"error": f"지원하지 않는 동작: {action}"}
