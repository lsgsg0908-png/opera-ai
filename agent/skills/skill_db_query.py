"""스킬: DB 쿼리·데이터 추출"""
import sqlite3, json
from pathlib import Path

SKILL_INFO = {"id": "skill_db_query", "name": "DB 쿼리·데이터 추출", "description": "SQL 생성 및 실행, 데이터 추출", "category": "dev"}

def register():
    return SKILL_INFO

def execute(action="query", db_path=None, sql=None, **kwargs):
    if action == "generate":
        return {"status": "generation_request", "note": "AI가 SQL을 생성합니다..."}
    if action == "query" and db_path and sql:
        if not Path(db_path).exists():
            return {"error": "DB 파일을 찾을 수 없습니다"}
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(sql)
            rows = [dict(r) for r in cur.fetchall()[:100]]
            conn.close()
            return {"status": "ok", "rows": rows, "count": len(rows)}
        except Exception as e:
            return {"error": str(e)}
    return {"error": "DB 경로와 SQL이 필요합니다"}
