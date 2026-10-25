import json

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _, gettext_lazy
from django.views.decorators.http import require_POST

from .decorators import teacher_required
from . import importer, netlog, pdf, roster
from .forms import (BulkStudentsForm, GroupForm, ImportTasksForm, LessonForm,
                    ReviewForm, RosterForm, SectionForm, StudentForm, TaskForm,
                    TestCaseFormSet)
from .judge import queue, sandbox
from .models import AccessLog, ClassSection, Lesson, SiteSettings, StudyGroup, Submission, Task
from .translit import make_login, make_password
from .utils import best_submissions

User = get_user_model()


def _unique_login(base):
    login, n = base, 1
    while User.objects.filter(username=login).exists():
        n += 1
        login = f"{base}{n}"
    return login


# --- Главная -------------------------------------------------------------------

@teacher_required
def dashboard(request):
    since = timezone.now() - timezone.timedelta(hours=24)
    stats = Submission.objects.filter(created_at__gte=since).aggregate(
        total=Count("id"),
        good=Count("id", filter=Q(status__in=Submission.GOOD)),
        review=Count("id", filter=Q(status=Submission.REVIEW)),
    )
    recent = Submission.objects.select_related("user", "user__group", "task")[:15]
    groups = StudyGroup.objects.annotate(n=Count("students")).order_by("name")
    since3h = timezone.now() - timezone.timedelta(hours=3)
    shared_ids, shared_ips = netlog.suspicious(since3h)
    return render(request, "core/teacher/dashboard.html", {
        "stats": stats, "recent": recent, "groups": groups,
        "alerts": len(shared_ids) + len(shared_ips) + len(netlog.kicked(since3h)),
        "review_total": Submission.objects.filter(status=Submission.REVIEW).count(),
    })


# --- Группы и студенты -----------------------------------------------------------

@teacher_required
def groups(request):
    form = GroupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        g = form.save()
        messages.success(request, _("Группа %(name)s создана.") % {"name": g.name})
        return redirect("t_group", g.pk)
    items = StudyGroup.objects.annotate(n=Count("students")).order_by("name")
    sections = ClassSection.objects.annotate(n=Count("students")).order_by("code")
    return render(request, "core/teacher/groups.html", {"form": form, "groups": items, "sections": sections})


@teacher_required
def group_detail(request, pk):
    group = get_object_or_404(StudyGroup, pk=pk)
    form = GroupForm(request.POST or None, instance=group)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Группа сохранена."))
        return redirect("t_group", pk)
    students = _students_with_stats(group.students.all())
    return render(request, "core/teacher/group_detail.html", {
        "group": group, "form": form, "students": students,
        "credentials": request.session.pop("credentials", None),
    })


def _students_with_stats(qs):
    """Студенты с числом решённых задач и последней активностью (IP)."""
    students = list(qs.select_related("group").prefetch_related("sections").annotate(
        solved=Count("submissions__task", filter=Q(submissions__status__in=Submission.GOOD), distinct=True),
    ).order_by("full_name", "username"))
    last = {}
    for row in (AccessLog.objects.filter(user__in=[s.id for s in students], ip__isnull=False)
                .exclude(action=AccessLog.LOGIN_FAIL).order_by("user_id", "-created_at")
                .values("user_id", "ip", "created_at")):
        last.setdefault(row["user_id"], row)
    for st in students:
        st.last_seen = last.get(st.id)
    return students


@teacher_required
def section_detail(request, pk):
    section = get_object_or_404(ClassSection, pk=pk)
    form = SectionForm(request.POST or None, instance=section)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Занятие сохранено."))
        return redirect("t_section", pk)
    return render(request, "core/teacher/section_detail.html", {
        "section": section, "form": form,
        "students": _students_with_stats(section.students.all()),
    })


@teacher_required
@require_POST
def section_delete(request, pk):
    section = get_object_or_404(ClassSection, pk=pk)
    section.delete()
    messages.success(request, _("Занятие %(code)s удалено. Студенты остались в своих группах.") % {"code": section.code})
    return redirect("t_groups")


@teacher_required
@require_POST
def group_delete(request, pk):
    group = get_object_or_404(StudyGroup, pk=pk)
    if group.students.exists():
        messages.error(request, _("В группе есть студенты — сначала удалите или переведите их."))
        return redirect("t_group", pk)
    group.delete()
    messages.success(request, _("Группа удалена."))
    return redirect("t_groups")


@teacher_required
def student_create(request):
    initial = {"group": request.GET.get("group")}
    form = StudentForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        student = form.save(commit=False)
        password = form.cleaned_data["password"] or make_password()
        student.set_password(password)
        student.save()
        form.save_m2m()
        request.session["credentials"] = [(student.full_name, student.username, password)]
        messages.success(request, _("Студент %(name)s добавлен.") % {"name": student.full_name})
        return redirect("t_group", student.group_id)
    if not form.is_bound and request.GET.get("name"):
        form.initial["full_name"] = request.GET["name"]
    return render(request, "core/teacher/student_form.html", {"form": form, "title": _("Новый студент")})


@teacher_required
def student_edit(request, pk):
    student = get_object_or_404(User, pk=pk, is_staff=False)
    form = StudentForm(request.POST or None, instance=student)
    if request.method == "POST" and form.is_valid():
        student = form.save(commit=False)
        if form.cleaned_data["password"]:
            student.set_password(form.cleaned_data["password"])
            request.session["credentials"] = [(student.full_name, student.username, form.cleaned_data["password"])]
        student.save()
        form.save_m2m()
        messages.success(request, _("Данные студента сохранены."))
        return redirect("t_group", student.group_id)
    return render(request, "core/teacher/student_form.html", {
        "form": form, "title": student.display_name(), "student": student,
    })


@teacher_required
def students_bulk(request):
    form = BulkStudentsForm(request.POST or None, initial={"group": request.GET.get("group")})
    if request.method == "POST" and form.is_valid():
        group = form.cleaned_data["group"]
        created = []
        with transaction.atomic():
            for line in form.cleaned_data["names"].splitlines():
                name = " ".join(line.split())
                if not name:
                    continue
                login = _unique_login(make_login(name))
                password = make_password()
                u = User(username=login, full_name=name, group=group)
                u.set_password(password)
                u.save()
                created.append((name, login, password))
        request.session["credentials"] = created
        messages.success(request, _("Добавлено студентов: %(n)s.") % {"n": len(created)})
        return redirect("t_group", group.pk)
    return render(request, "core/teacher/students_bulk.html", {"form": form})


@teacher_required
@require_POST
def student_reset_password(request, pk):
    student = get_object_or_404(User, pk=pk, is_staff=False)
    password = make_password()
    student.set_password(password)
    student.save()
    request.session["credentials"] = [(student.full_name, student.username, password)]
    messages.success(request, _("Новый пароль для %(name)s ниже.") % {"name": student.display_name()})
    return redirect("t_group", student.group_id)


@teacher_required
@require_POST
def student_delete(request, pk):
    student = get_object_or_404(User, pk=pk, is_staff=False)
    gid = student.group_id
    student.delete()
    messages.success(request, _("Студент удалён вместе с отправками."))
    return redirect("t_group", gid) if gid else redirect("t_groups")


@teacher_required
def students_roster(request):
    form = RosterForm(request.POST or None, initial={"group": request.GET.get("group")})
    report = preview = skipped = None
    if request.method == "POST" and form.is_valid():
        rows, skipped = roster.parse(form.cleaned_data["text"])
        if not rows:
            form.add_error("text", _("Не нашёл ни одной строки вида «1) ФИО (группа) ID»."))
        elif "confirm" in request.POST:
            report = roster.apply(rows, form.cleaned_data["group"])
            messages.success(request, _("Создано: %(created)s, обновлено: %(updated)s.") % {"created": len(report["created"]), "updated": len(report["updated"])})
            if not report["errors"]:
                if len(report["sections"]) == 1:
                    return redirect("t_section", next(iter(report["sections"])))
                groups_touched = {u.group_id for u in report["created"] + report["updated"]}
                if len(groups_touched) == 1 and not report["sections"]:
                    return redirect("t_group", groups_touched.pop())
                return redirect("t_groups")
        else:
            existing = set(User.objects.filter(username__in=[r["sid"] for r in rows]).values_list("username", flat=True))
            for r in rows:
                r["exists"] = r["sid"] in existing
            preview = rows
    return render(request, "core/teacher/students_roster.html", {
        "form": form, "preview": preview, "skipped": skipped, "report": report,
    })


# --- Уроки и задачи --------------------------------------------------------------

@teacher_required
def lessons(request):
    # Новые уроки сверху. С annotate() Django не применяет сортировку из Meta — задаём её явно
    items = list(Lesson.objects.prefetch_related("open_for", "open_for_sections", "tasks").annotate(
        subs=Count("tasks__submissions"),
    ).order_by("-order", "-id"))
    for i, lesson in enumerate(items):
        lesson.number = len(items) - i   # номер по порядку: старый урок — 1
    return render(request, "core/teacher/lessons.html", {
        "lessons": items, "groups": StudyGroup.objects.all(), "sections": ClassSection.objects.all(),
    })


ML_LABELS = (("ru", "РУС"), ("kk", "ҚАЗ"), ("en", "ENG"))


def _lang_panels(form, fields):
    """Вкладки языков для формы: (код, подпись, поля, заполнено ли)."""
    panels = []
    for code, label in ML_LABELS:
        bound = [form[f"{name}_{code}"] for name in fields]
        filled = any((f.value() or "").strip() for f in bound if isinstance(f.value(), str))
        panels.append((code, label, bound, filled))
    from django.utils.translation import get_language
    ui = (get_language() or "ru")[:2]
    filled_codes = [p[0] for p in panels if p[3]]
    active = ui if (ui in filled_codes or not filled_codes) else filled_codes[0]
    return panels, active


def _ids_from_body(request):
    try:
        ids = json.loads(request.body or b"{}").get("ids", [])
        return [int(x) for x in ids]
    except (ValueError, TypeError, AttributeError):
        return None


@teacher_required
@require_POST
def lessons_reorder(request):
    """Перетаскивание уроков. На странице новые сверху, поэтому
    первый в списке получает самый большой порядок, последний — 1."""
    ids = _ids_from_body(request)
    if ids is None:
        return JsonResponse({"error": _("Неверные данные")}, status=400)
    lessons_by_id = Lesson.objects.in_bulk(ids)
    total = len(ids)
    with transaction.atomic():
        for index, lesson_id in enumerate(ids):
            position = total - index
            lesson = lessons_by_id.get(lesson_id)
            if lesson and lesson.order != position:
                lesson.order = position
                lesson.save(update_fields=["order"])
    return JsonResponse({"ok": True})


@teacher_required
@require_POST
def tasks_reorder(request, pk):
    """Перетаскивание задач внутри урока (и между уроками)."""
    lesson = get_object_or_404(Lesson, pk=pk)
    ids = _ids_from_body(request)
    if ids is None:
        return JsonResponse({"error": _("Неверные данные")}, status=400)
    tasks_by_id = Task.objects.in_bulk(ids)
    with transaction.atomic():
        for position, task_id in enumerate(ids, start=1):
            task = tasks_by_id.get(task_id)
            if task and (task.order != position or task.lesson_id != lesson.pk):
                task.order = position
                task.lesson = lesson
                task.save(update_fields=["order", "lesson"])
    return JsonResponse({"ok": True})


@teacher_required
def lesson_form(request, pk=None):
    lesson = get_object_or_404(Lesson, pk=pk) if pk else None
    initial = {}
    if not lesson:
        initial["order"] = (Lesson.objects.order_by("-order").values_list("order", flat=True).first() or 0) + 1
    form = LessonForm(request.POST or None, instance=lesson, initial=initial)
    if request.method == "POST" and form.is_valid():
        lesson = form.save()
        messages.success(request, _("Урок сохранён."))
        return redirect("t_lessons")
    panels, active = _lang_panels(form, ("title", "topic"))
    return render(request, "core/teacher/lesson_form.html", {"form": form, "lesson": lesson, "panels": panels, "active": active})


@teacher_required
def lesson_pdf(request, pk):
    lesson = get_object_or_404(Lesson, pk=pk)
    tasks = lesson.tasks.prefetch_related("tests")
    if request.GET.get("open_only"):
        tasks = tasks.filter(is_open=True)
    return pdf.render_lesson(request, lesson, list(tasks), f"{lesson.title}.pdf")


@teacher_required
def task_pdf(request, pk):
    task = get_object_or_404(Task.objects.select_related("lesson").prefetch_related("tests"), pk=pk)
    return pdf.render_lesson(request, task.lesson, [task], f"{task.title}.pdf")


@teacher_required
@require_POST
def lesson_delete(request, pk):
    lesson = get_object_or_404(Lesson, pk=pk)
    lesson.delete()
    messages.success(request, _("Урок «%(title)s» удалён.") % {"title": lesson.title})
    return redirect("t_lessons")


@teacher_required
@require_POST
def lesson_toggle_group(request, pk, group_id):
    lesson = get_object_or_404(Lesson, pk=pk)
    group = get_object_or_404(StudyGroup, pk=group_id)
    if lesson.open_for.filter(pk=group.pk).exists():
        lesson.open_for.remove(group)
    else:
        lesson.open_for.add(group)
    return redirect(request.POST.get("next") or "t_lessons")


@teacher_required
@require_POST
def lesson_toggle_section(request, pk, section_id):
    lesson = get_object_or_404(Lesson, pk=pk)
    section = get_object_or_404(ClassSection, pk=section_id)
    if lesson.open_for_sections.filter(pk=section.pk).exists():
        lesson.open_for_sections.remove(section)
    else:
        lesson.open_for_sections.add(section)
    return redirect(request.POST.get("next") or "t_lessons")


@teacher_required
@require_POST
def task_toggle(request, pk):
    task = get_object_or_404(Task, pk=pk)
    task.is_open = not task.is_open
    task.save(update_fields=["is_open"])
    return redirect(request.POST.get("next") or "t_lessons")


@teacher_required
def task_form(request, pk=None):
    task = get_object_or_404(Task, pk=pk) if pk else None
    initial = {}
    if not task and request.GET.get("lesson"):
        lesson = get_object_or_404(Lesson, pk=request.GET["lesson"])
        initial = {"lesson": lesson, "order": lesson.tasks.count() + 1}
    form = TaskForm(request.POST or None, instance=task, initial=initial)
    formset = TestCaseFormSet(request.POST or None, instance=task or Task(), prefix="tests")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            task = form.save()
            formset.instance = task
            tests = formset.save(commit=False)
            for obj in formset.deleted_objects:
                obj.delete()
            for t in tests:
                t.task = task
                t.save()
            # порядок тестов — как на странице
            for i, f in enumerate(formset.forms):
                if f.instance.pk and not f.cleaned_data.get("DELETE"):
                    type(f.instance).objects.filter(pk=f.instance.pk).update(order=i)
        messages.success(request, _("Задача сохранена."))
        if "save_stay" in request.POST:
            return redirect("t_task_edit", task.pk)
        return redirect(reverse("t_lessons") + f"#lesson-{task.lesson_id}")
    panels, active = _lang_panels(form, ("title", "statement", "input_format", "output_format"))
    return render(request, "core/teacher/task_form.html", {
        "form": form, "formset": formset, "task": task, "panels": panels, "active": active,
    })


@teacher_required
@require_POST
def task_delete(request, pk):
    task = get_object_or_404(Task, pk=pk)
    task.delete()
    messages.success(request, _("Задача «%(title)s» удалена.") % {"title": task.title})
    return redirect("t_lessons")


@teacher_required
@require_POST
def task_rejudge(request, pk):
    task = get_object_or_404(Task, pk=pk)
    ids = list(task.submissions.values_list("id", flat=True))
    Submission.objects.filter(id__in=ids).update(status=Submission.PENDING)
    for sid in ids:
        queue.enqueue(sid)
    messages.success(request, _("Отправлено на перепроверку: %(n)s.") % {"n": len(ids)})
    return redirect("t_task_edit", pk)


@teacher_required
def tasks_import(request):
    form = ImportTasksForm(request.POST or None, request.FILES or None,
                           initial={"lesson": request.GET.get("lesson")})
    warnings = []
    if request.method == "POST" and form.is_valid():
        try:
            tasks = importer.parse(form.cleaned_data["raw"], form.cleaned_data["language"])
        except importer.TaskFileError as e:
            form.add_error(None, str(e))
        else:
            if form.cleaned_data["check_solutions"]:
                warnings = importer.verify_solutions(tasks)
            if warnings and "force" not in request.POST:
                form.add_error(None, _("Эталонные решения не прошли часть тестов — задачи не созданы. "
                                       "Исправьте файл или нажмите «Всё равно импортировать»."))
            else:
                lesson = form.cleaned_data["lesson"]
                created = importer.create_tasks(lesson, tasks, form.cleaned_data["is_open"], form.cleaned_data["language"])
                n_tests = sum(t.tests.count() for t in created)
                messages.success(request, _("Добавлено задач: %(tasks)s, тестов: %(tests)s.") % {"tasks": len(created), "tests": n_tests})
                return redirect(reverse("t_lessons") + f"#lesson-{lesson.pk}")
    return render(request, "core/teacher/tasks_import.html", {"form": form, "warnings": warnings})


# --- Отправки --------------------------------------------------------------------

@teacher_required
def submissions(request):
    qs = Submission.objects.select_related("user", "user__group", "task", "task__lesson")
    f = {k: request.GET.get(k, "") for k in ("group", "section", "lesson", "task", "student", "status")}
    if f["group"]:
        qs = qs.filter(user__group_id=f["group"])
    if f["section"]:
        qs = qs.filter(user__sections=f["section"])
    if f["lesson"]:
        qs = qs.filter(task__lesson_id=f["lesson"])
    if f["task"]:
        qs = qs.filter(task_id=f["task"])
    if f["student"]:
        qs = qs.filter(Q(user__full_name__icontains=f["student"]) | Q(user__username__icontains=f["student"]))
    if f["status"] == "good":
        qs = qs.filter(status__in=Submission.GOOD)
    elif f["status"] == "bad":
        qs = qs.exclude(status__in=Submission.GOOD | Submission.NEUTRAL)
    elif f["status"]:
        qs = qs.filter(status=f["status"])
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "core/teacher/submissions.html", {
        "page": page, "f": f, "query": query.urlencode(),
        "groups": StudyGroup.objects.all(),
        "sections": ClassSection.objects.all(),
        "lessons": Lesson.objects.all(),
        "tasks": Task.objects.filter(lesson_id=f["lesson"]) if f["lesson"] else Task.objects.select_related("lesson"),
        "statuses": Submission.STATUS_CHOICES,
    })


@teacher_required
def submission_detail(request, pk):
    sub = get_object_or_404(Submission.objects.select_related("user", "user__group", "task", "task__lesson"), pk=pk)
    form = ReviewForm(request.POST or None, initial={"comment": sub.teacher_comment})
    if request.method == "POST" and form.is_valid():
        action = request.POST.get("action")
        sub.teacher_comment = form.cleaned_data["comment"]
        if action == "accept":
            sub.status = Submission.ACCEPTED
        elif action == "reject":
            sub.status = Submission.REJECTED
        sub.checked_at = timezone.now()
        sub.save()
        messages.success(request, _("Решение отмечено."))
        nxt = Submission.objects.filter(status=Submission.REVIEW).order_by("created_at").first()
        if nxt and "next_review" in request.POST:
            return redirect("t_submission", nxt.pk)
        return redirect("t_submission", pk)
    others = Submission.objects.filter(user=sub.user, task=sub.task).only("id", "status", "created_at")
    # Как писался код: когда впервые открыл задачу, сколько запускал до этой отправки
    from . import integrity
    logs = AccessLog.objects.filter(user=sub.user, task=sub.task, created_at__lte=sub.created_at)
    first_open = logs.filter(action=AccessLog.OPEN).order_by("created_at").values_list("created_at", flat=True).first()
    runs = logs.filter(action=AccessLog.RUN).count()
    spent = integrity._fmt_dur((sub.created_at - first_open).total_seconds()) if first_open else None
    from . import ai_review
    from .models import AIReview
    return render(request, "core/teacher/submission_detail.html", {
        "sub": sub, "form": form, "others": others, "spent": spent, "runs": runs,
        "ai_markers": integrity.ai_markers(sub.code),
        "review": AIReview.objects.filter(submission=sub).first(), "ai_ready": ai_review.is_configured(),
    })


@teacher_required
@require_POST
def submission_ai_review(request, pk):
    """Оценить одно решение с помощью ИИ (со страницы отправки)."""
    from django.utils.translation import get_language
    from . import ai_review, integrity
    sub = get_object_or_404(Submission, pk=pk)
    if not ai_review.is_configured():
        messages.error(request, _("ИИ не настроен: добавьте AI_API_KEY в constants.py."))
        return redirect("t_submission", pk)
    items, _students = integrity.analyze([sub.user_id], [sub.task_id])
    item = next((r for r in items if r["sub"].pk == sub.pk), None) or {"sub": sub, "flags": [], "similar_other": None}
    ai_review.enqueue([item], (get_language() or "ru")[:2], force=True)
    messages.success(request, _("Решение отправлено на оценку ИИ. Обновите страницу через несколько секунд."))
    return redirect("t_submission", pk)


@teacher_required
@require_POST
def submission_rejudge(request, pk):
    sub = get_object_or_404(Submission, pk=pk)
    sub.status = Submission.PENDING
    sub.save(update_fields=["status"])
    queue.enqueue(sub.pk)
    messages.success(request, _("Отправка поставлена на перепроверку."))
    return redirect("t_submission", pk)


@teacher_required
@require_POST
def requeue_stale(request):
    n = queue.requeue_stale(minutes=2)
    messages.success(request, _("Возвращено в очередь: %(n)s.") % {"n": n})
    return redirect("t_submissions")


# --- Сводная таблица -------------------------------------------------------------

def _parse_dt(value):
    """«2026-10-24T14:00» или «2026-10-24» → aware datetime (часовой пояс сайта)."""
    from datetime import datetime
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return timezone.make_aware(datetime.strptime(value, fmt))
        except ValueError:
            continue
    return None


def _results_scope(request):
    """Общие фильтры сводной: кто (занятие/группа), урок, период отправок."""
    groups_qs = StudyGroup.objects.all()
    sections_qs = ClassSection.objects.all()
    # Кого показывать: «s12» — занятие, «g3» — группа. Старые ссылки ?group=3 тоже работают.
    who = request.GET.get("who") or (f"g{request.GET['group']}" if request.GET.get("group") else "")
    target, by_section = None, False
    if who.startswith("s") and who[1:].isdigit():
        target = sections_qs.filter(pk=who[1:]).first()
        by_section = True
    elif who.startswith("g") and who[1:].isdigit():
        target = groups_qs.filter(pk=who[1:]).first()
    if target is None:
        target = sections_qs.first() or groups_qs.first()
        by_section = isinstance(target, ClassSection)
    students = []
    if target is not None:
        who = f"{'s' if by_section else 'g'}{target.pk}"
        qs = target.students.select_related("group")
        students = list(qs.order_by("group__name", "full_name") if by_section else qs.order_by("full_name", "username"))

    # Уроки: новые сверху. Отмечаем открытые для выбранного занятия/группы.
    lessons_list = list(Lesson.objects.order_by("-order", "-id"))
    open_ids = set()
    if target is not None:
        rel = "open_for_sections" if by_section else "open_for"
        open_ids = set(Lesson.objects.filter(**{rel: target}).values_list("id", flat=True))
    for lesson in lessons_list:
        lesson.is_open_here = lesson.id in open_ids

    # Какой урок показать: явно выбранный, «все» или по умолчанию — последний открытый
    lesson_param = request.GET.get("lesson")
    if lesson_param == "all":
        current = None
    elif lesson_param and lesson_param.isdigit():
        current = next((x for x in lessons_list if x.id == int(lesson_param)), None)
    else:
        current = next((x for x in lessons_list if x.is_open_here), None) or (lessons_list[0] if lessons_list else None)
    newer = older = None
    if current is not None:
        idx = lessons_list.index(current)
        newer = lessons_list[idx - 1] if idx > 0 else None
        older = lessons_list[idx + 1] if idx + 1 < len(lessons_list) else None

    # Период: с / по (по — включительно, если указана только дата)
    date_from = request.GET.get("from", "")
    date_to = request.GET.get("to", "")
    since = _parse_dt(date_from)
    until = _parse_dt(date_to)
    if until is not None and "T" not in date_to:
        until += timezone.timedelta(days=1)
    today = timezone.localdate()
    presets = [
        (_("Всё время"), "", ""),
        (_("Сегодня"), today.isoformat(), today.isoformat()),
        (_("Вчера"), (today - timezone.timedelta(days=1)).isoformat(), (today - timezone.timedelta(days=1)).isoformat()),
        (_("7 дней"), (today - timezone.timedelta(days=6)).isoformat(), today.isoformat()),
    ]
    from django.http import QueryDict
    base = QueryDict(mutable=True)
    base["who"] = who
    base["lesson"] = "all" if current is None else str(current.id)
    preset_links = []
    for label, f, t_ in presets:
        q = base.copy()
        if f:
            q["from"], q["to"] = f, t_
        preset_links.append((label, q.urlencode(), f == date_from and t_ == date_to))
    keep = base.copy()
    if date_from:
        keep["from"] = date_from
    if date_to:
        keep["to"] = date_to
    period_q = QueryDict(mutable=True)
    period_q.update({k: v for k, v in (("from", date_from), ("to", date_to)) if v})

    return {
        "target": target, "who": who, "by_section": by_section, "students": students,
        "lessons": lessons_list, "current": current, "newer": newer, "older": older,
        "lesson_id": "all" if current is None else str(current.id),
        "since": since, "until": until, "date_from": date_from, "date_to": date_to,
        "preset_links": preset_links, "query": keep.urlencode(), "period_query": period_q.urlencode(),
        "section_opts": [(f"s{x.pk}", str(x)) for x in sections_qs],
        "group_opts": [(f"g{x.pk}", x.name) for x in groups_qs],
    }


@teacher_required
def results(request):
    ctx = _results_scope(request)
    tasks = Task.objects.select_related("lesson")
    if ctx["current"] is not None:
        tasks = tasks.filter(lesson=ctx["current"])
    tasks = list(tasks)
    rows = []
    students = ctx["students"]
    best, attempts = best_submissions([s.id for s in students], [t.id for t in tasks], ctx["since"], ctx["until"])
    for s in students:
        cells = [(t, best.get((s.id, t.id)), attempts.get((s.id, t.id), 0)) for t in tasks]
        rows.append((s, cells, sum(1 for _t, b, _n in cells if b and b.is_good)))
    # Сколько студентов решило каждую задачу — строка внизу таблицы
    solved_by_task = [sum(1 for _s, cells, _n in rows if cells[i][1] and cells[i][1].is_good) for i in range(len(tasks))]
    ctx.update({"tasks": tasks, "rows": rows, "solved_by_task": solved_by_task, "tab": "table"})
    return render(request, "core/teacher/results.html", ctx)


@teacher_required
def results_lessons(request):
    """Прохождение уроков: сколько студентов приступили, решили хотя бы одну и все задачи."""
    ctx = _results_scope(request)
    students = ctx["students"]
    ids = [s.id for s in students]
    total = len(ids)
    subs = Submission.objects.filter(user_id__in=ids)
    if ctx["since"]:
        subs = subs.filter(created_at__gte=ctx["since"])
    if ctx["until"]:
        subs = subs.filter(created_at__lt=ctx["until"])
    tried, solved = set(), set()
    for row in subs.values("user_id", "task_id", "status"):
        tried.add((row["user_id"], row["task_id"]))
        if row["status"] in Submission.GOOD:
            solved.add((row["user_id"], row["task_id"]))
    stats = []
    for lesson in ctx["lessons"]:
        tasks = list(lesson.tasks.all())
        if not tasks:
            continue
        tids = [t.id for t in tasks]
        per_student = {uid: sum(1 for t in tids if (uid, t) in solved) for uid in ids}
        started = sum(1 for uid in ids if any((uid, t) in tried for t in tids))
        some = sum(1 for v in per_student.values() if v > 0)
        full = sum(1 for v in per_student.values() if v == len(tids))
        done = sum(per_student.values())
        task_rows = []
        for t in tasks:
            n = sum(1 for uid in ids if (uid, t.id) in solved)
            task_rows.append((t, n, round(100 * n / total) if total else 0))
        stats.append({
            "lesson": lesson, "tasks": len(tids), "started": started, "some": some, "full": full,
            "avg": round(done / total, 1) if total else 0,
            "pct": round(100 * done / (total * len(tids))) if total else 0,
            "task_rows": task_rows,
        })
    ctx.update({"stats": stats, "total": total, "tab": "lessons"})
    return render(request, "core/teacher/results_lessons.html", ctx)


@teacher_required
def results_integrity(request):
    """Проверка на списывание: верные решения с признаками, подозрительные сверху."""
    from . import integrity
    ctx = _results_scope(request)
    tasks = Task.objects.all()
    if ctx["current"] is not None:
        tasks = tasks.filter(lesson=ctx["current"])
    items, per_student = integrity.analyze([s.id for s in ctx["students"]], list(tasks.values_list("id", flat=True)),
                                           ctx["since"], ctx["until"])
    from . import ai_review
    from .models import AIReview
    reviews = {r.submission_id: r for r in AIReview.objects.filter(submission__in=[x["sub"] for x in items])}
    for r in items:
        r["review"] = reviews.get(r["sub"].pk)

    from django.utils.translation import get_language
    lang = (get_language() or "ru")[:2]

    # Выгрузка подозрительных решений в файл — для проверки вне сайта (например, в чате с Claude)
    if request.GET.get("export") == "1":
        import json
        from django.http import HttpResponse
        force = request.GET.get("force") == "1"
        chosen = [r for r in items if r["score"] >= ai_review.MIN_SCORE and ai_review.needs_review(r, force)]
        payload = ai_review.export_payload(chosen, lang)
        response = HttpResponse(json.dumps(payload, ensure_ascii=False, indent=1), content_type="application/json; charset=utf-8")
        stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
        response["Content-Disposition"] = f'attachment; filename="pylab-review-{stamp}.json"'
        return response

    # Загрузка готовых оценок (файл или вставленный текст)
    if request.method == "POST" and request.POST.get("action") == "import":
        upload = request.FILES.get("file")
        raw = upload.read().decode("utf-8-sig", errors="replace") if upload else request.POST.get("text", "")
        try:
            saved, skipped = ai_review.import_reviews(raw, lang, label=request.POST.get("label") or "manual")
        except ValueError as e:   # сюда же попадает ошибка разбора JSON
            messages.error(request, _("Не удалось прочитать оценки: %(e)s") % {"e": e})
        else:
            messages.success(request, _("Загружено оценок: %(n)s.") % {"n": saved}
                             + (" " + _("Пропущено (нет такого решения): %(n)s.") % {"n": skipped} if skipped else ""))
        return redirect(request.get_full_path())

    # Кнопка «Оценить с помощью ИИ»
    if request.method == "POST":
        if not ai_review.is_configured():
            messages.error(request, _("ИИ не настроен: добавьте AI_API_KEY в constants.py."))
        else:
            candidates = [r for r in items if r["score"] >= ai_review.MIN_SCORE]
            n = ai_review.enqueue(candidates, lang, force=request.POST.get("force") == "1")
            if n:
                messages.success(request, _("Отправлено на оценку ИИ: %(n)s. Результаты появятся в таблице через минуту.") % {"n": n})
            else:
                messages.info(request, _("Новых решений для оценки нет — все подозрительные уже оценены."))
        return redirect(request.get_full_path())

    show_all = request.GET.get("all") == "1"
    ai_filter = request.GET.get("ai", "")
    shown = items if show_all else [r for r in items if r["score"] >= 2 or (r["review"] and r["review"].verdict in AIReview.BAD)]
    if ai_filter == "bad":
        shown = [r for r in shown if r["review"] and r["review"].verdict in AIReview.BAD]
    # Сначала то, что ИИ счёл нечестным, затем по баллу
    severity = {AIReview.COPIED: 0, AIReview.AI: 0, AIReview.UNSURE: 1, AIReview.HONEST: 3}
    shown.sort(key=lambda r: (severity.get(r["review"].verdict, 2) if r["review"] and r["review"].status == "done" else 2,
                              -r["score"], r["sub"].created_at))
    ai_bad = {}
    for r in items:
        if r["review"] and r["review"].verdict in AIReview.BAD:
            ai_bad[r["sub"].user_id] = ai_bad.get(r["sub"].user_id, 0) + 1
    for p in per_student:
        p["ai_bad"] = ai_bad.get(p["user"].pk, 0)
    pending = sum(1 for r in items if r["review"] and r["review"].status == AIReview.PENDING)
    to_review = sum(1 for r in items if r["score"] >= ai_review.MIN_SCORE and not (
        r["review"] and r["review"].status in (AIReview.DONE, AIReview.PENDING)
        and r["review"].code_hash == ai_review.code_hash(r["sub"].code)))
    ctx.update({"items": shown, "total_ok": len(items), "hidden": len(items) - len(shown), "show_all": show_all,
                "per_student": [p for p in per_student if p["flagged"] or p["ai_bad"]], "tab": "integrity",
                "thresholds": integrity, "ai_ready": ai_review.is_configured(), "ai_pending": pending,
                "ai_to_review": min(to_review, ai_review.MAX_PER_RUN), "ai_to_review_total": to_review,
                "ai_filter": ai_filter, "ai_model": ai_review.MODEL})
    return render(request, "core/teacher/results_integrity.html", ctx)


# --- Журнал входов и IP ---

PERIODS = {"3h": (gettext_lazy("3 часа"), 3), "day": (gettext_lazy("сутки"), 24), "week": (gettext_lazy("неделя"), 24 * 7)}


@teacher_required
def access_log(request):
    period = request.GET.get("period", "3h")
    if period not in PERIODS:
        period = "3h"
    since = timezone.now() - timezone.timedelta(hours=PERIODS[period][1])
    shared_ids, shared_ips = netlog.suspicious(since)
    kicked = netlog.kicked(since)

    qs = AccessLog.objects.filter(created_at__gte=since).select_related("user", "user__group")
    f = {k: request.GET.get(k, "") for k in ("group", "student", "ip", "action")}
    if f["group"]:
        qs = qs.filter(user__group_id=f["group"])
    if f["student"]:
        qs = qs.filter(Q(user__full_name__icontains=f["student"]) | Q(user__username__icontains=f["student"]))
    if f["ip"]:
        qs = qs.filter(ip=f["ip"])
    if f["action"]:
        qs = qs.filter(action=f["action"])
    page = Paginator(qs, 100).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "core/teacher/access_log.html", {
        "shared_ids": shared_ids, "shared_ips": shared_ips, "kicked": kicked,
        "single_session": SiteSettings.get().single_session,
        "page": page, "f": f, "query": query.urlencode(),
        "period": period, "periods": [(k, v[0]) for k, v in PERIODS.items()],
        "groups": StudyGroup.objects.all(), "actions": AccessLog.ACTION_CHOICES,
    })


# --- Настройки сайта ---

@teacher_required
def site_settings(request):
    obj = SiteSettings.get()
    if request.method == "POST":
        obj.single_session = "single_session" in request.POST
        obj.save()
        messages.success(request, _("Настройки сохранены. ")
                         + (_("Один вход на аккаунт — включено.") if obj.single_session else _("Один вход на аккаунт — выключено.")))
        return redirect(request.POST.get("next") or "t_settings")
    return render(request, "core/teacher/settings.html", {"s": obj})


# --- Песочница -------------------------------------------------------------------

@teacher_required
def sandbox_check(request):
    report = sandbox.selftest() if request.method == "POST" else None
    return render(request, "core/teacher/sandbox.html", {"report": report})
