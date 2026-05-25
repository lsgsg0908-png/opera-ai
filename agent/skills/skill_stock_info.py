"""스킬: 주식/시세/뉴스 수집"""
try:
    from tools.web_tools import search_web
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from tools.web_tools import search_web

SKILL_INFO = {"id": "skill_stock_info", "name": "주식/시세/뉴스 수집", "description": "실시간 주가, 시세, 뉴스 수집", "category": "research"}

def register():
    return SKILL_INFO

def execute(action="search", query=None, **kwargs):
    if not query:
        query = "stock market today"
    return search_web(query)
