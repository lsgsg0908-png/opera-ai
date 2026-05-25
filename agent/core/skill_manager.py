"""
스킬 관리자 — 스킬 로드, 실행, 제한 관리
"""
import importlib
import inspect
import os
import sys
from pathlib import Path
SKILLS_DIR = Path(__file__).parent.parent / "skills"

SKILLS_DIR = Path(__file__).parent.parent / "skills"


class SkillRegistry:
    def __init__(self):
        self._skills = {}
        self._load_skills()

    def _load_skills(self):
        """스킬 디렉토리에서 모든 스킬 로드"""
        if not SKILLS_DIR.exists():
            SKILLS_DIR.mkdir(parents=True)

        # skills 디렉토리를 sys.path에 추가
        skills_parent = str(SKILLS_DIR.parent)
        if skills_parent not in sys.path:
            sys.path.insert(0, skills_parent)

        for fname in sorted(os.listdir(SKILLS_DIR)):
            if fname.startswith("skill_") and fname.endswith(".py"):
                skill_id = fname.replace(".py", "")
                try:
                    module = importlib.import_module(f"skills.{skill_id}")
                    if hasattr(module, "register"):
                        skill_info = module.register()
                        self._skills[skill_info["id"]] = {
                            "id": skill_info["id"],
                            "name": skill_info["name"],
                            "description": skill_info.get("description", ""),
                            "module": module,
                            "function": getattr(module, "execute", None),
                            "category": skill_info.get("category", "general"),
                        }
                except Exception as e:
                    print(f"  [!] 스킬 로드 실패: {fname} - {e}")

    def get_all_skills(self):
        """모든 스킬 목록"""
        return [
            {"id": s["id"], "name": s["name"], "description": s["description"], "category": s["category"]}
            for s in self._skills.values()
        ]

    def get_skill(self, skill_id):
        """특정 스킬 정보"""
        return self._skills.get(skill_id)

    def execute(self, skill_id, **kwargs):
        """스킬 실행"""
        skill = self._skills.get(skill_id)
        if not skill:
            return {"error": f"스킬 '{skill_id}'을(를) 찾을 수 없습니다"}
        if skill["function"] is None:
            return {"error": f"스킬 '{skill_id}'에 실행 함수가 없습니다"}
        try:
            result = skill["function"](**kwargs)
            return result
        except Exception as e:
            return {"error": f"스킬 실행 오류: {str(e)}"}

    def get_skill_names(self):
        """스킬 이름 목록"""
        return [s["name"] for s in self._skills.values()]


# 싱글톤
_registry = None


def get_registry():
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
    return _registry
