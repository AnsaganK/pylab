"""Импорт задач из JSON.

Формат:
{
  "tasks": [
    {
      "title": "Название",                 # строка — на языке, выбранном в форме импорта,
      "statement": "Условие (Markdown)",   # или словарь {"ru": "...", "kk": "...", "en": "..."}
      "input_format": "...", "output_format": "...",
      "kind": "io" | "manual",            # по умолчанию io
      "time_limit": 1, "memory_limit": 256, # необязательно
      "starter_code": "",                   # необязательно
      "solution": "эталонное решение",      # необязательно, только для проверки тестов
      "tests": [{"input": "...", "output": "...", "sample": true}, ...]
    }
  ]
}
"""
import json

from django.db import transaction

import constants
from django.utils.translation import gettext as _

from .judge import sandbox
from .judge.checker import outputs_match
from .models import Task, TestCase


class TaskFileError(Exception):
    pass


LANGS = ("ru", "kk", "en")
ML_FIELDS = ("title", "statement", "input_format", "output_format")


def ml_values(value, default_lang):
    """Строка → {язык_импорта: строка}; словарь → только известные языки."""
    if isinstance(value, dict):
        return {k: str(v) for k, v in value.items() if k in LANGS and v is not None and str(v).strip()}
    if value is None or not str(value).strip():
        return {}
    return {default_lang: str(value)}


def display_title(t, default_lang="ru"):
    vals = ml_values(t.get("title"), default_lang) if isinstance(t, dict) else {}
    for code in (default_lang,) + LANGS:
        if vals.get(code):
            return vals[code]
    return "?"


def parse(raw: str, default_lang="ru"):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise TaskFileError(_("Файл не похож на JSON: строка %(line)s, позиция %(col)s: %(msg)s") % {"line": e.lineno, "col": e.colno, "msg": e.msg})
    tasks = data.get("tasks") if isinstance(data, dict) else data
    if not isinstance(tasks, list) or not tasks:
        raise TaskFileError(_("В файле нет списка задач (ключ \"tasks\")."))
    for i, t in enumerate(tasks, 1):
        if not isinstance(t, dict) or not ml_values(t.get("title"), default_lang):
            raise TaskFileError(_("У задачи №%(n)s нет названия (\"title\").") % {"n": i})
        t["_title"] = display_title(t, default_lang)
        kind = t.get("kind", Task.KIND_IO)
        if kind not in (Task.KIND_IO, Task.KIND_MANUAL):
            raise TaskFileError(_("Задача «%(title)s»: kind должен быть \"io\" или \"manual\".") % {"title": t["_title"]})
        tests = t.get("tests", [])
        if not isinstance(tests, list):
            raise TaskFileError(_("Задача «%(title)s»: tests должен быть списком.") % {"title": t["_title"]})
        if kind == Task.KIND_IO and not tests:
            raise TaskFileError(_("Задача «%(title)s»: для автопроверки нужны тесты.") % {"title": t["_title"]})
        for j, tc in enumerate(tests, 1):
            if not isinstance(tc, dict) or "output" not in tc:
                raise TaskFileError(_("Задача «%(title)s», тест %(n)s: нужен ключ \"output\".") % {"title": t["_title"], "n": j})
    return tasks


def verify_solutions(tasks):
    """Прогоняет эталонные решения. Возвращает список предупреждений."""
    warnings = []
    for t in tasks:
        sol = t.get("solution")
        if not sol or t.get("kind", Task.KIND_IO) != Task.KIND_IO:
            continue
        tl = float(t.get("time_limit", constants.DEFAULT_TIME_LIMIT))
        ml = int(t.get("memory_limit", constants.DEFAULT_MEMORY_LIMIT))
        for j, tc in enumerate(t["tests"], 1):
            res = sandbox.run_program(sol, str(tc.get("input", "")), tl, ml)
            if res.status != sandbox.OK:
                warnings.append(_("«%(title)s», тест %(n)s: эталон завершился с ошибкой (%(status)s). %(err)s") % {"title": t["_title"], "n": j, "status": res.status, "err": res.stderr[-200:]})
                break
            if not outputs_match(str(tc["output"]), res.stdout):
                warnings.append(_("«%(title)s», тест %(n)s: эталон вывел «%(got)s», в тесте «%(expected)s».") % {"title": t["_title"], "n": j, "got": res.stdout.strip()[:60], "expected": str(tc["output"]).strip()[:60]})
    return warnings


@transaction.atomic
def create_tasks(lesson, tasks, is_open=True, default_lang="ru"):
    start = lesson.tasks.count()
    created = []
    for i, t in enumerate(tasks, 1):
        texts = {}
        for field in ML_FIELDS:
            for code, value in ml_values(t.get(field), default_lang).items():
                texts[f"{field}_{code}"] = value.strip()[:200] if field == "title" else value
        task = Task.objects.create(
            lesson=lesson,
            **texts,
            kind=t.get("kind", Task.KIND_IO),
            time_limit=float(t.get("time_limit", constants.DEFAULT_TIME_LIMIT)),
            memory_limit=int(t.get("memory_limit", constants.DEFAULT_MEMORY_LIMIT)),
            starter_code=t.get("starter_code", ""),
            order=start + i,
            is_open=is_open,
        )
        TestCase.objects.bulk_create([
            TestCase(task=task, input_data=str(tc.get("input", "")), output_data=str(tc["output"]),
                     is_sample=bool(tc.get("sample")), order=k)
            for k, tc in enumerate(t.get("tests", []))
        ])
        created.append(task)
    return created
