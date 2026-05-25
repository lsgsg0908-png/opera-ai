"""스킬: 문법·맞춤법 검사"""
SKILL_INFO = {"id": "skill_grammar", "name": "문법·맞춤법 검사", "description": "한국어/영어 문법 및 맞춤법 검사", "category": "utility"}

def register():
    return SKILL_INFO

def execute(text="", language="ko", **kwargs):
    if not text:
        return {"error": "검사할 텍스트가 필요합니다"}
    # LLM에 검사 요청 전달
    return {
        "status": "grammar_check_request",
        "text": text[:5000],
        "language": language,
        "note": "AI가 문법/맞춤법을 검사합니다..."
    }
