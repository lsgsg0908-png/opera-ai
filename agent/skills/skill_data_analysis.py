"""스킬: 데이터 분석·통계"""
import json, csv, io

SKILL_INFO = {"id": "skill_data_analysis", "name": "데이터 분석·통계", "description": "CSV/JSON 분석, 차트 생성", "category": "office"}

def register():
    return SKILL_INFO

def execute(action="analyze", data=None, path=None, format="json", **kwargs):
    if path and not data:
        try:
            with open(path) as f:
                if path.endswith(".json"):
                    data = json.load(f)
                elif path.endswith(".csv"):
                    reader = csv.DictReader(f)
                    data = list(reader)
        except Exception as e:
            return {"error": f"파일 읽기 오류: {str(e)}"}

    if not data:
        return {"error": "분석할 데이터가 필요합니다"}

    summary = {}
    if isinstance(data, list) and len(data) > 0:
        summary["count"] = len(data)
        if isinstance(data[0], dict):
            summary["fields"] = list(data[0].keys())
            summary["sample"] = data[:3]
    elif isinstance(data, dict):
        summary["keys"] = list(data.keys())[:20]
        summary["item_count"] = len(data)

    return {"status": "ok", "summary": summary, "data_preview": str(data)[:5000]}
