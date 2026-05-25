"""
작업 큐 — 동시 작업 제한, 우선순위, 상태 관리
"""
import json
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from enum import Enum

DATA_DIR = Path(__file__).parent.parent / "data"
TASK_FILE = DATA_DIR / "tasks.json"

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

# 플랜별 동시 작업 수
MAX_CONCURRENT = {
    "trial": 3,
    "basic": 1,
    "pro": 3,
    "enterprise": 10,
}

# 작업 우선순위
PRIORITIES = {
    "low": 0,
    "normal": 1,
    "high": 2,
    "urgent": 3,
}


def _load_tasks():
    if TASK_FILE.exists():
        with open(TASK_FILE) as f:
            return json.load(f)
    return {"tasks": [], "queue": [], "running": [], "next_id": 1}


def _save_tasks(data):
    TASK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TASK_FILE, "w") as f:
        json.dump(data, f, indent=2)


class TaskQueue:
    def __init__(self):
        self._lock = threading.Lock()
        self._running = {}
        self._data = _load_tasks()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def _worker_loop(self):
        """백그라운드 워커 — 큐 처리"""
        while True:
            try:
                self._process_queue()
            except Exception as e:
                pass
            time.sleep(1)

    def _process_queue(self):
        """큐에서 작업 처리"""
        with self._lock:
            plan = self._get_plan()
            max_concurrent = MAX_CONCURRENT.get(plan, 1)
            available = max_concurrent - len(self._data["running"])

            if available <= 0:
                return

            # 우선순위 정렬
            self._data["queue"].sort(key=lambda t: (
                -PRIORITIES.get(t.get("priority", "normal"), 1),
                t.get("created_at", "")
            ))

            # 실행 가능한 작업
            to_run = []
            for task in self._data["queue"][:available]:
                task["status"] = TaskStatus.RUNNING.value
                task["started_at"] = datetime.now().isoformat()
                to_run.append(task)
                self._data["running"].append(task["id"])

            self._data["queue"] = [
                t for t in self._data["queue"]
                if t["id"] not in [r["id"] for r in to_run]
            ]
            _save_tasks(self._data)

        # 실제 작업 실행 (lock 밖에서)
        for task in to_run:
            thread = threading.Thread(
                target=self._execute_task,
                args=(task,),
                daemon=True
            )
            thread.start()

    def _execute_task(self, task):
        """개별 작업 실행"""
        try:
            action = task.get("action", "")
            params = task.get("params", {})
            # executor 호출
            from core.executor import (
                file_read, file_write, file_list, file_delete, file_copy, file_search,
                system_info, clipboard_get, clipboard_set,
                process_list, process_run,
                screen_size, get_active_window, window_list,
            )
            actions = {
                "file_read": lambda: file_read(params.get("path", "")),
                "file_write": lambda: file_write(params.get("path", ""), params.get("content", "")),
                "file_list": lambda: file_list(params.get("path", ".")),
                "file_delete": lambda: file_delete(params.get("path", "")),
                "file_search": lambda: file_search(params.get("query", ""), params.get("root", "~")),
                "system_info": system_info,
                "process_list": process_list,
                "process_run": lambda: process_run(params.get("command", ""), params.get("timeout", 30)),
                "clipboard_get": clipboard_get,
                "clipboard_set": lambda: clipboard_set(params.get("text", "")),
                "window_list": window_list,
                "screen_size": screen_size,
            }
            handler = actions.get(action)
            if handler:
                result = handler()
            else:
                result = {"error": f"unknown action: {action}"}

            with self._lock:
                self._data["tasks"].append({
                    "id": task["id"],
                    "action": action,
                    "result": str(result)[:500],
                    "completed_at": datetime.now().isoformat(),
                    "status": TaskStatus.COMPLETED.value,
                })
                self._data["running"] = [r for r in self._data["running"] if r != task["id"]]
                _save_tasks(self._data)

        except Exception as e:
            with self._lock:
                self._data["tasks"].append({
                    "id": task["id"],
                    "action": task.get("action", ""),
                    "error": str(e),
                    "completed_at": datetime.now().isoformat(),
                    "status": TaskStatus.FAILED.value,
                })
                self._data["running"] = [r for r in self._data["running"] if r != task["id"]]
                _save_tasks(self._data)

    def _get_plan(self):
        """현재 플랜 확인"""
        from core.token_manager import _load_config
        config = _load_config()
        return config.get("plan", "trial")

    def enqueue(self, action, params=None, priority="normal", description=""):
        """작업 큐에 추가"""
        with self._lock:
            task_id = str(uuid.uuid4())[:8]
            now = datetime.now().isoformat()
            task = {
                "id": task_id,
                "action": action,
                "params": params or {},
                "priority": priority,
                "description": description,
                "status": TaskStatus.PENDING.value,
                "created_at": now,
            }
            self._data["queue"].append(task)
            _save_tasks(self._data)
        return {"task_id": task_id, "status": "queued"}

    def get_status(self):
        """작업 큐 상태"""
        with self._lock:
            plan = self._get_plan()
            max_conc = MAX_CONCURRENT.get(plan, 1)
            return {
                "queue_length": len(self._data["queue"]),
                "running": len(self._data["running"]),
                "max_concurrent": max_conc,
                "available": max_conc - len(self._data["running"]),
                "completed_today": len(self._data["tasks"]),
            }

    def cancel(self, task_id):
        """작업 취소"""
        with self._lock:
            self._data["queue"] = [t for t in self._data["queue"] if t["id"] != task_id]
            _save_tasks(self._data)
        return {"status": "cancelled" if task_id else "not_found"}

    def get_history(self, limit=50):
        """작업 이력"""
        with self._lock:
            recent = self._data["tasks"][-limit:]
            recent.reverse()
            return recent


# 싱글톤
_task_queue = None

def get_queue():
    global _task_queue
    if _task_queue is None:
        _task_queue = TaskQueue()
    return _task_queue
