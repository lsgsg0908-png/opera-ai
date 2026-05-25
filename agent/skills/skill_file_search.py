"""스킬: 파일 검색·내용 분석"""
from core.executor import file_search

SKILL_INFO = {"id": "skill_file_search", "name": "파일 검색·내용 분석", "description": "내부 문서 내용 기반 검색", "category": "utility"}

def register():
    return SKILL_INFO

def execute(action="search", query=None, root="~", **kwargs):
    if not query:
        return {"error": "검색어가 필요합니다"}
    return file_search(query, root)
