"""스킬: OCR (이미지→텍스트)"""
from core.executor import screenshot, ocr_image

SKILL_INFO = {"id": "skill_ocr", "name": "OCR (이미지→텍스트)", "description": "이미지/화면 캡처 → 텍스트 변환", "category": "utility"}

def register():
    return SKILL_INFO

def execute(action="capture", image_path=None, region=None, lang="kor+eng", **kwargs):
    if action == "capture":
        # 화면 캡처
        result = screenshot(region=region)
        if result.get("status") != "ok":
            return result
        # OCR
        ocr_result = ocr_image(result["file"], lang=lang)
        if ocr_result.get("status") == "ok":
            return {"status": "ok", "text": ocr_result["text"], "image": result["file"]}
        return ocr_result

    if action == "ocr" and image_path:
        ocr_result = ocr_image(image_path, lang=lang)
        return ocr_result

    return {"error": "OCR 대상이 필요합니다 (이미지 경로 또는 캡처 명령)"}
