"""스킬: PDF 읽고 요약"""
import subprocess, tempfile, os
from pathlib import Path

SKILL_INFO = {"id": "skill_pdf_summary", "name": "PDF 읽고 요약", "description": "PDF 텍스트 추출 → AI 요약", "category": "research"}

def register():
    return SKILL_INFO

def execute(action="read", path=None, **kwargs):
    if not path or not Path(path).exists():
        return {"error": "PDF 파일 경로가 필요합니다"}
    try:
        # pdftotext 사용
        result = subprocess.run(
            ["pdftotext", path, "-"],
            capture_output=True, text=True, timeout=30
        )
        text = result.stdout or "PDF 텍스트를 추출할 수 없습니다"
        return {
            "status": "ok",
            "path": path,
            "text": text[:30000],
            "length": len(text),
            "note": "요약이 필요하면 '이 PDF를 요약해줘'라고 말씀해주세요"
        }
    except FileNotFoundError:
        try:
            # PyMuPDF fallback
            import fitz
            doc = fitz.open(path)
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            return {"status": "ok", "path": path, "text": text[:30000], "length": len(text)}
        except ImportError:
            return {"error": "PDF 읽기 도구가 필요합니다 (poppler-utils 또는 PyMuPDF)"}
    except Exception as e:
        return {"error": str(e)}

def execute_summary(text, max_length=500):
    """텍스트 요약"""
    return {
        "status": "summarize_request",
        "text": text[:10000],
        "max_length": max_length,
        "note": "AI 요약 처리 중…"
    }
