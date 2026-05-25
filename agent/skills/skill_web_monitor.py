"""스킬: 웹사이트 변경 감지"""
import requests, json, hashlib
from pathlib import Path

SKILL_INFO = {"id": "skill_web_monitor", "name": "웹사이트 변경 감지", "description": "정기 fetch + 변경 내용 비교", "category": "automation"}
MONITOR_FILE = Path(__file__).parent.parent / "data" / "web_monitor.json"

def _load():
    if MONITOR_FILE.exists():
        return json.load(open(MONITOR_FILE))
    return {"sites": []}

def _save(data):
    MONITOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    json.dump(data, open(MONITOR_FILE, "w"), indent=2)

def register():
    return SKILL_INFO

def execute(action="check", url=None, **kwargs):
    data = _load()
    if action == "add" and url:
        try:
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            h = hashlib.md5(resp.text.encode()).hexdigest()
            data["sites"].append({"url": url, "hash": h, "last_check": __import__("datetime").datetime.now().isoformat()})
            _save(data)
            return {"status": "ok", "url": url, "monitoring": True}
        except Exception as e:
            return {"error": str(e)}
    if action == "check":
        changes = []
        for site in data.get("sites", []):
            try:
                resp = requests.get(site["url"], timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                h = hashlib.md5(resp.text.encode()).hexdigest()
                if h != site["hash"]:
                    changes.append({"url": site["url"], "changed": True})
                    site["hash"] = h
            except:
                changes.append({"url": site["url"], "error": "접근 불가"})
        _save(data)
        return {"status": "ok", "changes": changes, "total": len(data.get("sites", []))}
    if action == "list":
        return {"status": "ok", "sites": [s["url"] for s in data.get("sites", [])]}
    return {"error": "URL 또는 동작이 필요합니다"}
