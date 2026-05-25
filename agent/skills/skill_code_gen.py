"""스킬: 코딩·스크립트 자동 생성"""
from core.executor import process_run, file_write
import tempfile, os

SKILL_INFO = {"id": "skill_code_gen", "name": "코딩·스크립트 자동 생성", "description": "Python/JS 코드 생성 및 실행", "category": "dev"}

def register():
    return SKILL_INFO

def execute(action="run", code=None, language="python", path=None, **kwargs):
    if action == "generate" and not code:
        return {
            "status": "generation_request",
            "note": "AI가 코드를 생성합니다. 완료 후 전달드립니다."
        }
    if action == "run" and code:
        if language == "python":
            tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w")
            tmp.write(code)
            tmp.close()
            result = process_run(f"python3 {tmp.name}", timeout=15)
            os.unlink(tmp.name)
            return result
        return {"error": f"지원하지 않는 언어: {language}"}
    if action == "save" and code and path:
        return file_write(path, code)
    return {"error": "코드 또는 동작이 필요합니다"}
