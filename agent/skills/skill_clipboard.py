"""스킬: 클립보드 관리"""
from core.executor import clipboard_get, clipboard_set

SKILL_INFO = {"id": "skill_clipboard", "name": "클립보드 관리", "description": "클립보드 읽기/쓰기/히스토리", "category": "utility"}

_history = []

def register():
    return SKILL_INFO

def execute(action="read", **kwargs):
    if action == "read":
        return clipboard_get()
    if action == "write":
        text = kwargs.get("text", "")
        result = clipboard_set(text)
        if result.get("status") == "ok":
            _history.append({"text": text[:100], "time": __import__("datetime").datetime.now().isoformat()})
            if len(_history) > 100:
                _history.pop(0)
        return result
    if action == "history":
        return {"status": "ok", "history": _history[-20:]}
    return {"error": "read/write/history 중 선택해주세요"}
