"""Выгрузка задач урока в PDF — на случай, если сайт на паре недоступен.

PDF собирает WeasyPrint (pip install weasyprint; на Ubuntu ещё
sudo apt install libpango-1.0-0 libpangoft2-1.0-0). Если его нет
(например, на Windows), открывается страница для печати —
«Сохранить как PDF» в браузере даёт тот же результат.
"""
from pathlib import Path

import markdown
from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.utils.safestring import mark_safe

import constants

FONTS_DIR = Path(settings.STATICFILES_DIRS[0]) / "fonts"


def _md(text):
    return mark_safe(markdown.markdown(text or "", extensions=["fenced_code", "tables", "nl2br"]))


def _css_string(text):
    """Текст для content: "..." в @page — экранируем для CSS, а не для HTML."""
    text = str(text).replace("\\", "\\\\").replace('"', '\\"').replace("<", "\\3C ").replace("\n", " ")
    return mark_safe(text)


def _task_blocks(tasks):
    blocks = []
    for n, task in enumerate(tasks, 1):
        tests = list(task.tests.all())
        samples = [t for t in tests if t.is_sample]
        has_input = bool(task.input_format.strip()) or any(t.input_data.strip() for t in tests) or not tests
        blocks.append({
            "n": n, "task": task,
            "statement": _md(task.statement),
            "input_format": _md(task.input_format),
            "output_format": _md(task.output_format),
            "samples": samples, "has_input": has_input,
        })
    return blocks


def render_lesson(request, lesson, tasks, filename):
    course = getattr(constants, "PDF_COURSE_TITLE", "Python Programming")
    site_url = request.build_absolute_uri("/")
    context = {
        "lesson": lesson,
        "header_css": _css_string(f"{course} · {lesson.title}"),
        "site_css": _css_string(site_url),
        "blocks": _task_blocks(tasks),
        "course": course,
        "site_url": site_url,
        "generated": timezone.localtime(),
        "fonts": FONTS_DIR.as_uri(),
    }
    try:
        from weasyprint import HTML
    except Exception:  # нет WeasyPrint или системных библиотек — печать из браузера
        context["print_mode"] = True
        context["fonts"] = settings.STATIC_URL.rstrip("/") + "/fonts"
        return HttpResponse(render_to_string("core/pdf/lesson.html", context, request=request))

    html = render_to_string("core/pdf/lesson.html", context, request=request)
    pdf = HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = content_disposition_header(as_attachment=True, filename=filename)
    return response
