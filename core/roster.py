"""Разбор списка студентов из расписания.

Понимает:
    74469) Программирование на Python ЛЗ            — заголовок: код занятия и название
    1) Иванов Иван Иванович (МИК241) 10001          — студент: ФИО, группа, ID
    Ахмет Зарина 49533                              — без группы: берётся группа из формы
В одном тексте может быть несколько занятий: студенты относятся к ближайшему заголовку выше.
"""
import re

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.translation import gettext as _

from .models import ClassSection, StudyGroup

User = get_user_model()

STUDENT_RE = re.compile(
    r"^\s*(?:\d+\s*[).]\s*)?"            # номер строки «1)» или «1.»
    r"(?P<name>[^()\d]+?)\s*"             # ФИО
    r"(?:\((?P<group>[^)]+)\)\s*)?"       # (МИК241)
    r"(?P<sid>\d{3,})\s*$"                # ID
)
HEADER_RE = re.compile(r"^\s*(?P<code>\d{3,})\s*[).]\s*(?P<title>.*\D)?\s*$")


def _norm(name):
    return re.sub(r"[\s\-_]", "", name).upper()


def parse(text):
    """-> (записи [{name, group, sid, section, section_title, line}], нераспознанные строки)"""
    rows, skipped = [], []
    section, section_title = "", ""
    for line in text.splitlines():
        if not line.strip():
            continue
        m = STUDENT_RE.match(line)
        if m:
            rows.append({
                "name": " ".join(m["name"].split()),
                "group": (m["group"] or "").strip(),
                "sid": m["sid"],
                "section": section,
                "section_title": section_title,
                "line": line.strip(),
            })
            continue
        h = HEADER_RE.match(line)
        if h:
            section = h["code"]
            section_title = " ".join((h["title"] or "").split())
            continue
        skipped.append(line.strip())
    return rows, skipped


def find_group(code, cache):
    """«МИК241» находит уже созданную «МИК-241»; если такой нет — создаёт."""
    key = _norm(code)
    if key not in cache:
        existing = {_norm(g.name): g for g in StudyGroup.objects.all()}
        cache[key] = existing.get(key) or StudyGroup.objects.create(name=code)
    return cache[key]


def find_section(code, title, cache):
    if code not in cache:
        section, created = ClassSection.objects.get_or_create(code=code, defaults={"title": title})
        if not created and title and section.title != title:
            section.title = title
            section.save(update_fields=["title"])
        cache[code] = section
    return cache[code]


@transaction.atomic
def apply(rows, default_group=None):
    """Создаёт или обновляет студентов. Возвращает отчёт."""
    report = {"created": [], "updated": [], "errors": [], "sections": set()}
    groups, sections = {}, {}
    for r in rows:
        if r["group"]:
            group = find_group(r["group"], groups)
        elif default_group:
            group = default_group
        else:
            report["errors"].append((r["line"], _("не указана группа — выберите её в форме")))
            continue
        user = User.objects.filter(username=r["sid"]).first()
        if user and user.is_staff:
            report["errors"].append((r["line"], _("этот ID занят аккаунтом преподавателя")))
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
        if r["section"]:
            section = find_section(r["section"], r["section_title"], sections)
            user.sections.add(section)
            report["sections"].add(section.pk)
    return report
