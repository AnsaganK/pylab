"""Признаки списывания и решений, сгенерированных ИИ.

Это не доказательство, а повод посмотреть на решение внимательнее и, например,
попросить студента объяснить код или изменить его при вас. Каждое верное решение
получает список признаков и сумму баллов; чем больше баллов — тем выше в списке.
"""
import builtins
import difflib
import io
import keyword
import re
import tokenize
from collections import defaultdict
from datetime import timedelta

from django.utils import timezone
from django.utils.translation import gettext as _

from . import schedule
from .models import AccessLog, Submission

# Пороги — можно подстроить под свою группу
FAST_SECONDS = 90          # верное решение быстрее, чем через столько секунд после открытия задачи
MIN_CODE_LEN = 40          # совсем короткий код (print(...)) не оцениваем на скорость и вставку
PASTE_SHARE = 0.5          # вставлено не меньше этой доли кода
BURST_WINDOW = timedelta(minutes=15)
BURST_COUNT = 5            # столько верных задач за окно — «серия»
SIMILARITY = 0.9           # похожесть кода с другим студентом после нормализации
AWAY_SECONDS = 20          # суммарно столько секунд страница была скрыта
# Работа на паре (нужно расписание занятия): по задачам того же урока
CLASS_WEAK = 1             # на паре решено не больше стольких задач урока…
HOME_STRONG = 3            # …а дома — не меньше стольких: «дома сильнее, чем на паре»
CLASS_GOOD = 3             # на паре решено столько задач урока и больше — домашним решениям доверия больше

WEIGHTS = {"paste": 3, "similar": 3, "similar_src": 1, "fast": 2, "away": 2, "burst": 2, "ai": 2, "first_try": 1,
           "home_strong": 2, "class_good": -2}

BUILTINS = set(dir(builtins))


def normalize(code):
    """Код без комментариев и с одинаковыми именами переменных: a, total → V."""
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            if tok.type in (tokenize.COMMENT, tokenize.NL, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING):
                continue
            if tok.type == tokenize.NEWLINE:
                out.append(";")
            elif tok.type == tokenize.NAME and not keyword.iskeyword(tok.string) and tok.string not in BUILTINS:
                out.append("V")
            elif tok.type == tokenize.STRING:
                out.append("S")
            elif tok.type != tokenize.ENDMARKER:
                out.append(tok.string)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return re.sub(r"\s+", " ", code)
    return " ".join(out)


AI_MARKERS = [
    (re.compile(r'^\s*(""".*?"""|\'\'\'.*?\'\'\')', re.S | re.M), "docstring"),
    (re.compile(r"if\s+__name__\s*==\s*['\"]__main__['\"]"), "if __name__"),
    (re.compile(r"^\s*def\s+main\s*\(", re.M), "def main()"),
    (re.compile(r"def\s+\w+\s*\([^)]*:\s*\w+[^)]*\)|\)\s*->\s*\w+"), "type hints"),
    (re.compile(r"^\s*try\s*:", re.M), "try/except"),
    (re.compile(r"sys\.stdin"), "sys.stdin"),
    (re.compile(r"\braise\s+\w+Error\b"), "raise"),
    (re.compile(r"f[\"'][^\"']*\{[^}]+:[^}]+\}"), "f-string format"),
]


def ai_markers(code):
    """Приёмы, типичные для кода от ИИ и редкие у начинающих."""
    found = [label for rx, label in AI_MARKERS if rx.search(code)]
    comments = [l for l in code.splitlines() if l.strip().startswith("#")]
    if len(comments) >= 3:
        found.append(_("комментарии (%(n)s)") % {"n": len(comments)})
    return found


def _fmt_dur(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return _("%(s)s с") % {"s": seconds}
    return _("%(m)s мин %(s)s с") % {"m": seconds // 60, "s": seconds % 60}


def analyze(user_ids, task_ids, since=None, until=None):
    """Первые верные решения студентов с признаками. Возвращает (решения, сводка по студентам)."""
    qs = (Submission.objects
          .filter(user_id__in=user_ids, task_id__in=task_ids, status__in=Submission.GOOD)
          .select_related("user", "user__group", "task", "task__lesson")
          .order_by("created_at"))
    if since:
        qs = qs.filter(created_at__gte=since)
    if until:
        qs = qs.filter(created_at__lt=until)

    first_ok = {}
    for sub in qs:
        first_ok.setdefault((sub.user_id, sub.task_id), sub)
    subs = sorted(first_ok.values(), key=lambda x: x.created_at)
    if not subs:
        return [], []

    # Сколько было отправок до этого верного решения
    attempts_before = defaultdict(int)
    for row in (Submission.objects.filter(user_id__in=user_ids, task_id__in=task_ids)
                .values("user_id", "task_id", "created_at")):
        key = (row["user_id"], row["task_id"])
        ok = first_ok.get(key)
        if ok and row["created_at"] < ok.created_at:
            attempts_before[key] += 1

    # Открытия задачи и запуски из журнала (пишутся с этого обновления)
    first_open, runs_before = {}, defaultdict(int)
    logs = (AccessLog.objects.filter(user_id__in=user_ids, task_id__in=task_ids,
                                     action__in=[AccessLog.OPEN, AccessLog.RUN])
            .values("user_id", "task_id", "action", "created_at").order_by("created_at"))
    for row in logs:
        key = (row["user_id"], row["task_id"])
        ok = first_ok.get(key)
        if not ok or row["created_at"] > ok.created_at:
            continue
        if row["action"] == AccessLog.OPEN:
            first_open.setdefault(key, row["created_at"])
        else:
            runs_before[key] += 1

    # Похожие решения: сравниваем с первыми верными решениями задачи у ВСЕХ студентов
    # и за всё время (списать могли у другой пары или раньше периода)
    pool = defaultdict(dict)
    for other in (Submission.objects.filter(task_id__in={s.task_id for s in subs}, status__in=Submission.GOOD)
                  .select_related("user").order_by("created_at")):
        pool[other.task_id].setdefault(other.user_id, other)
    norm_cache = {}

    def norm(s):
        if s.pk not in norm_cache:
            norm_cache[s.pk] = normalize(s.code)
        return norm_cache[s.pk]

    similar = {}   # pk -> (ratio, другое решение, сдано ли другое раньше)
    for sub in subs:
        if len(sub.code) < MIN_CODE_LEN:
            continue
        for other in pool[sub.task_id].values():
            if other.user_id == sub.user_id or len(other.code) < MIN_CODE_LEN:
                continue
            ratio = difflib.SequenceMatcher(None, norm(sub), norm(other)).ratio()
            if ratio < SIMILARITY:
                continue
            earlier = other.created_at < sub.created_at
            best = similar.get(sub.pk)
            # «списал у того, кто сдал раньше» важнее, чем «у меня списали»
            if best is None or (earlier, ratio) > (best[2], best[0]):
                similar[sub.pk] = (ratio, other, earlier)

    # Серии: много верных задач за короткое время
    burst = {}
    by_user = defaultdict(list)
    for sub in subs:
        by_user[sub.user_id].append(sub)
    for items in by_user.values():
        j = 0
        for i in range(len(items)):
            while items[i].created_at - items[j].created_at > BURST_WINDOW:
                j += 1
            window = items[j:i + 1]
            if len(window) >= BURST_COUNT:
                minutes = max(1, round((window[-1].created_at - window[0].created_at).total_seconds() / 60))
                for s in window:
                    if len(window) > burst.get(s.pk, (0, 0))[0]:
                        burst[s.pk] = (len(window), minutes)

    # На паре или дома — по расписанию занятий студента
    slots = schedule.slots_for_users(list({s.user_id for s in subs}))
    activity = schedule.lesson_activity(list({s.user_id for s in subs}), list({s.task.lesson_id for s in subs}))

    results = []
    for sub in subs:
        key = (sub.user_id, sub.task_id)
        flags = []
        where = schedule.place(sub.created_at, slots.get(sub.user_id))
        act = activity.get((sub.user_id, sub.task.lesson_id))
        code_len = len(sub.code)
        if sub.pasted_chars is not None and code_len >= MIN_CODE_LEN and sub.pasted_chars >= PASTE_SHARE * code_len:
            flags.append(("paste", _("вставлено %(p)s из %(n)s символов, напечатано %(t)s") % {
                "p": sub.pasted_chars, "n": code_len, "t": sub.typed_chars or 0}))
        if sub.pk in similar:
            ratio, other, earlier = similar[sub.pk]
            params = {"r": round(ratio * 100), "name": other.user.display_name(),
                      "when": timezone.localtime(other.created_at).strftime("%d.%m %H:%M")}
            if earlier:
                flags.append(("similar", _("код на %(r)s%% совпадает с решением %(name)s, сданным раньше (%(when)s)") % params))
            else:
                flags.append(("similar_src", _("с этим кодом на %(r)s%% совпадает решение %(name)s, сданное позже (%(when)s)") % params))
        opened = first_open.get(key)
        if opened and code_len >= MIN_CODE_LEN:
            spent = (sub.created_at - opened).total_seconds()
            if spent < FAST_SECONDS:
                flags.append(("fast", _("решено через %(d)s после открытия задачи") % {"d": _fmt_dur(spent)}))
        if (sub.away_seconds or 0) >= AWAY_SECONDS:
            flags.append(("away", _("уходил со страницы %(n)s раз, всего %(d)s") % {
                "n": sub.away_count, "d": _fmt_dur(sub.away_seconds)}))
        if sub.pk in burst:
            n, minutes = burst[sub.pk]
            flags.append(("burst", _("серия: %(n)s верных задач за %(m)s мин") % {"n": n, "m": minutes}))
        markers = ai_markers(sub.code)
        if len(markers) >= 2:
            flags.append(("ai", _("приёмы, нетипичные для начинающих: %(list)s") % {"list": ", ".join(markers)}))
        if attempts_before[key] == 0 and runs_before[key] == 0 and code_len >= MIN_CODE_LEN and key in first_open:
            flags.append(("first_try", _("с первой попытки, без единого запуска")))
        if where == "home" and act:
            params = {"c": act["class"], "h": act["home"], "n": act["tasks"]}
            if act["class"] <= CLASS_WEAK and act["home"] >= HOME_STRONG:
                flags.append(("home_strong", _("решено дома; по этому уроку на паре решено %(c)s из %(n)s, дома — %(h)s") % params))
            elif act["class"] >= CLASS_GOOD:
                flags.append(("class_good", _("решено дома, но на паре по этому уроку сам решил %(c)s из %(n)s — доверия больше") % params))
        score = max(0, sum(WEIGHTS[k] for k, _text in flags))
        opened = first_open.get(key)
        results.append({"sub": sub, "flags": flags, "score": score, "place": where, "activity": act,
                        "attempts": attempts_before[key] + 1, "runs": runs_before[key],
                        "similar_other": similar[sub.pk][1] if sub.pk in similar else None,
                        "spent": (sub.created_at - opened).total_seconds() if opened else None})

    results.sort(key=lambda r: (-r["score"], r["sub"].created_at))
    per_student = defaultdict(lambda: {"user": None, "solved": 0, "flagged": 0, "score": 0, "in_class": 0, "at_home": 0})
    for r in results:
        item = per_student[r["sub"].user_id]
        item["user"] = r["sub"].user
        item["solved"] += 1
        if r["place"] == "class":
            item["in_class"] += 1
        elif r["place"] == "home":
            item["at_home"] += 1
        if r["score"] >= 3:
            item["flagged"] += 1
        item["score"] += r["score"]
    students = sorted(per_student.values(), key=lambda x: (-x["flagged"], -x["score"]))
    return results, students
