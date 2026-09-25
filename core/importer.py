"""Импорт задач из JSON.

Формат:
{
  "tasks": [
    {
      "title": "Название",
      "statement": "Условие (Markdown)",
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

from .judge import sandbox
from .judge.checker import outputs_match
from .models import Task, TestCase


class TaskFileError(Exception):
    pass


def parse(raw: str):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise TaskFileError(f"Файл не похож на JSON: строка {e.lineno}, позиция {e.colno}: {e.msg}")
    tasks = data.get("tasks") if isinstance(data, dict) else data
    if not isinstance(tasks, list) or not tasks:
        raise TaskFileError("В файле нет списка задач (ключ \"tasks\").")
    for i, t in enumerate(tasks, 1):
        if not isinstance(t, dict) or not str(t.get("title", "")).strip():
            raise TaskFileError(f"У задачи №{i} нет названия (\"title\").")
        kind = t.get("kind", Task.KIND_IO)
        if kind not in (Task.KIND_IO, Task.KIND_MANUAL):
            raise TaskFileError(f"Задача «{t['title']}»: kind должен быть \"io\" или \"manual\".")
        tests = t.get("tests", [])
        if not isinstance(tests, list):
            raise TaskFileError(f"Задача «{t['title']}»: tests должен быть списком.")
        if kind == Task.KIND_IO and not tests:
            raise TaskFileError(f"Задача «{t['title']}»: для автопроверки нужны тесты.")
        for j, tc in enumerate(tests, 1):
            if not isinstance(tc, dict) or "output" not in tc:
                raise TaskFileError(f"Задача «{t['title']}», тест {j}: нужен ключ \"output\".")
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
                warnings.append(f"«{t['title']}», тест {j}: эталон завершился с ошибкой ({res.status}). {res.stderr[-200:]}")
                break
            if not outputs_match(str(tc["output"]), res.stdout):
                warnings.append(f"«{t['title']}», тест {j}: эталон вывел «{res.stdout.strip()[:60]}», в тесте «{str(tc['output']).strip()[:60]}».")
    return warnings


@transaction.atomic
def create_tasks(lesson, tasks, is_open=True):
    start = lesson.tasks.count()
    created = []
    for i, t in enumerate(tasks, 1):
        task = Task.objects.create(
            lesson=lesson,
            title=str(t["title"]).strip()[:200],
            statement=t.get("statement", ""),
            input_format=t.get("input_format", ""),
            output_format=t.get("output_format", ""),
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
