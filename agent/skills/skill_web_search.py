"""스킬: 웹 검색·정보 수집"""
import requests
import re
from bs4 import BeautifulSoup

SKILL_INFO = {"id": "skill_web_search", "name": "웹 검색·정보 수집", "description": "웹 검색 및 페이지 데이터 수집", "category": "research"}

def register():
    return SKILL_INFO

def execute(action="fetch", url=None, query=None, **kwargs):
    if action == "fetch" and url:
        try:
            resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text(separator="\n", strip=True)
            text = re.sub(r'\n{3,}', '\n\n', text)
            return {"status": "ok", "url": url, "content": text[:10000], "length": len(text)}
        except Exception as e:
            return {"error": str(e)}

    if action == "search" and query:
        try:
            resp = requests.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            for r in soup.select(".result__body")[:5]:
                link_el = r.select_one("a")
                snippet_el = r.select_one(".result__snippet")
                if link_el:
                    href = link_el.get("href", "")
                    title = link_el.get_text(strip=True)
                    snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                    results.append({"title": title, "url": href, "snippet": snippet})
            return {"status": "ok", "query": query, "results": results}
        except Exception as e:
            return {"error": str(e)}

    return {"error": "URL 또는 검색어가 필요합니다"}
