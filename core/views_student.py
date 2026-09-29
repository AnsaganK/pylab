import markdown
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST

from . import netlog
from .judge import queue
from .judge.sandbox import MLE, OK, OLE, RE, SE, TLE
from .models import AccessLog, Lesson, Submission, Task
from .utils import best_submissions

MAX_CODE_SIZE = 64 * 1024
MAX_STDIN_SIZE = 256 * 1024


def _md(text):
    return mark_safe(markdown.markdown(text or "", extensions=["fenced_code", "tables", "nl2br"]))


def _visible_task_or_404(request, pk):
    task = get_object_or_404(Task.objects.select_related("lesson"), pk=pk)
    if not task.is_visible_to(request.user):
        raise Http404
    return task


@login_required
def lessons(request):
    user = request.user
    if user.is_teacher:
        return redirect("t_lessons")
    items = []
    qs = Lesson.objects.filter(Lesson.visible_q(user)).distinct().prefetch_related("tasks")
    all_tasks = [t for l in qs for t in l.tasks.all() if t.is_open]
    best, attempts = best_submissions([user.id], [t.id for t in all_tasks])
    for lesson in qs:
        tasks = [(t, best.get((user.id, t.id)), attempts.get((user.id, t.id), 0))
                 for t in lesson.tasks.all() if t.is_open]
        if tasks:
            solved = sum(1 for _, b, _ in tasks if b and b.is_good)
            items.append((lesson, tasks, solved))
    has_place = bool(user.group_id) or user.sections.exists()
    return render(request, "core/student/lessons.html", {"items": items, "has_place": has_place})


@login_required
def task_page(request, pk):
    task = _visible_task_or_404(request, pk)
    my = Submission.objects.filter(user=request.user, task=task)
    last = my.first()
    visible = [t for t in task.lesson.tasks.all() if t.is_visible_to(request.user)]
    best, attempts = best_submissions([request.user.id], [t.id for t in visible])
    siblings = [(t, best.get((request.user.id, t.id)), attempts.get((request.user.id, t.id), 0)) for t in visible]
    current = best.get((request.user.id, task.id))
    tests = list(task.tests.all())
    # «Задача без ввода»: во всех тестах пустой ввод и формат ввода не описан
    has_input = bool(task.input_format.strip()) or any(t.input_data.strip() for t in tests) or not tests
    idx = next(i for i, (t, _, _) in enumerate(siblings) if t.id == task.id) if siblings else 0
    return render(request, "core/student/task.html", {
        "current": current,
        "has_input": has_input,
        "prev_task": siblings[idx - 1][0] if idx > 0 else None,
        "next_task": siblings[idx + 1][0] if idx + 1 < len(siblings) else None,
        "task": task,
        "statement": _md(task.statement),
        "input_format": _md(task.input_format),
        "output_format": _md(task.output_format),
        "samples": [t for t in tests if t.is_sample],
        "code": last.code if last else task.starter_code,
        "subs": my[:20],
        "busy": my.filter(status__in=Submission.IN_PROGRESS).exists(),
        "siblings": siblings,
    })


@login_required
def submissions_fragment(request, pk):
    task = _visible_task_or_404(request, pk)
    subs = list(Submission.objects.filter(user=request.user, task=task)[:20])
    busy = any(s.in_progress for s in subs)
    best, _ = best_submissions([request.user.id], [task.id])
    current = best.get((request.user.id, task.id))
    return render(request, "core/student/_subs.html", {"subs": subs, "task": task, "busy": busy, "current": current})


RUN_LABELS = {
    OK: "Программа завершилась",
    TLE: "Превышено время",
    MLE: "Превышена память",
    RE: "Ошибка выполнения",
    OLE: "Слишком большой вывод",
    SE: "Ошибка песочницы",
}


@login_required
@require_POST
def run_code(request, pk):
    task = _visible_task_or_404(request, pk)
    code = request.POST.get("code", "")
    stdin = request.POST.get("stdin", "")
    if len(code) > MAX_CODE_SIZE or len(stdin) > MAX_STDIN_SIZE:
        return JsonResponse({"error": "Слишком большой код или ввод."}, status=400)
    if "\x00" in code or "\x00" in stdin:
        return JsonResponse({"error": "В коде или вводе есть недопустимый символ (NUL). Перепечатайте строку вручную."}, status=400)
    netlog.log(request, AccessLog.RUN, details=task.title)
    syntax = queue.check_syntax(code)
    if syntax:
        hint = ("Проблема с отступами: внутри блока все строки должны начинаться с одинакового отступа."
                if ("IndentationError" in syntax or "TabError" in syntax)
                else "Проверьте указанную строку и строку перед ней: часто там не закрыта скобка или кавычка, "
                     "либо забыто двоеточие после if/for/while/def.")
        return JsonResponse({"status": "ce", "label": "Синтаксическая ошибка", "stdout": "", "stderr": syntax,
                             "hint": hint, "time": 0})
    res = queue.run_once(code, stdin, task.time_limit, task.memory_limit)
    if res is None:
        return JsonResponse({"error": "Сервер сейчас занят — попробуйте через несколько секунд."}, status=503)
    stderr = res.stderr or "\n".join(res.notes)
    hint = ""
    if "EOFError" in stderr:
        hint = ("Программа вызвала input(), но ввод закончился. "
                + ("Поле «Ввод» пустое — впишите туда данные." if not stdin.strip()
                   else "Строк во вводе меньше, чем вызовов input()."))
    elif "NameError" in stderr and "input" not in stderr and "name '" in stderr:
        hint = "Опечатка в имени переменной или функции, либо переменная используется раньше, чем создана."
    elif "IndentationError" in stderr or "TabError" in stderr:
        hint = "Проблема с отступами: внутри блока все строки должны начинаться с одинакового отступа."
    return JsonResponse({
        "status": res.status, "label": RUN_LABELS.get(res.status, res.status),
        "stdout": res.stdout, "stderr": stderr, "hint": hint, "time": res.time,
    })


@login_required
@require_POST
def submit(request, pk):
    task = _visible_task_or_404(request, pk)
    code = request.POST.get("code", "")
    if not code.strip():
        return JsonResponse({"error": "Код пустой."}, status=400)
    if len(code) > MAX_CODE_SIZE:
        return JsonResponse({"error": "Слишком большой код."}, status=400)
    if "\x00" in code:
        # PostgreSQL не хранит символ NUL в текстовых полях — без проверки была бы ошибка 500
        return JsonResponse({"error": "В коде есть недопустимый символ (NUL). Перепечатайте строку вручную."}, status=400)
    if Submission.objects.filter(user=request.user, status__in=Submission.IN_PROGRESS).count() >= 3:
        return JsonResponse({"error": "Дождитесь проверки предыдущих отправок."}, status=429)
    status = Submission.PENDING if task.is_io else Submission.REVIEW
    sub = Submission.objects.create(user=request.user, task=task, code=code, status=status,
                                    ip=netlog.client_ip(request))
    netlog.log(request, AccessLog.SUBMIT, details=task.title)
    if task.is_io:
        queue.enqueue(sub.pk)
    return JsonResponse({"ok": True, "id": sub.pk})


@login_required
def my_submission(request, pk):
    sub = get_object_or_404(Submission.objects.select_related("task"), pk=pk)
    if sub.user_id != request.user.id and not request.user.is_teacher:
        raise Http404
    return render(request, "core/student/submission.html", {"sub": sub})
