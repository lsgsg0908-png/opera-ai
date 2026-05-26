"""
실행 엔진 — 마우스, 키보드, 화면, 파일, 프로세스 제어
"""
import os
import platform

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
        return {"error": "GUI 환경이 아닙니다 (헤드리스 서버)", "simulated": {"x": x, "y": y}}
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
            return {"error": "파일을 찾을 수 없습니다"}
        content = p.read_text(encoding="utf-8", errors="replace")
        return {"status": "ok", "content": content[:50000], "size": len(content)}
    except Exception as e:
        return {"error": str(e)}


def file_write(path, content):
    """파일 쓰기"""
    if _is_path_blocked(path):
        return {"error": "접근이 차단된 경로입니다"}
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"status": "ok", "path": str(p), "size": len(content)}
    except Exception as e:
        return {"error": str(e)}


def file_list(path="."):
    """디렉토리 목록"""
    if _is_path_blocked(path):
        return {"error": "접근이 차단된 경로입니다"}
    try:
        p = Path(path)
        if not p.exists():
            return {"error": "경로를 찾을 수 없습니다"}
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
    """파일/폴더 삭제 (중요 작업 → 2차 확인 필요)"""
    if _is_path_blocked(path):
        return {"error": "접근이 차단된 경로입니다"}
    try:
        p = Path(path)
        if not p.exists():
            return {"error": "파일을 찾을 수 없습니다"}
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return {"status": "ok", "deleted": str(p)}
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
    """명령어 실행"""
    if _is_command_blocked(command):
        return {"error": "차단된 명령어입니다"}
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return {
            "status": "ok",
            "returncode": result.returncode,
            "stdout": result.stdout[:10000],
            "stderr": result.stderr[:1000],
        }
    except subprocess.TimeoutExpired:
        return {"error": "시간 초과"}
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
        return {"error": "Tesseract OCR이 설치되지 않았습니다"}
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
    """PC 종료"""
    import platform
    os_name = platform.system().lower()
    try:
        if os_name == "windows":
            cmd = f"shutdown /s /t {delay}" if delay > 0 else "shutdown /s"
        else:
            cmd = f"shutdown -h +{delay}" if delay > 0 else "shutdown -h now"
        subprocess.run(cmd, shell=True, timeout=5)
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
