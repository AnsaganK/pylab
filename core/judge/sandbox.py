"""
Запуск кода студента в изолированном процессе.

На Linux с bubblewrap: без сети, файловая система только для чтения,
свой /tmp, отдельные pid/ipc/uts-пространства, лимиты через prlimit.
Без bubblewrap (SANDBOX_USE_BWRAP=False) — только лимиты и таймаут,
этот режим годится лишь для проверки на своём компьютере.
"""
import math
import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

import constants
from django.utils.translation import gettext as _

IS_POSIX = os.name == "posix"

# Итоговые состояния одного запуска
OK = "ok"
TLE = "tle"
MLE = "mle"
RE = "re"
OLE = "ole"
SE = "se"


INPUT = "input"          # программа ждёт ввод (режим консоли)

WAIT_MARKER = "__PYLAB_WAITING_FOR_INPUT__"
WAIT_EXIT_CODE = 97
# Эхо введённых строк в выводе консоли: \x01<p|t>значение\x02 (p — из поля заранее, t — набрано)
ECHO_START, ECHO_END = "\x01", "\x02"

RUNNER_CODE = r'''
import builtins, os, sys, traceback
_prefilled = int(sys.argv[2])
_count = 0
_orig_input = builtins.input

def _console_input(prompt=""):
    global _count
    try:
        value = _orig_input(prompt)
    except EOFError:
        sys.stdout.flush()
        sys.stderr.write("\n__PYLAB_WAITING_FOR_INPUT__\n")
        sys.stderr.flush()
        os._exit(97)
    kind = "p" if _count < _prefilled else "t"
    _count += 1
    sys.stdout.write("\x01" + kind + value + "\x02\n")
    return value

builtins.input = _console_input
with open(sys.argv[1], encoding="utf-8") as f:
    _src = f.read()
_globals = {"__name__": "__main__", "__file__": "main.py", "__builtins__": builtins}
try:
    exec(compile(_src, "main.py", "exec"), _globals)
except SystemExit:
    raise
except BaseException as e:
    sys.stdout.flush()
    traceback.print_exception(type(e), e, e.__traceback__.tb_next)
    sys.exit(1)
'''


@dataclass
class RunResult:
    status: str
    stdout: str = ""
    stderr: str = ""
    time: float = 0.0
    exit_code: int | None = None
    notes: list = field(default_factory=list)


def _read_limited(path, limit):
    with open(path, "rb") as f:
        data = f.read(limit + 1)
    truncated = len(data) >= limit  # Python игнорирует SIGXFSZ, вывод просто обрывается на лимите
    return data[:limit].decode("utf-8", errors="replace"), truncated


def _build_command(workdir, time_limit, memory_mb, console_prefilled=None):
    out_limit = constants.OUTPUT_LIMIT_KB * 1024
    cpu = math.ceil(time_limit) + 1
    python = constants.SANDBOX_PYTHON

    limits = []
    prlimit = shutil.which("prlimit") if IS_POSIX else None
    if prlimit:
        limits = [
            prlimit,
            f"--as={memory_mb * 1024 * 1024}",
            f"--cpu={cpu}:{cpu + 1}",
            f"--fsize={out_limit}",
            f"--nofile=64",
            f"--nproc={constants.MAX_PROCESSES}",
            f"--core=0",
            "--",
        ]

    if constants.SANDBOX_USE_BWRAP:
        cmd = [
            constants.SANDBOX_BWRAP,
            "--ro-bind", "/usr", "/usr",
            "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64",
            "--symlink", "usr/bin", "/bin",
            "--ro-bind-try", "/etc/ld.so.cache", "/etc/ld.so.cache",
            "--ro-bind-try", "/etc/localtime", "/etc/localtime",
            "--ro-bind-try", "/etc/alternatives", "/etc/alternatives",
            "--ro-bind", str(workdir), "/sandbox",
            "--tmpfs", "/tmp",
            "--proc", "/proc",
            "--dev", "/dev",
            # корень песочницы — только чтение; писать можно лишь в свой /tmp
            "--remount-ro", "/",
            "--chdir", "/sandbox",
            "--unshare-all",
            "--hostname", "sandbox",
            "--die-with-parent",
            "--new-session",
            "--clearenv",
            "--setenv", "PATH", "/usr/bin",
            "--setenv", "HOME", "/tmp",
            "--setenv", "LANG", "C.UTF-8",
        ]
        if console_prefilled is None:
            cmd += limits + [python, "-I", "-X", "utf8", "/sandbox/main.py"]
        else:
            cmd += limits + [python, "-I", "-X", "utf8", "/sandbox/runner.py", "/sandbox/main.py", str(console_prefilled)]
        if constants.SANDBOX_RUN_AS:
            cmd = ["sudo", "-n", "-u", constants.SANDBOX_RUN_AS] + cmd
        return cmd, None

    # Режим без изоляции
    if console_prefilled is None:
        return limits + [python, "-I", "-X", "utf8", os.path.join(workdir, "main.py")], workdir
    return limits + [python, "-I", "-X", "utf8", os.path.join(workdir, "runner.py"),
                     os.path.join(workdir, "main.py"), str(console_prefilled)], workdir


def run_program(code: str, stdin_text: str, time_limit: float, memory_mb: int,
                console_prefilled=None) -> RunResult:
    """console_prefilled=None — обычный запуск (проверка решений).
    Число — режим консоли для кнопки «Запустить»: столько первых строк ввода
    пришли из поля «Ввод заранее»; когда ввод кончается, программа не падает
    с EOFError, а возвращает статус INPUT («жду ввод»)."""
    os.makedirs(constants.SANDBOX_TMP_DIR, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="run_", dir=constants.SANDBOX_TMP_DIR)
    try:
        os.chmod(workdir, 0o755)
        main_py = os.path.join(workdir, "main.py")
        with open(main_py, "w", encoding="utf-8") as f:
            f.write(code)
        os.chmod(main_py, 0o644)
        if console_prefilled is not None:
            runner_py = os.path.join(workdir, "runner.py")
            with open(runner_py, "w", encoding="utf-8") as f:
                f.write(RUNNER_CODE)
            os.chmod(runner_py, 0o644)
        # Ввод/вывод — файлы, открытые сервером: программа их только наследует
        io_dir = tempfile.mkdtemp(prefix="io_", dir=constants.SANDBOX_TMP_DIR)
        in_path = os.path.join(io_dir, "in.txt")
        out_path = os.path.join(io_dir, "out.txt")
        err_path = os.path.join(io_dir, "err.txt")
        with open(in_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(stdin_text.replace("\r\n", "\n"))

        cmd, cwd = _build_command(workdir, time_limit, memory_mb, console_prefilled)
        wall_limit = time_limit + 0.5
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": workdir}
        if not IS_POSIX:
            env = None

        timed_out = False
        started = time.monotonic()
        with open(in_path, "rb") as fin, open(out_path, "wb") as fout, open(err_path, "wb") as ferr:
            try:
                proc = subprocess.Popen(
                    cmd, stdin=fin, stdout=fout, stderr=ferr, cwd=cwd, env=env,
                    start_new_session=IS_POSIX,
                )
            except FileNotFoundError as e:
                return RunResult(SE, stderr=_("Не найден исполняемый файл: %(f)s. Проверьте constants.py.") % {"f": e.filename})
            try:
                proc.wait(timeout=wall_limit)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    if IS_POSIX:
                        os.killpg(proc.pid, signal.SIGKILL)
                    else:
                        proc.kill()
                except ProcessLookupError:
                    pass
                proc.wait()
        elapsed = time.monotonic() - started

        out_limit = constants.OUTPUT_LIMIT_KB * 1024
        stdout, out_trunc = _read_limited(out_path, out_limit)
        stderr, _ = _read_limited(err_path, 16 * 1024)
        # В трассировке показываем просто main.py, без путей сервера
        stderr = stderr.replace(os.path.join(workdir, "main.py"), "main.py").replace("/sandbox/main.py", "main.py")
        shutil.rmtree(io_dir, ignore_errors=True)

        rc = proc.returncode
        sig = None
        if rc is not None and rc < 0:
            sig = -rc
        elif constants.SANDBOX_USE_BWRAP and rc is not None and rc > 128:
            sig = rc - 128  # bwrap отдаёт 128+номер сигнала

        res = RunResult(OK, stdout=stdout, stderr=stderr, time=round(elapsed, 3), exit_code=rc)

        if console_prefilled is not None and rc == WAIT_EXIT_CODE and WAIT_MARKER in stderr:
            res.status = INPUT
            res.stderr = stderr.replace(WAIT_MARKER, "").strip("\n")
            return res

        if constants.SANDBOX_USE_BWRAP and rc == 1 and stderr.startswith("bwrap:"):
            res.status = SE
            res.notes.append(_("bubblewrap не смог создать песочницу — см. раздел «Песочница» в README."))
        elif timed_out or elapsed > time_limit or sig in (getattr(signal, "SIGXCPU", -1),) or \
                (sig == getattr(signal, "SIGKILL", -1)):
            res.status = TLE
        elif sig == getattr(signal, "SIGXFSZ", -1) or out_trunc:
            res.status = OLE
        elif "MemoryError" in stderr:
            res.status = MLE
        elif rc != 0:
            res.status = RE
        return res
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


SELFTEST_CODE = r'''
import os, socket, sys
print("python", sys.version.split()[0])
try:
    s = socket.create_connection(("1.1.1.1", 53), timeout=2)
    print("NETWORK: OPEN")
except Exception:
    print("NETWORK: BLOCKED")
# пробуем писать туда, где лежит программа (на сервере это каталог проекта)
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pylab_probe"), "w") as f:
        f.write("x")
    print("HOMEWRITE: OPEN")
except Exception:
    print("HOMEWRITE: BLOCKED")
try:
    with open("/pylab_probe", "w") as f:
        f.write("x")
    print("ROOTWRITE: OPEN")
except Exception:
    print("ROOTWRITE: BLOCKED")
print("HOME_DIRS:", "VISIBLE" if os.path.isdir("/home") and os.listdir("/home") else "HIDDEN")
print(int(input()) * 2)
'''


def selftest():
    """Проверки для страницы «Песочница»: список (название, прошло, пояснение)."""
    r = run_program(SELFTEST_CODE, "21\n", 5, constants.DEFAULT_MEMORY_LIMIT)
    out = r.stdout
    checks = [
        (_("Программа запускается и читает ввод"), r.status == OK and out.strip().endswith("42"),
         (r.stderr or "; ".join(r.notes) or out)[:500]),
        (_("Нет доступа в сеть"), "NETWORK: BLOCKED" in out, ""),
        (_("Нельзя писать в файлы сервера"), "HOMEWRITE: BLOCKED" in out and "ROOTWRITE: BLOCKED" in out, ""),
        (_("Каталоги пользователей скрыты"), "HOME_DIRS: HIDDEN" in out, ""),
    ]
    t = run_program("while True: pass", "", 1, constants.DEFAULT_MEMORY_LIMIT)
    checks.append((_("Бесконечный цикл останавливается по времени"), t.status == TLE, f"{t.time} " + _("с")))
    m = run_program("a = [0] * (10**9)", "", 2, 64)
    checks.append((_("Лимит памяти работает"), m.status == MLE, m.status))
    return {
        "mode": "bubblewrap" if constants.SANDBOX_USE_BWRAP else "none",
        "run_as": constants.SANDBOX_RUN_AS or _("пользователь сервера"),
        "checks": checks,
        "all_ok": all(c[1] for c in checks),
    }
