"""스킬: 보고서 생성"""
import json, datetime
from core.executor import file_write

SKILL_INFO = {"id": "skill_report_gen", "name": "보고서 생성", "description": "데이터 수집 → 요약 → 문서화", "category": "office"}

def register():
    return SKILL_INFO

def execute(action="create", title="보고서", data=None, format="txt", path=None, sections=None, **kwargs):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    if format == "txt":
        content = f"= {title} =\n"
        content += f"생성일: {now}\n{'=' * 40}\n\n"
        if sections:
            for sec in sections:
                content += f"\n## {sec.get('title', '')}\n"
                content += f"{sec.get('content', '')}\n"
        elif data:
            content += json.dumps(data, indent=2, ensure_ascii=False)
        else:
            content += "(내용 없음)\n"

        if path:
            file_write(path, content)
        return {"status": "ok", "content": content, "format": format}

    if format == "html":
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:sans-serif;max-width:800px;margin:40px auto;padding:20px}}
h1{{border-bottom:2px solid #c8ff00;padding-bottom:10px}}
.sec{{margin:20px 0;padding:15px;background:#f5f5f5;border-radius:8px}}
.meta{{color:#888;font-size:14px}}</style></head><body>
<h1>{title}</h1><p class="meta">생성일: {now}</p>"""
        if sections:
            for sec in sections:
                html += f'<div class="sec"><h2>{sec.get("title","")}</h2><p>{sec.get("content","")}</p></div>'
        html += "</body></html>"

        if path:
            file_write(path, html)
        return {"status": "ok", "content": html, "format": format}

    return {"error": f"지원하지 않는 형식: {format}"}
