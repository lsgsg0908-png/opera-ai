"""스킬: 시스템 정보 조회"""
from core.executor import system_info

SKILL_INFO = {"id": "skill_sys_info", "name": "시스템 정보 조회", "description": "CPU/메모리/디스크/프로세스 현황", "category": "utility"}

def register():
    return SKILL_INFO

def execute(**kwargs):
    return system_info()
