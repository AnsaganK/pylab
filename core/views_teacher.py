from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
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
    groups = StudyGroup.objects.annotate(n=Count("students"))
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
        messages.success(request, f"Группа {g.name} создана.")
        return redirect("t_group", g.pk)
    items = StudyGroup.objects.annotate(n=Count("students"))
    sections = ClassSection.objects.annotate(n=Count("students"))
    return render(request, "core/teacher/groups.html", {"form": form, "groups": items, "sections": sections})


@teacher_required
def group_detail(request, pk):
    group = get_object_or_404(StudyGroup, pk=pk)
    form = GroupForm(request.POST or None, instance=group)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Группа сохранена.")
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
    ))
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
        messages.success(request, "Занятие сохранено.")
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
    messages.success(request, f"Занятие {section.code} удалено. Студенты остались в своих группах.")
    return redirect("t_groups")


@teacher_required
@require_POST
def group_delete(request, pk):
    group = get_object_or_404(StudyGroup, pk=pk)
    if group.students.exists():
        messages.error(request, "В группе есть студенты — сначала удалите или переведите их.")
        return redirect("t_group", pk)
    group.delete()
    messages.success(request, "Группа удалена.")
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
        messages.success(request, f"Студент {student.full_name} добавлен.")
        return redirect("t_group", student.group_id)
    if not form.is_bound and request.GET.get("name"):
        form.initial["full_name"] = request.GET["name"]
    return render(request, "core/teacher/student_form.html", {"form": form, "title": "Новый студент"})


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
        messages.success(request, "Данные студента сохранены.")
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
        messages.success(request, f"Добавлено студентов: {len(created)}.")
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
    messages.success(request, f"Новый пароль для {student.display_name()} ниже.")
    return redirect("t_group", student.group_id)


@teacher_required
@require_POST
def student_delete(request, pk):
    student = get_object_or_404(User, pk=pk, is_staff=False)
    gid = student.group_id
    student.delete()
    messages.success(request, "Студент удалён вместе с отправками.")
    return redirect("t_group", gid) if gid else redirect("t_groups")


@teacher_required
def students_roster(request):
    form = RosterForm(request.POST or None, initial={"group": request.GET.get("group")})
    report = preview = skipped = None
    if request.method == "POST" and form.is_valid():
        rows, skipped = roster.parse(form.cleaned_data["text"])
        if not rows:
            form.add_error("text", "Не нашёл ни одной строки вида «1) ФИО (группа) ID».")
        elif "confirm" in request.POST:
            report = roster.apply(rows, form.cleaned_data["group"])
            messages.success(request, f"Создано: {len(report['created'])}, обновлено: {len(report['updated'])}.")
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
    items = Lesson.objects.prefetch_related("open_for", "open_for_sections", "tasks").annotate(
        subs=Count("tasks__submissions"),
    )
    return render(request, "core/teacher/lessons.html", {
        "lessons": items, "groups": StudyGroup.objects.all(), "sections": ClassSection.objects.all(),
    })


@teacher_required
def lesson_form(request, pk=None):
    lesson = get_object_or_404(Lesson, pk=pk) if pk else None
    initial = {}
    if not lesson:
        initial["order"] = (Lesson.objects.order_by("-order").values_list("order", flat=True).first() or 0) + 1
    form = LessonForm(request.POST or None, instance=lesson, initial=initial)
    if request.method == "POST" and form.is_valid():
        lesson = form.save()
        messages.success(request, "Урок сохранён.")
        return redirect("t_lessons")
    return render(request, "core/teacher/lesson_form.html", {"form": form, "lesson": lesson})


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
    messages.success(request, f"Урок «{lesson.title}» удалён.")
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
        messages.success(request, "Задача сохранена.")
        if "save_stay" in request.POST:
            return redirect("t_task_edit", task.pk)
        return redirect("t_lessons")
    return render(request, "core/teacher/task_form.html", {
        "form": form, "formset": formset, "task": task,
    })


@teacher_required
@require_POST
def task_delete(request, pk):
    task = get_object_or_404(Task, pk=pk)
    task.delete()
    messages.success(request, f"Задача «{task.title}» удалена.")
    return redirect("t_lessons")


@teacher_required
@require_POST
def task_rejudge(request, pk):
    task = get_object_or_404(Task, pk=pk)
    ids = list(task.submissions.values_list("id", flat=True))
    Submission.objects.filter(id__in=ids).update(status=Submission.PENDING)
    for sid in ids:
        queue.enqueue(sid)
    messages.success(request, f"Отправлено на перепроверку: {len(ids)}.")
    return redirect("t_task_edit", pk)


@teacher_required
def tasks_import(request):
    form = ImportTasksForm(request.POST or None, request.FILES or None,
                           initial={"lesson": request.GET.get("lesson")})
    warnings = []
    if request.method == "POST" and form.is_valid():
        try:
            tasks = importer.parse(form.cleaned_data["raw"])
        except importer.TaskFileError as e:
            form.add_error(None, str(e))
        else:
            if form.cleaned_data["check_solutions"]:
                warnings = importer.verify_solutions(tasks)
            if warnings and "force" not in request.POST:
                form.add_error(None, "Эталонные решения не прошли часть тестов — задачи не созданы. "
                                     "Исправьте файл или нажмите «Всё равно импортировать».")
            else:
                lesson = form.cleaned_data["lesson"]
                created = importer.create_tasks(lesson, tasks, form.cleaned_data["is_open"])
                n_tests = sum(t.tests.count() for t in created)
                messages.success(request, f"Добавлено задач: {len(created)}, тестов: {n_tests}.")
                return redirect("t_lessons")
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
        messages.success(request, "Решение отмечено.")
        nxt = Submission.objects.filter(status=Submission.REVIEW).order_by("created_at").first()
        if nxt and "next_review" in request.POST:
            return redirect("t_submission", nxt.pk)
        return redirect("t_submission", pk)
    others = Submission.objects.filter(user=sub.user, task=sub.task).only("id", "status", "created_at")
    return render(request, "core/teacher/submission_detail.html", {"sub": sub, "form": form, "others": others})


@teacher_required
@require_POST
def submission_rejudge(request, pk):
    sub = get_object_or_404(Submission, pk=pk)
    sub.status = Submission.PENDING
    sub.save(update_fields=["status"])
    queue.enqueue(sub.pk)
    messages.success(request, "Отправка поставлена на перепроверку.")
    return redirect("t_submission", pk)


@teacher_required
@require_POST
def requeue_stale(request):
    n = queue.requeue_stale(minutes=2)
    messages.success(request, f"Возвращено в очередь: {n}.")
    return redirect("t_submissions")


# --- Сводная таблица -------------------------------------------------------------

@teacher_required
def results(request):
    groups_qs = StudyGroup.objects.all()
    sections_qs = ClassSection.objects.all()
    lessons_qs = Lesson.objects.all()
    # Кого показывать: «s12» — занятие, «g3» — группа. Старые ссылки ?group=3 тоже работают.
    who = request.GET.get("who") or (f"g{request.GET['group']}" if request.GET.get("group") else "")
    target, students_qs, by_section = None, None, False
    if who.startswith("s") and who[1:].isdigit():
        target = sections_qs.filter(pk=who[1:]).first()
        by_section = True
    elif who.startswith("g") and who[1:].isdigit():
        target = groups_qs.filter(pk=who[1:]).first()
    if target is None:
        target = sections_qs.first() or groups_qs.first()
        by_section = isinstance(target, ClassSection)
    if target is not None:
        who = f"{'s' if by_section else 'g'}{target.pk}"
        students_qs = target.students.select_related("group")

    lesson_id = request.GET.get("lesson", "")
    tasks = Task.objects.select_related("lesson")
    if lesson_id:
        tasks = tasks.filter(lesson_id=lesson_id)
    tasks = list(tasks)
    rows = []
    if students_qs is not None:
        students = list(students_qs.order_by("group__name", "full_name") if by_section else students_qs)
        best, attempts = best_submissions([s.id for s in students], [t.id for t in tasks])
        for s in students:
            cells = [(t, best.get((s.id, t.id)), attempts.get((s.id, t.id), 0)) for t in tasks]
            rows.append((s, cells, sum(1 for _, b, _ in cells if b and b.is_good)))
    return render(request, "core/teacher/results.html", {
        "lessons": lessons_qs,
        "section_opts": [(f"s{x.pk}", str(x)) for x in sections_qs],
        "group_opts": [(f"g{x.pk}", x.name) for x in groups_qs],
        "target": target, "who": who, "by_section": by_section,
        "lesson_id": lesson_id, "tasks": tasks, "rows": rows,
    })


# --- Журнал входов и IP ---

PERIODS = {"3h": ("3 часа", 3), "day": ("сутки", 24), "week": ("неделя", 24 * 7)}


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
        messages.success(request, "Настройки сохранены. "
                         + ("Один вход на аккаунт — включено." if obj.single_session else "Один вход на аккаунт — выключено."))
        return redirect(request.POST.get("next") or "t_settings")
    return render(request, "core/teacher/settings.html", {"s": obj})


# --- Песочница -------------------------------------------------------------------

@teacher_required
def sandbox_check(request):
    report = sandbox.selftest() if request.method == "POST" else None
    return render(request, "core/teacher/sandbox.html", {"report": report})
