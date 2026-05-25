"""웹 도구 — 검색, fetch 등"""
import requests
from bs4 import BeautifulSoup
import re

def search_web(query, max_results=5):
    """간단 웹 검색"""
    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for r in soup.select(".result__body")[:max_results]:
            link = r.select_one("a")
            snippet = r.select_one(".result__snippet")
            if link:
                results.append({
                    "title": link.get_text(strip=True),
                    "url": link.get("href", ""),
                    "snippet": snippet.get_text(strip=True) if snippet else ""
                })
        return {"status": "ok", "query": query, "results": results}
    except Exception as e:
        return {"error": str(e)}


def fetch_url(url):
    """URL 내용 가져오기"""
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return {"status": "ok", "url": url, "content": text[:15000]}
    except Exception as e:
        return {"error": str(e)}
