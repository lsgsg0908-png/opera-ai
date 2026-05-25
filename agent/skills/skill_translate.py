"""스킬: 번역"""
SKILL_INFO = {"id": "skill_translate", "name": "번역", "description": "다국어 번역 (한/영/중/일)", "category": "utility"}

def register():
    return SKILL_INFO

def execute(action="translate", text="", source="auto", target="ko", **kwargs):
    if not text:
        return {"error": "번역할 텍스트가 필요합니다"}
    # 번역 요청을 LLM에 전달하는 방식으로 프롬프트 생성
    return {
        "status": "translation_request",
        "text": text,
        "source": source,
        "target": target,
        "note": "AI 번역 처리 중… (DeepSeek API에서 처리)"
    }
