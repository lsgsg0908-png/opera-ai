"""
실행 엔진 — 마우스, 키보드, 화면, 파일, 프로세스 제어
"""
import os
import platform
import shlex

# GUI 모듈 조건부 로드 (헤드리스 환경 대응)
_pyautogui = None
_gw = None
try:
    if os.environ.get('DISPLAY') or platform.system() == 'Windows':
        import pyautogui as _pyautogui
        import pygetwindow as _gw
        _pyautogui.FAILSAFE = True
        _pyautogui.PAUSE = 0.1
except Exception:
    pass

import subprocess
import shutil
import time
import tempfile
from pathlib import Path

# 블랙리스트 디렉토리 (시스템 영역)
BLOCKED_DIRS = [
    "/System", "/etc", "/usr", "/bin", "/sbin", "/boot",
    "C:\\Windows", "C:\\System32", "C:\\Program Files",
    "/proc", "/sys", "/dev",
]

# 블랙리스트 명령어
BLOCKED_COMMANDS = [
    "rm -rf /", "rm -rf /*", "format", "dd if=", "mkfs",
    "shutdown -r", "shutdown -h", "poweroff", "reboot",
    "chmod 777 /", "chown", "passwd",
]

# ── 권한 레벨 시스템 ──
PERMISSION_SAFE = "SAFE"
PERMISSION_ADVANCED = "ADVANCED"
PERMISSION_DANGEROUS = "DANGEROUS"
PERMISSION_SYSTEM = "SYSTEM"

# 실행 함수별 권한 레벨 맵
PERMISSION_MAP = {
    # SAFE
    "mouse_move": PERMISSION_SAFE,
    "mouse_click": PERMISSION_SAFE,
    "mouse_double_click": PERMISSION_SAFE,
    "mouse_drag": PERMISSION_SAFE,
    "mouse_scroll": PERMISSION_SAFE,
    "get_mouse_position": PERMISSION_SAFE,
    "keyboard_type": PERMISSION_SAFE,
    "keyboard_press": PERMISSION_SAFE,
    "keyboard_hotkey": PERMISSION_SAFE,
    "screenshot": PERMISSION_SAFE,
    "screen_size": PERMISSION_SAFE,
    "locate_on_screen": PERMISSION_SAFE,
    "window_list": PERMISSION_SAFE,
    "get_active_window": PERMISSION_SAFE,
    "file_read": PERMISSION_SAFE,
    "file_list": PERMISSION_SAFE,
    "file_search": PERMISSION_SAFE,
    "file_copy": PERMISSION_SAFE,
    "system_info": PERMISSION_SAFE,
    "pc_status": PERMISSION_SAFE,
    "clipboard_get": PERMISSION_SAFE,
    "ocr_image": PERMISSION_SAFE,
    # ADVANCED
    "file_write": PERMISSION_ADVANCED,
    "file_delete": PERMISSION_ADVANCED,
    "window_activate": PERMISSION_ADVANCED,
    "window_minimize": PERMISSION_ADVANCED,
    "window_resize": PERMISSION_ADVANCED,
    "process_kill": PERMISSION_ADVANCED,
    "clipboard_set": PERMISSION_ADVANCED,
    "wake_on_lan": PERMISSION_ADVANCED,
    # DANGEROUS
    "process_run": PERMISSION_DANGEROUS,
    "shutdown_pc": PERMISSION_DANGEROUS,
    # SYSTEM (현재 미구현 — 레지스트리/서비스/시작프로그램)
}


def _get_permission_level(action_name):
    """실행 함수의 권한 레벨 반환"""
    return PERMISSION_MAP.get(action_name, PERMISSION_SAFE)


def _get_function_by_action(action_name):
    """액션 이름에 해당하는 함수 참조 반환 (server.py _route_action 검증용)"""
    mapping = {
        "mouse_move": mouse_move,
        "mouse_click": mouse_click,
        "mouse_double_click": mouse_double_click,
        "mouse_drag": mouse_drag,
        "mouse_scroll": mouse_scroll,
        "mouse_position": get_mouse_position,
        "keyboard_type": keyboard_type,
        "keyboard_press": keyboard_press,
        "keyboard_hotkey": keyboard_hotkey,
        "screenshot": screenshot,
        "screen_size": screen_size,
        "locate_on_screen": locate_on_screen,
        "window_list": window_list,
        "window_activate": window_activate,
        "window_minimize": window_minimize,
        "window_resize": window_resize,
        "active_window": get_active_window,
        "file_read": file_read,
        "file_write": file_write,
        "file_list": file_list,
        "file_delete": file_delete,
        "file_copy": file_copy,
        "file_search": file_search,
        "process_list": process_list,
        "process_run": process_run,
        "process_kill": process_kill,
        "system_info": system_info,
        "clipboard_get": clipboard_get,
        "clipboard_set": clipboard_set,
        "ocr": ocr_image,
        "wake_on_lan": wake_on_lan,
        "shutdown_pc": shutdown_pc,
        "pc_status": pc_status,
    }
    return mapping.get(action_name)


# ── 백업 / 롤백 시스템 ──
BACKUP_DIR = Path(__file__).parent.parent / "data" / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _auto_backup(path):
    """파일 쓰기/삭제 전 자동 백업"""
    p = Path(path)
    if not p.exists():
        return None
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{p.name}.{ts}.bak"
    backup_path = BACKUP_DIR / backup_name
    try:
        shutil.copy2(str(p), str(backup_path))
        return str(backup_path)
    except Exception:
        return None


def rollback_file(backup_path, target_path):
    """백업 파일에서 복원"""
    bp = Path(backup_path)
    tp = Path(target_path)
    if not bp.exists():
        return {"error": "backup_file_not_found"}
    try:
        shutil.copy2(str(bp), str(tp))
        return {"status": "ok", "restored": str(tp), "from": str(bp)}
    except Exception as e:
        return {"error": str(e)}


def list_backups(prefix=""):
    """백업 목록 조회"""
    backups = []
    for f in sorted(BACKUP_DIR.iterdir(), reverse=True):
        if prefix and not f.name.startswith(prefix):
            continue
        if f.suffix == ".bak":
            backups.append({
                "name": f.name,
                "path": str(f),
                "size": f.stat().st_size,
                "modified": datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            })
    return {"backups": backups[:100], "backup_dir": str(BACKUP_DIR)}


def _is_path_blocked(path):
    """차단된 경로인지 확인"""
    if not path:
        return False
    abs_path = os.path.abspath(str(path))
    for blocked in BLOCKED_DIRS:
        if abs_path.startswith(blocked):
            return True
    return False


def _is_command_blocked(cmd):
    """차단된 명령어인지 확인"""
    if not cmd:
        return False
    cmd_lower = str(cmd).lower()
    for blocked in BLOCKED_COMMANDS:
        if blocked in cmd_lower:
            return True
    return False


# ── 마우스 제어 ──

def mouse_move(x, y, duration=0.2):
    """마우스 이동"""
    if _pyautogui is None:
        return {"error": "gui_environment_not_available_headless_server", "simulated": {"x": x, "y": y}}
    _pyautogui.moveTo(x, y, duration=duration)
    return {"status": "ok", "x": x, "y": y}


def mouse_click(x=None, y=None, button="left"):
    """마우스 클릭"""
    if x is not None and y is not None:
        _pyautogui.click(x, y, button=button)
    else:
        _pyautogui.click(button=button)
    return {"status": "ok", "action": f"click_{button}"}


def mouse_double_click(x=None, y=None):
    """더블클릭"""
    if x and y:
        _pyautogui.doubleClick(x, y)
    else:
        _pyautogui.doubleClick()
    return {"status": "ok", "action": "double_click"}


def mouse_drag(start_x, start_y, end_x, end_y, duration=0.3):
    """드래그"""
    _pyautogui.drag(end_x - start_x, end_y - start_y, duration=duration)
    return {"status": "ok", "from": (start_x, start_y), "to": (end_x, end_y)}


def mouse_scroll(clicks):
    """스크롤"""
    _pyautogui.scroll(clicks)
    return {"status": "ok", "clicks": clicks}


def get_mouse_position():
    """현재 마우스 위치"""
    x, y = _pyautogui.position()
    return {"x": x, "y": y}


# ── 키보드 제어 ──

def keyboard_type(text, interval=0.01):
    """텍스트 입력"""
    _pyautogui.write(text, interval=interval)
    return {"status": "ok", "chars": len(text)}


def keyboard_press(key):
    """키 누르기 (enter, tab, esc, f1-f12 등)"""
    _pyautogui.press(key)
    return {"status": "ok", "key": key}


def keyboard_hotkey(*keys):
    """단축키 (ctrl+c, alt+tab 등)"""
    _pyautogui.hotkey(*keys)
    return {"status": "ok", "combination": "+".join(keys)}


# ── 화면 제어 ──

def screenshot(filename=None, region=None):
    """화면 캡처"""
    img = _pyautogui.screenshot(region=region)
    if filename:
        img.save(filename)
        return {"status": "ok", "file": filename, "size": img.size}
    # 임시 파일 저장
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name)
    return {"status": "ok", "file": tmp.name, "size": img.size}


def screen_size():
    """화면 해상도"""
    if _pyautogui is None:
        return {"width": 0, "height": 0, "note": "헤드리스 환경"}
    w, h = _pyautogui.size()
    return {"width": w, "height": h}


def locate_on_screen(image_path, confidence=0.9):
    """화면에서 이미지 찾기"""
    try:
        pos = _pyautogui.locateOnScreen(image_path, confidence=confidence)
        if pos:
            return {"found": True, "x": pos.left, "y": pos.top, "w": pos.width, "h": pos.height}
        return {"found": False}
    except Exception as e:
        return {"error": str(e)}


# ── 윈도우 제어 ──

def window_list():
    """실행 중인 윈도우 목록"""
    windows = _gw.getAllTitles()
    return [w for w in windows if w.strip()]


def window_activate(title):
    """윈도우 활성화"""
    try:
        win = _gw.getWindowsWithTitle(title)
        if win:
            win[0].activate()
            return {"status": "ok", "window": title}
        return {"status": "not_found", "window": title}
    except Exception as e:
        return {"error": str(e)}


def window_minimize(title):
    """윈도우 최소화"""
    try:
        win = _gw.getWindowsWithTitle(title)
        if win:
            win[0].minimize()
            return {"status": "ok"}
        return {"status": "not_found"}
    except Exception as e:
        return {"error": str(e)}


def window_resize(title, width, height):
    """윈도우 크기 변경"""
    try:
        win = _gw.getWindowsWithTitle(title)
        if win:
            win[0].resizeTo(width, height)
            return {"status": "ok"}
        return {"status": "not_found"}
    except Exception as e:
        return {"error": str(e)}


def get_active_window():
    """현재 활성화된 윈도우"""
    try:
        win = _gw.getActiveWindow()
        if win:
            return {"title": win.title, "x": win.left, "y": win.top, "w": win.width, "h": win.height}
        return {"title": ""}
    except Exception as e:
        return {"error": str(e)}


# ── 파일 시스템 ──

def file_read(path):
    """파일 읽기"""
    if _is_path_blocked(path):
        return {"error": "접근이 차단된 경로입니다"}
    try:
        p = Path(path)
        if not p.exists():
            return {"error": "file_not_found"}
        content = p.read_text(encoding="utf-8", errors="replace")
        return {"status": "ok", "content": content[:50000], "size": len(content)}
    except Exception as e:
        return {"error": str(e)}


def file_write(path, content):
    """파일 쓰기 (자동 백업 포함)"""
    if _is_path_blocked(path):
        return {"error": "접근이 차단된 경로입니다"}
    try:
        p = Path(path)
        # 자동 백업 (기존 파일이 있을 경우)
        backup_path = _auto_backup(str(p))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        result = {"status": "ok", "path": str(p), "size": len(content)}
        if backup_path:
            result["backup"] = backup_path
        return result
    except Exception as e:
        return {"error": str(e)}


def file_list(path="."):
    """디렉토리 목록"""
    if _is_path_blocked(path):
        return {"error": "접근이 차단된 경로입니다"}
    try:
        p = Path(path)
        if not p.exists():
            return {"error": "path_not_found"}
        items = []
        for item in p.iterdir():
            items.append({
                "name": item.name,
                "is_dir": item.is_dir(),
                "size": item.stat().st_size if item.is_file() else 0,
                "modified": item.stat().st_mtime,
            })
        return {"status": "ok", "path": str(p), "items": items[:100]}
    except Exception as e:
        return {"error": str(e)}


def file_delete(path):
    """파일/폴더 삭제 (자동 백업 포함, 2차 확인 필요)"""
    if _is_path_blocked(path):
        return {"error": "접근이 차단된 경로입니다"}
    try:
        p = Path(path)
        if not p.exists():
            return {"error": "file_not_found"}
        # 자동 백업
        backup_path = _auto_backup(str(p))
        if p.is_dir() and p.is_symlink():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        result = {"status": "ok", "deleted": str(p)}
        if backup_path:
            result["backup"] = backup_path
            result["rollback_hint"] = f"rollback_file('{backup_path}', '{p}')"
        return result
    except Exception as e:
        return {"error": str(e)}


def file_copy(src, dst):
    """파일 복사"""
    if _is_path_blocked(src) or _is_path_blocked(dst):
        return {"error": "접근이 차단된 경로입니다"}
    try:
        shutil.copy2(src, dst)
        return {"status": "ok", "from": src, "to": dst}
    except Exception as e:
        return {"error": str(e)}


def file_search(query, root="~"):
    """파일 내용 검색"""
    try:
        root = os.path.expanduser(root)
        results = []
        for root_dir, _, files in os.walk(root):
            if _is_path_blocked(root_dir):
                continue
            for fname in files:
                if query.lower() in fname.lower():
                    fpath = os.path.join(root_dir, fname)
                    results.append({"name": fname, "path": fpath, "size": os.path.getsize(fpath)})
                if len(results) >= 20:
                    break
            if len(results) >= 20:
                break
        return {"status": "ok", "query": query, "results": results}
    except Exception as e:
        return {"error": str(e)}


# ── 프로세스 제어 ──

def process_list():
    """실행 중인 프로세스 목록"""
    import psutil
    processes = []
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            processes.append(proc.info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return {"status": "ok", "count": len(processes), "processes": processes[:50]}


def process_run(command, timeout=30):
    """명령어 실행 (shell=False, shlex 기반 안전 실행)"""
    if _is_command_blocked(command):
        return {"error": "command_is_blocked"}
    try:
        args = shlex.split(command)
        result = subprocess.run(
            args, shell=False, capture_output=True, text=True, timeout=timeout
        )
        return {
            "status": "ok",
            "returncode": result.returncode,
            "stdout": result.stdout[:10000],
            "stderr": result.stderr[:1000],
        }
    except subprocess.TimeoutExpired:
        return {"error": "timeout_expired"}
    except Exception as e:
        return {"error": str(e)}


def process_kill(pid):
    """프로세스 종료"""
    import psutil
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        return {"status": "ok", "pid": pid}
    except Exception as e:
        return {"error": str(e)}


# ── 시스템 정보 ──

def system_info():
    """시스템 정보"""
    import psutil
    return {
        "os": platform.platform(),
        "hostname": platform.node(),
        "cpu": {
            "cores": psutil.cpu_count(),
            "usage": psutil.cpu_percent(interval=0.1),
        },
        "memory": {
            "total": psutil.virtual_memory().total,
            "available": psutil.virtual_memory().available,
            "percent": psutil.virtual_memory().percent,
        },
        "disk": {
            "total": psutil.disk_usage("/").total,
            "free": psutil.disk_usage("/").free,
            "percent": psutil.disk_usage("/").percent,
        },
        "screen": screen_size(),
    }


# ── 클립보드 ──

def clipboard_get():
    """클립보드 읽기"""
    import pyperclip
    try:
        return {"status": "ok", "content": pyperclip.paste()[:10000]}
    except Exception as e:
        return {"error": str(e)}


def clipboard_set(text):
    """클립보드 쓰기"""
    import pyperclip
    try:
        pyperclip.copy(text)
        return {"status": "ok", "size": len(text)}
    except Exception as e:
        return {"error": str(e)}


# ── OCR ──

def ocr_image(image_path, lang="kor+eng"):
    """이미지 OCR"""
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(image_path)
        text = pytesseract.image_to_string(img, lang=lang)
        return {"status": "ok", "text": text.strip()}
    except ImportError:
        return {"error": "tesseract_ocr_not_installed"}
    except Exception as e:
        return {"error": str(e)}


# ── WOL (Wake-on-LAN) ──

def wake_on_lan(mac_address, broadcast_ip="255.255.255.255", port=9):
    """WOL 매직패킷 전송
    
    Args:
        mac_address: 대상 PC MAC 주소 (XX:XX:XX:XX:XX:XX 형식)
        broadcast_ip: 브로드캐스트 IP
        port: WOL 포트 (기본 9)
    """
    import socket
    import struct
    
    # MAC 주소 정규화
    mac = mac_address.replace(":", "").replace("-", "").replace(" ", "")
    if len(mac) != 12:
        return {"error": f"MAC 주소 형식 오류: {mac_address}"}
    
    try:
        mac_bytes = bytes.fromhex(mac)
    except ValueError:
        return {"error": f"MAC 주소 변환 실패: {mac_address}"}
    
    # 매직패킷 생성: 6xFF + 16 x MAC 주소
    magic_packet = b"\xff" * 6 + mac_bytes * 16
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(magic_packet, (broadcast_ip, port))
        sock.close()
        return {"status": "ok", "mac": mac_address, "target": f"{broadcast_ip}:{port}"}
    except Exception as e:
        return {"error": f"WOL 전송 실패: {e}"}


def shutdown_pc(delay=0):
    """PC 종료 (shell=False, shlex 기반 안전 실행)"""
    import platform
    os_name = platform.system().lower()
    try:
        if os_name == "windows":
            cmd_str = f"shutdown /s /t {delay}" if delay > 0 else "shutdown /s"
        else:
            cmd_str = f"shutdown -h +{delay}" if delay > 0 else "shutdown -h now"
        args = shlex.split(cmd_str)
        subprocess.run(args, shell=False, timeout=5)
        return {"status": "ok", "action": "shutdown", "delay": delay}
    except Exception as e:
        return {"error": str(e)}


def pc_status():
    """PC 상태 정보 (WOL 대상 확인용)"""
    import psutil
    return {
        "status": "running",
        "os": platform.platform(),
        "hostname": platform.node(),
        "uptime": time.time() - psutil.boot_time(),
        "cpu_usage": psutil.cpu_percent(interval=0.1),
        "memory_percent": psutil.virtual_memory().percent,
    }


# ── GPU 감지 시스템 ──
def detect_gpu():
    """로컬 PC GPU 감지 및 등급 분류"""
    import subprocess, re
    result = {
        "available": False,
        "type": None,           # "nvidia" / "amd" / "apple" / "intel" / "none"
        "name": None,
        "vram_mb": 0,
        "grade": None,          # "high" / "mid" / "low" / "none"
        "capability": {
            "image_gen": False,
            "video_gen": False,
            "speed_estimate": None
        }
    }
    
    # 1. NVIDIA GPU 감지 (nvidia-smi)
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                          capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.strip().split(",")
            name = parts[0].strip()
            vram = int(parts[1].strip())
            result["available"] = True
            result["type"] = "nvidia"
            result["name"] = name
            result["vram_mb"] = vram
            # 등급 분류
            if vram >= 10000:
                result["grade"] = "high"
                result["capability"]["image_gen"] = True
                result["capability"]["video_gen"] = True
                result["capability"]["speed_estimate"] = f"초고속 (1~2초/장)"
            elif vram >= 6000:
                result["grade"] = "mid"
                result["capability"]["image_gen"] = True
                result["capability"]["video_gen"] = True
                result["capability"]["speed_estimate"] = f"보통 (2~5초/장)"
            else:
                result["grade"] = "low"
                result["capability"]["image_gen"] = True
                result["capability"]["video_gen"] = False
                result["capability"]["speed_estimate"] = f"저속 (5~15초/장)"
            return result
    except:
        pass
    
    # 2. AMD GPU 감지 (rocm-smi)
    try:
        r = subprocess.run(["rocm-smi", "--showproductname"],
                          capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            result["available"] = True
            result["type"] = "amd"
            result["name"] = r.stdout.strip()[:50]
            result["grade"] = "mid"
            result["capability"]["image_gen"] = True
            result["capability"]["video_gen"] = True
            result["capability"]["speed_estimate"] = "보통"
            return result
    except:
        pass
    
    # 3. Apple Silicon 감지
    import platform as _pf
    if _pf.system() == "Darwin" and _pf.machine() == "arm64":
        result["available"] = True
        result["type"] = "apple"
        result["name"] = "Apple Silicon"
        result["grade"] = "mid"
        result["capability"]["image_gen"] = True
        result["capability"]["video_gen"] = True
        result["capability"]["speed_estimate"] = "보통 (M1/M2/M3)"
        return result
    
    # 4. GPU 없음
    result["grade"] = "none"
    result["capability"]["image_gen"] = False
    result["capability"]["video_gen"] = False
    result["capability"]["speed_estimate"] = "CPU 모드 (30초~2분/장)"
    return result


def get_gpu_install_message(gpu_info):
    """GPU 등급별 설치 안내 메시지"""
    grade = gpu_info.get("grade")
    name = gpu_info.get("name", "Unknown GPU")
    
    messages = {
        "high": (
            f"✅ 감지된 GPU: {name} ({gpu_info['vram_mb']}MB VRAM)\n"
            f"이미지 생성: 초고속 (1~2초/장)\n"
            f"영상 제작: 가능 (30초 영상 약 2~3분)\n"
            f"GPU 가속 모듈(PyTorch + diffusers, 약 800MB)을 설치하시겠습니까?"
        ),
        "mid": (
            f"🟢 감지된 GPU: {name}\n"
            f"이미지 생성: 가능 (2~5초/장)\n"
            f"영상 제작: 가능 (30초 영상 약 5~10분)\n"
            f"GPU 가속 모듈을 설치하시겠습니까?"
        ),
        "low": (
            f"🟡 감지된 GPU: {name} ({gpu_info['vram_mb']}MB VRAM)\n"
            f"이미지 생성: 가능 (기본 해상도, 5~15초/장)\n"
            f"영상 제작: 제한적 (짧은 클립만 가능)\n"
            f"GPU 가속 모듈을 설치하시겠습니까? (CPU보다 약 5배 빠름)"
        ),
        "none": (
            "🔲 GPU 가속을 지원하지 않는 PC입니다.\n"
            "이미지 생성: 가능 (1장당 30초~2분 소요, CPU 모드)\n"
            "영상 제작: CPU 모드로는 권장하지 않습니다."
        ),
    }
    return messages.get(grade, messages["none"])

