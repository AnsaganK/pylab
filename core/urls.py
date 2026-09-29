from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views_auth as a
from . import views_student as s
from . import views_teacher as t

urlpatterns = [
    path("", a.home, name="home"),
    path("login/", a.login_view, name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("setup/", a.setup, name="setup"),

    # Студент
    path("lessons/", s.lessons, name="s_lessons"),
    path("task/<int:pk>/", s.task_page, name="s_task"),
    path("task/<int:pk>/run/", s.run_code, name="s_run"),
    path("task/<int:pk>/submit/", s.submit, name="s_submit"),
    path("task/<int:pk>/subs/", s.submissions_fragment, name="s_subs"),
    path("submission/<int:pk>/", s.my_submission, name="s_submission"),

    # Преподаватель
    path("t/", t.dashboard, name="t_dashboard"),
    path("t/groups/", t.groups, name="t_groups"),
    path("t/groups/<int:pk>/", t.group_detail, name="t_group"),
    path("t/groups/<int:pk>/delete/", t.group_delete, name="t_group_delete"),
    path("t/sections/<int:pk>/", t.section_detail, name="t_section"),
    path("t/sections/<int:pk>/delete/", t.section_delete, name="t_section_delete"),
    path("t/students/new/", t.student_create, name="t_student_new"),
    path("t/students/bulk/", t.students_bulk, name="t_students_bulk"),
    path("t/students/roster/", t.students_roster, name="t_students_roster"),
    path("t/students/<int:pk>/", t.student_edit, name="t_student"),
    path("t/students/<int:pk>/password/", t.student_reset_password, name="t_student_password"),
    path("t/students/<int:pk>/delete/", t.student_delete, name="t_student_delete"),
    path("t/lessons/", t.lessons, name="t_lessons"),
    path("t/lessons/new/", t.lesson_form, name="t_lesson_new"),
    path("t/lessons/<int:pk>/", t.lesson_form, name="t_lesson_edit"),
    path("t/lessons/<int:pk>/delete/", t.lesson_delete, name="t_lesson_delete"),
    path("t/lessons/<int:pk>/pdf/", t.lesson_pdf, name="t_lesson_pdf"),
    path("t/tasks/<int:pk>/pdf/", t.task_pdf, name="t_task_pdf"),
    path("t/lessons/<int:pk>/toggle/<int:group_id>/", t.lesson_toggle_group, name="t_lesson_toggle"),
    path("t/lessons/<int:pk>/toggle-section/<int:section_id>/", t.lesson_toggle_section, name="t_lesson_toggle_section"),
    path("t/tasks/new/", t.task_form, name="t_task_new"),
    path("t/tasks/import/", t.tasks_import, name="t_tasks_import"),
    path("t/tasks/<int:pk>/", t.task_form, name="t_task_edit"),
    path("t/tasks/<int:pk>/toggle/", t.task_toggle, name="t_task_toggle"),
    path("t/tasks/<int:pk>/delete/", t.task_delete, name="t_task_delete"),
    path("t/tasks/<int:pk>/rejudge/", t.task_rejudge, name="t_task_rejudge"),
    path("t/submissions/", t.submissions, name="t_submissions"),
    path("t/submissions/<int:pk>/", t.submission_detail, name="t_submission"),
    path("t/submissions/<int:pk>/rejudge/", t.submission_rejudge, name="t_submission_rejudge"),
    path("t/submissions/requeue/", t.requeue_stale, name="t_requeue"),
    path("t/results/", t.results, name="t_results"),
    path("t/sandbox/", t.sandbox_check, name="t_sandbox"),
    path("t/log/", t.access_log, name="t_log"),
    path("t/settings/", t.site_settings, name="t_settings"),
]
