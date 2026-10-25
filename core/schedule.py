"""Расписание пар занятия и определение, решена задача на паре или дома.

Расписание задаётся строкой в настройках занятия, по паре через «;» или с новой строки:
    Вт 15:40-17:40
    Пт 14:40-16:40; Пн 9:00-10:50
Дни: пн вт ср чт пт сб вс / дс сс ср бс жм сн жк / mon tue wed thu fri sat sun.
"""
import re
from collections import defaultdict
from datetime import time, timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext as _

import constants

# Отправку чуть раньше начала или чуть позже конца пары тоже считаем «на паре»
GRACE = timedelta(minutes=getattr(constants, "CLASS_GRACE_MINUTES", 10))

DAYS = {
    0: ("пн", "понедельник", "дс", "дүйсенбі", "mon", "monday", "mo"),
    1: ("вт", "вторник", "сс", "сейсенбі", "tue", "tuesday", "tu"),
    2: ("ср", "среда", "сәрсенбі", "wed", "wednesday", "we"),
    3: ("чт", "четверг", "бс", "бейсенбі", "thu", "thursday", "th"),
    4: ("пт", "пятница", "жм", "жұма", "fri", "friday", "fr"),
    5: ("сб", "суббота", "сн", "сенбі", "sat", "saturday", "sa"),
    6: ("вс", "воскресенье", "жк", "жексенбі", "sun", "sunday", "su"),
}
DAY_BY_NAME = {name: d for d, names in DAYS.items() for name in names}
SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

SLOT_RE = re.compile(r"^\s*([^\W\d_]+)\.?\s+(\d{1,2})[:.](\d{2})\s*[-–—]\s*(\d{1,2})[:.](\d{2})\s*$")


def parse(text):
    """'Вт 15:40-17:40; Пт 14:40-16:40' → [(1, time(15,40), time(17,40)), …]. Ошибка — ValidationError."""
    slots = []
    for part in re.split(r"[;\n]+", text or ""):
        if not part.strip():
            continue
        m = SLOT_RE.match(part)
        if not m:
            raise ValidationError(_("Не понял «%(p)s». Пример: Вт 15:40-17:40") % {"p": part.strip()})
        day = DAY_BY_NAME.get(m.group(1).lower())
        if day is None:
            raise ValidationError(_("Неизвестный день недели «%(d)s». Пишите Пн, Вт, Ср, Чт, Пт, Сб, Вс") % {"d": m.group(1)})
        h1, m1, h2, m2 = (int(x) for x in m.groups()[1:])
        if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59 or (h1, m1) >= (h2, m2):
            raise ValidationError(_("Неверное время в «%(p)s»") % {"p": part.strip()})
        slots.append((day, time(h1, m1), time(h2, m2)))
    return slots


def safe_parse(text):
    try:
        return parse(text)
    except ValidationError:
        return []


def fmt(slots):
    return "; ".join(f"{SHORT[d]} {a:%H:%M}-{b:%H:%M}" for d, a, b in slots)


def in_slots(dt, slots):
    """Попадает ли момент (aware datetime) в одну из пар (с запасом GRACE)."""
    local = timezone.localtime(dt)
    for day, start, end in slots:
        begin = local.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
        finish = local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
        # день недели проверяем по началу пары, запас может перейти через полночь — не страшно
        if local.weekday() == day and begin - GRACE <= local <= finish + GRACE:
            return True
    return False


def slots_for_users(user_ids):
    """{user_id: [пары всех его занятий]}. Студента без расписания в словаре нет."""
    from .models import User
    result = defaultdict(list)
    rows = User.sections.through.objects.filter(user_id__in=user_ids).select_related("classsection")
    for row in rows:
        slots = safe_parse(row.classsection.schedule)
        if slots:
            result[row.user_id].extend(slots)
    return dict(result)


def place(dt, slots):
    """'class' — на паре, 'home' — вне пары, None — расписание неизвестно."""
    if not slots:
        return None
    return "class" if in_slots(dt, slots) else "home"


def lesson_activity(user_ids, lesson_ids):
    """Первые верные решения по задачам уроков, разделённые на пару и дом (за всё время).

    Возвращает {(user_id, lesson_id): {"class": n, "home": m, "tasks": всего задач в уроке,
                                        "class_titles": [...], "home_titles": [...]}}.
    """
    from .models import Submission, Task
    slots = slots_for_users(user_ids)
    total = defaultdict(int)
    for row in Task.objects.filter(lesson_id__in=lesson_ids).values("lesson_id"):
        total[row["lesson_id"]] += 1
    first = {}
    qs = (Submission.objects.filter(user_id__in=user_ids, task__lesson_id__in=lesson_ids, status__in=Submission.GOOD)
          .select_related("task").order_by("created_at"))
    for sub in qs:
        first.setdefault((sub.user_id, sub.task_id), sub)
    out = {}
    for uid in user_ids:
        for lid in lesson_ids:
            out[(uid, lid)] = {"class": 0, "home": 0, "tasks": total[lid], "known": uid in slots,
                               "class_titles": [], "home_titles": []}
    for (uid, _tid), sub in first.items():
        item = out.get((uid, sub.task.lesson_id))
        if item is None:
            continue
        where = place(sub.created_at, slots.get(uid))
        if where:
            item[where] += 1
            item[f"{where}_titles"].append(sub.task.title)
    return out
