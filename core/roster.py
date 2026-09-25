"""Разбор списка студентов из расписания.

Понимает строки вида:
    1) Аханбай Данира Нышанбайқызы (МИК241) 49562
    Ахмет Зарина Султанқызы 49533          — без группы: берётся группа из формы
Строки без ID в конце (например, «43554) Программирование на Python ЛЗ») пропускаются.
"""
import re

from django.contrib.auth import get_user_model
from django.db import transaction

from .models import StudyGroup

User = get_user_model()

LINE_RE = re.compile(
    r"^\s*(?:\d+\s*[).]\s*)?"            # номер строки «1)» или «1.»
    r"(?P<name>[^()\d]+?)\s*"             # ФИО
    r"(?:\((?P<group>[^)]+)\)\s*)?"       # (МИК241)
    r"(?P<sid>\d{3,})\s*$"                # ID
)


def _norm(name):
    return re.sub(r"[\s\-_]", "", name).upper()


def parse(text):
    """-> (записи [{name, group, sid, line}], нераспознанные строки)"""
    rows, skipped = [], []
    for line in text.splitlines():
        if not line.strip():
            continue
        m = LINE_RE.match(line)
        if not m:
            skipped.append(line.strip())
            continue
        rows.append({
            "name": " ".join(m["name"].split()),
            "group": (m["group"] or "").strip(),
            "sid": m["sid"],
            "line": line.strip(),
        })
    return rows, skipped


def find_group(code, cache):
    """«МИК241» находит уже созданную «МИК-241»; если такой нет — создаёт."""
    key = _norm(code)
    if key not in cache:
        existing = {_norm(g.name): g for g in StudyGroup.objects.all()}
        cache[key] = existing.get(key) or StudyGroup.objects.create(name=code)
    return cache[key]


@transaction.atomic
def apply(rows, default_group=None):
    """Создаёт или обновляет студентов. Возвращает отчёт по строкам."""
    report = {"created": [], "updated": [], "errors": []}
    cache = {}
    for r in rows:
        if r["group"]:
            group = find_group(r["group"], cache)
        elif default_group:
            group = default_group
        else:
            report["errors"].append((r["line"], "не указана группа — выберите её в форме"))
            continue
        user = User.objects.filter(username=r["sid"]).first()
        if user and user.is_staff:
            report["errors"].append((r["line"], "этот ID занят аккаунтом преподавателя"))
            continue
        if user:
            user.full_name = r["name"]
            user.group = group
            user.id_login = True
            user.is_active = True
            user.save()
            report["updated"].append(user)
        else:
            user = User(username=r["sid"], full_name=r["name"], group=group, id_login=True)
            user.set_unusable_password()
            user.save()
            report["created"].append(user)
    return report
