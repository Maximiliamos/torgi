#!/usr/bin/env python3
"""
BankrotAI System Health Check & Integration Test Suite
Запуск: python run_system_check.py
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

# Цвета для вывода (эмуляция для Windows)
try:
    import colorama
    colorama.init()
    GREEN = colorama.Fore.GREEN
    RED = colorama.Fore.RED
    YELLOW = colorama.Fore.YELLOW
    RESET = colorama.Style.RESET_ALL
except ImportError:
    # Если colorama нет, попробуем стандартные ANSI (в новых Win терминалах работают)
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    RESET = '\033[0m'

def run_cmd(cmd: list[str], cwd=None, env=None):
    """Выполнить команду и вернуть (success, stdout, stderr)."""
    try:
        # Используем sys.executable для запуска python-модулей
        if cmd and cmd[0] == "python":
            cmd = [sys.executable, *cmd[1:]]
        elif cmd:
            resolved = shutil.which(cmd[0]) or shutil.which(f"{cmd[0]}.cmd") or shutil.which(f"{cmd[0]}.exe")
            if resolved:
                cmd = [resolved, *cmd[1:]]

        result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=env, timeout=30)
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Command timed out"
    except Exception as e:
        return False, "", str(e)

def check_module(name, success, details=""):
    symbol = f"{GREEN}OK{RESET}" if success else f"{RED}FAIL{RESET}"
    print(f"  {symbol} {name} {details}")
    return success

def main():
    print(f"{YELLOW}=== BankrotAI System Integration Test Suite ==={RESET}\n")
    project_root = Path(__file__).parent.absolute()
    # Добавляем src в PYTHONPATH
    env = os.environ.copy()
    src_path = str(project_root / "src")
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = src_path + os.pathsep + env["PYTHONPATH"]
    else:
        env["PYTHONPATH"] = src_path
    
    overall = True
    queue_fallback_ready = False

    # 1. Проверка окружения
    print(f"{YELLOW}[1] Environment & Dependencies{RESET}")
    env_success = True
    for cmd in (["python", "--version"], ["node", "--version"], ["npm", "--version"]):
        ok, out, _ = run_cmd(cmd)
        env_success &= ok
        check_module(cmd[0], ok, out.strip() if ok else "Not found")
    overall &= env_success

    # 2. Проверка БД и Alembic
    print(f"\n{YELLOW}[2] Database & Migrations{RESET}")
    # Проверка наличия файла конфигурации alembic.ini
    alembic_ini = project_root / "alembic.ini"
    ok_ini = alembic_ini.exists()
    overall &= check_module("alembic.ini exists", ok_ini)

    # Проверка актуальности миграций (alembic check)
    # Используем python -m alembic для надежности
    ok_mig, out, err = run_cmd(["python", "-m", "alembic", "upgrade", "head"], cwd=project_root, env=env)
    overall &= check_module("Migrations up-to-date", ok_mig, (out if ok_mig else err)[:100])

    # 3. Проверка Redis Connection
    print(f"\n{YELLOW}[3] Redis Connection{RESET}")
    # Используем redis-cli ping если доступен
    ok_redis, out, _ = run_cmd(["redis-cli", "-h", "localhost", "ping"])
    if not ok_redis:
        # Попробуем через Python
        try:
            import redis
            # 1. Пробуем localhost
            try:
                r_local = redis.Redis(host='localhost', port=6379, db=0, socket_connect_timeout=2)
                if r_local.ping():
                    ok_redis = True
                    out = "PONG (via localhost)"
            except:
                ok_redis = False
            
            # 2. Если не вышло, пробуем из конфига (если он есть)
            if not ok_redis:
                try:
                    sys.path.insert(0, src_path)
                    from bankrotai.core import get_settings
                    # Check if redis_url exists in AppSettings, if not use default
                    settings = get_settings()
                    redis_url = getattr(settings, "redis_url", "redis://localhost:6379/0")
                    r_cfg = redis.from_url(redis_url, socket_connect_timeout=2)
                    if r_cfg.ping():
                        ok_redis = True
                        out = f"PONG (via {redis_url})"
                except:
                    pass
        except Exception as e:
            ok_redis = False
            out = str(e)
    if not ok_redis:
        try:
            if src_path not in sys.path:
                sys.path.insert(0, src_path)
            from bankrotai.tasks import broker_is_available

            queue_fallback_ready = not broker_is_available()
        except Exception:
            queue_fallback_ready = False

    queue_ok = ok_redis or queue_fallback_ready
    queue_details = out.strip() if ok_redis else ("Thread fallback active" if queue_fallback_ready else "Not responding")
    overall &= check_module("Redis available or thread fallback", queue_ok, queue_details)

    # 4. Проверка API (запуск и health endpoint)
    print(f"\n{YELLOW}[4] API Server{RESET}")
    # Попробуем запустить uvicorn в фоне на другом порту для теста
    api_port = "8005"
    api_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "bankrotai.api:app", "--host", "127.0.0.1", "--port", api_port],
        cwd=project_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env
    )
    
    time.sleep(5) # Дадим больше времени на запуск
    # Проверим health endpoint
    ok_api = False
    api_details = ""
    try:
        with urlopen(f"http://127.0.0.1:{api_port}/health", timeout=5) as response:
            ok_api = response.status == 200
            if ok_api:
                api_details = response.read().decode("utf-8")
    except Exception as e:
        api_details = str(e)
    
    api_proc.terminate()
    try:
        api_proc.wait(timeout=5)
    except:
        api_proc.kill()
        
    overall &= check_module("API /health responds", ok_api, api_details)

    # 5. Проверка Celery Worker
    print(f"\n{YELLOW}[5] Celery Worker{RESET}")
    # Попробуем запустить celery inspect ping
    # Путь к celery может быть через python -m celery
    ok_celery, out, _ = run_cmd(["python", "-m", "celery", "-A", "bankrotai.tasks:celery_app", "inspect", "ping"], env=env)
    if not ok_celery:
        # Запустим worker на несколько секунд и проверим
        celery_proc = subprocess.Popen(
            [sys.executable, "-m", "celery", "-A", "bankrotai.tasks:celery_app", "worker", "--loglevel=error", "--concurrency=1"],
            cwd=project_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env
        )
        time.sleep(5)
        ok_celery, out, _ = run_cmd(["python", "-m", "celery", "-A", "bankrotai.tasks:celery_app", "inspect", "ping"], env=env)
        celery_proc.terminate()
        try:
            celery_proc.wait(timeout=5)
        except:
            celery_proc.kill()
            
    celery_ok = ok_celery or queue_fallback_ready
    celery_details = out[:100] if ok_celery else ("Thread fallback active" if queue_fallback_ready else "No worker responding")
    overall &= check_module("Celery worker responds or thread fallback", celery_ok, celery_details)

    # 6. Проверка AI модуля
    print(f"\n{YELLOW}[6] AI Appraiser Module{RESET}")
    try:
        if src_path not in sys.path:
            sys.path.insert(0, src_path)
        from bankrotai.ai import OpenAIAppraiser
        ok_ai = True
        ai_details = "OpenAIAppraiser class imported successfully"
    except ImportError as e:
        ok_ai = False
        ai_details = str(e)
    overall &= check_module("Appraiser import", ok_ai, ai_details)

    # 7. GUI Dependencies Check
    print(f"\n{YELLOW}[7] GUI Environment (PySide6){RESET}")
    try:
        import PySide6
        overall &= check_module("PySide6", True, f"Version {PySide6.__version__}")
    except ImportError:
        overall &= check_module("PySide6", False, "PySide6 not installed")

    # 8. Pytest существующих тестов
    print(f"\n{YELLOW}[8] Unit Tests (pytest){RESET}")
    # Используем python -m pytest
    ok_tests, out, err = run_cmd(["python", "-m", "pytest", "tests/", "-q", "--tb=short"], cwd=project_root, env=env)
    overall &= check_module("Pytest suite", ok_tests, "All tests passed" if ok_tests else (err[:200] or out[:200]))

    # Итог
    status_text = f"{GREEN}PASS{RESET}" if overall else f"{RED}FAIL{RESET}"
    print(f"\n{YELLOW}=== Overall Status: {status_text} ==={RESET}")
    sys.exit(0 if overall else 1)

if __name__ == "__main__":
    main()
