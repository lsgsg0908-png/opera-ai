"""
스케줄러 — 자동화 루틴 예약 실행
"""
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
ROUTINE_FILE = DATA_DIR / "routines.json"

# 플랜별 루틴 한도
MAX_ROUTINES = {
    "trial": 3,
    "basic": 0,
    "pro": 5,
    "enterprise": 9999,
}


def _load_routines():
    if ROUTINE_FILE.exists():
        with open(ROUTINE_FILE) as f:
            return json.load(f)
    return {"routines": []}


def _save_routines(data):
    ROUTINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ROUTINE_FILE, "w") as f:
        json.dump(data, f, indent=2)


class Scheduler:
    def __init__(self):
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._worker_thread = threading.Thread(target=self._check_loop, daemon=True)
        self._worker_thread.start()

    def _check_loop(self):
        """1분마다 루틴 체크"""
        while not self._stop.is_set():
            try:
                self._check_routines()
            except Exception:
                pass
            time.sleep(60)

    def _check_routines(self):
        """실행해야 할 루틴 확인"""
        with self._lock:
            data = _load_routines()
            now = datetime.now()
            triggered = []

            for routine in data.get("routines", []):
                if routine.get("disabled"):
                    continue

                last_run = routine.get("last_run")
                interval = routine.get("interval_minutes", 0)

                if last_run is None:
                    # 첫 실행
                    triggered.append(routine)
                    continue

                last = datetime.fromisoformat(last_run)
                if (now - last).total_seconds() >= interval * 60:
                    triggered.append(routine)

            for routine in triggered:
                routine["last_run"] = now.isoformat()

            _save_routines(data)

        # 루틴 실행 (lock 밖)
        for routine in triggered:
            self._execute_routine(routine)

    def _execute_routine(self, routine):
        """루틴 실행"""
        try:
            from core.task_queue import get_queue
            queue = get_queue()

            for step in routine.get("steps", []):
                queue.enqueue(
                    action=step.get("action", ""),
                    params=step.get("params", {}),
                    priority="normal",
                    description=f"루틴: {routine.get('name', '')} - {step.get('action', '')}"
                )
        except Exception as e:
            pass

    def get_routines(self):
        """등록된 루틴 목록"""
        with self._lock:
            data = _load_routines()
            return data["routines"]

    def add_routine(self, name, steps, interval_minutes, plan="basic"):
        """루틴 추가"""
        with self._lock:
            data = _load_routines()
            limit = MAX_ROUTINES.get(plan, 0)

            if limit > 0 and len(data["routines"]) >= limit:
                return {"error": f"{plan} 플랜은 최대 {limit}개 루틴만 등록 가능합니다"}

            if plan == "basic" and limit == 0:
                return {"error": "Basic 플랜은 자동화 루틴을 지원하지 않습니다"}

            routine = {
                "id": f"rtn_{len(data['routines']) + 1}",
                "name": name,
                "steps": steps,
                "interval_minutes": interval_minutes,
                "disabled": False,
                "last_run": None,
                "created_at": datetime.now().isoformat(),
            }
            data["routines"].append(routine)
            _save_routines(data)
            return {"status": "ok", "routine": routine}

    def remove_routine(self, routine_id):
        """루틴 삭제"""
        with self._lock:
            data = _load_routines()
            data["routines"] = [r for r in data["routines"] if r["id"] != routine_id]
            _save_routines(data)
        return {"status": "ok"}

    def disable_routine(self, routine_id):
        """루틴 비활성화"""
        with self._lock:
            data = _load_routines()
            for r in data["routines"]:
                if r["id"] == routine_id:
                    r["disabled"] = True
            _save_routines(data)
        return {"status": "ok"}

    def get_limit(self, plan):
        """루틴 한도 확인"""
        return {"limit": MAX_ROUTINES.get(plan, 0), "plan": plan}


# 싱글톤
_scheduler = None


def get_scheduler():
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler
