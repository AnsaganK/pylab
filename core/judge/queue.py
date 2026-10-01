"""
Очередь проверки: пул потоков внутри веб-процесса.
Отдельный воркер запускать не нужно.
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.db import close_old_connections, transaction
from django.utils import timezone

import constants
from core.models import Submission

from . import sandbox
from .checker import outputs_match

_executor = ThreadPoolExecutor(max_workers=constants.JUDGE_WORKERS, thread_name_prefix="judge")
_run_slots = threading.BoundedSemaphore(constants.MAX_PARALLEL_RUNS)
_recovered = False
_lock = threading.Lock()

SANDBOX_TO_STATUS = {
    sandbox.TLE: Submission.TLE,
    sandbox.MLE: Submission.MLE,
    sandbox.RE: Submission.RE,
    sandbox.OLE: Submission.OLE,
    sandbox.SE: Submission.SE,
}


def enqueue(submission_id: int):
    """Поставить отправку в очередь после коммита транзакции."""
    _recover_once()
    transaction.on_commit(lambda: _executor.submit(_judge_safe, submission_id))


def requeue_stale(minutes=10) -> int:
    """Вернуть в очередь отправки, зависшие дольше `minutes` (например, после перезапуска сервера)."""
    border = timezone.now() - timedelta(minutes=minutes)
    ids = list(Submission.objects.filter(
        status__in=Submission.IN_PROGRESS, created_at__lt=border,
    ).values_list("id", flat=True))
    for sid in ids:
        _executor.submit(_judge_safe, sid)
    return len(ids)


def _recover_once():
    global _recovered
    with _lock:
        if _recovered:
            return
        _recovered = True
    requeue_stale()


def _judge_safe(submission_id: int):
    close_old_connections()
    try:
        judge(submission_id)
    except Exception as e:  # проверка не должна молча зависать
        Submission.objects.filter(pk=submission_id).update(
            status=Submission.SE, details=f"{type(e).__name__}: {e}", checked_at=timezone.now(),
        )
    finally:
        close_old_connections()


def check_syntax(code: str):
    try:
        compile(code, "main.py", "exec")
    except SyntaxError as e:
        line = (e.text or "").rstrip()
        msg = f"Строка {e.lineno}: {type(e).__name__}: {e.msg}"
        if line:
            msg += f"\n    {line.strip()}"
        return msg
    except ValueError as e:
        return str(e)
    return None


def judge(submission_id: int):
    sub = Submission.objects.select_related("task").get(pk=submission_id)
    task = sub.task

    if not task.is_io:
        sub.status = Submission.REVIEW
        sub.save(update_fields=["status"])
        return

    tests = list(task.tests.all())
    sub.tests_total = len(tests)
    sub.tests_passed = 0
    sub.failed_test = None
    sub.failed_is_sample = False
    sub.details = sub.failed_input = sub.failed_expected = sub.failed_output = ""
    sub.max_time = None

    if not tests:
        sub.status = Submission.SE
        sub.details = "У задачи нет тестов — сообщите преподавателю."
        sub.checked_at = timezone.now()
        sub.save()
        return

    syntax_error = check_syntax(sub.code)
    if syntax_error:
        sub.status = Submission.CE
        sub.details = syntax_error
        sub.checked_at = timezone.now()
        sub.save()
        return

    sub.status = Submission.RUNNING
    sub.save()

    max_time = 0.0
    for number, test in enumerate(tests, start=1):
        res = sandbox.run_program(sub.code, test.input_data, task.time_limit, task.memory_limit)
        max_time = max(max_time, res.time)
        verdict = None
        if res.status != sandbox.OK:
            verdict = SANDBOX_TO_STATUS.get(res.status, Submission.SE)
        elif not outputs_match(test.output_data, res.stdout):
            verdict = Submission.WA

        if verdict:
            sub.status = verdict
            sub.failed_test = number
            sub.failed_is_sample = test.is_sample
            sub.details = (res.stderr or "\n".join(res.notes))[-4000:]
            sub.failed_input = test.input_data[:4000]
            sub.failed_expected = test.output_data[:4000]
            sub.failed_output = res.stdout[:4000]
            break
        sub.tests_passed = number
        sub.save(update_fields=["tests_passed"])
    else:
        sub.status = Submission.OK

    sub.max_time = round(max_time, 3)
    sub.checked_at = timezone.now()
    sub.save()


def run_once(code: str, stdin_text: str, time_limit: float, memory_mb: int, console_prefilled=None):
    """Кнопка «Запустить»: один прогон с вводом студента. None — сервер занят."""
    if not _run_slots.acquire(timeout=constants.RUN_WAIT_SECONDS):
        return None
    try:
        return sandbox.run_program(code, stdin_text, time_limit, memory_mb, console_prefilled)
    finally:
        _run_slots.release()
