"""스킬: 문서 작성·편집"""
from core.executor import keyboard_type, file_write

SKILL_INFO = {"id": "skill_doc_write", "name": "문서 작성·편집", "description": "한글/워드/텍스트 문서 작성 및 편집", "category": "office"}

def register():
    return SKILL_INFO

def execute(action="write", path=None, content=None, **kwargs):
    if action == "write" and path:
        return file_write(path, content or "")
    elif action == "type" and content:
        return keyboard_type(content)
    return {"error": "지원하지 않는 동작입니다"}
