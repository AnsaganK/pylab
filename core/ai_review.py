"""Оценка подозрительных решений языковой моделью (Claude или OpenAI).

Настройки — в constants.py:
    AI_PROVIDER = "anthropic"            # или "openai"
    AI_API_KEY = "sk-..."                # ключ API выбранного провайдера
    AI_MODEL = "claude-haiku-5-5"        # для OpenAI — модель из вашего кабинета
    AI_MAX_PER_RUN = 40                  # сколько решений оценивать за одно нажатие
    AI_MIN_SCORE = 2                     # оценивать решения с баллом признаков от …

Модели отправляются: условие задачи, код студента, его прошлые попытки по задаче,
два его других решения (для сравнения стиля), похожий код другого студента и
признаки (вставка, время, уходы со страницы). ФИО, ID и IP не отправляются —
вместо имён «Студент A» / «Студент B».
"""
import hashlib
import json
import re
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections
from django.utils import timezone, translation

import constants

from .models import AIReview, Submission

PROVIDER = getattr(constants, "AI_PROVIDER", "anthropic")
API_KEY = getattr(constants, "AI_API_KEY", "")
MODEL = getattr(constants, "AI_MODEL", "claude-haiku-5-5" if PROVIDER == "anthropic" else "")
MAX_PER_RUN = getattr(constants, "AI_MAX_PER_RUN", 40)
MIN_SCORE = getattr(constants, "AI_MIN_SCORE", 2)
TIMEOUT = getattr(constants, "AI_TIMEOUT", 90)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai-review")
_lock = threading.Lock()

LANG_NAMES = {"ru": "русском", "kk": "казахском", "en": "английском"}


def is_configured():
    return bool(API_KEY and MODEL and PROVIDER in ("anthropic", "openai"))


def code_hash(code):
    return hashlib.sha1(code.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- запрос к модели

SYSTEM_PROMPT = """Ты помогаешь преподавателю вуза проверять, сами ли студенты решили задачи по Python.
Студенты — 3 курс, специальность «Математика и информатика», изучают Python с нуля; темы идут по порядку
(ввод-вывод, условия, циклы, строки, списки, функции…). Задачи простые, в духе олимпиадных: ввод → вывод.

Тебе дают одно верное решение и контекст. Оцени, насколько вероятно, что студент:
- "honest" — решил сам (возможно, с ошибками по пути);
- "ai" — получил код от ИИ (ChatGPT и т.п.);
- "copied" — списал у другого студента;
- "unsure" — данных недостаточно для вывода.

На что опираться:
- Признаки честной работы: неудачные попытки, которые постепенно исправляются; запуски перед отправкой;
  код напечатан, а не вставлен; стиль совпадает с другими решениями этого студента; простые конструкции уровня темы.
- Признаки ИИ: код целиком вставлен и/или студент уходил со страницы перед этим; конструкции сильно выше уровня
  темы и курса (def main(), if __name__ == "__main__", аннотации типов, docstring, try/except, sys.stdin,
  f-строки с форматированием, много аккуратных комментариев на английском); резкий скачок стиля по сравнению
  с его другими решениями; верно с первой попытки за десятки секунд; серия задач подряд за минуты.
- Признаки списывания: почти тот же код, что у другого студента, сданный позже него, особенно с той же
  структурой и теми же необычными приёмами, но с переименованными переменными.
- Отдельный признак сам по себе ничего не доказывает: короткие задачи часто решаются одинаково, а сильный студент
  может решать быстро. Обвинение — серьёзная вещь: при сомнениях выбирай "unsure", а не "ai" или "copied".

Ответь ТОЛЬКО JSON-объектом без пояснений вокруг:
{"verdict": "honest|ai|copied|unsure", "confidence": 0-100, "reason": "2–4 предложения: на чём основан вывод, со ссылкой на конкретные строки или признаки"}"""


def _shorten(code, limit=2500):
    return code if len(code) <= limit else code[:limit] + "\n# … (обрезано)"


def build_prompt(sub, signals, lang):
    task = sub.task
    with translation.override("ru"):
        statement = task.statement or ""
        title = task.title
        lesson = task.lesson.title
        topic = task.lesson.topic or ""
    parts = [f"## Урок\n{lesson}" + (f" — {topic}" if topic else ""),
             f"## Задача «{title}»\n{_shorten(statement, 2000)}"]

    # Прошлые попытки по этой задаче
    earlier = list(Submission.objects.filter(user=sub.user, task=task, created_at__lt=sub.created_at)
                   .order_by("created_at"))
    if earlier:
        shown = earlier[-4:]
        lines = [f"Всего неудачных попыток до этого: {len(earlier)}. Последние:"]
        for e in shown:
            lines.append(f"### {timezone.localtime(e.created_at):%d.%m %H:%M} — {e.get_status_display()}\n```python\n{_shorten(e.code, 1200)}\n```")
        parts.append("## Предыдущие попытки студента по этой задаче\n" + "\n".join(lines))
    else:
        parts.append("## Предыдущие попытки студента по этой задаче\nНет — верно с первой отправки.")

    parts.append(f"## Проверяемое решение ({timezone.localtime(sub.created_at):%d.%m %H:%M})\n```python\n{_shorten(sub.code)}\n```")

    metrics = []
    if sub.typed_chars is not None:
        metrics.append(f"напечатано символов: {sub.typed_chars}, вставлено: {sub.pasted_chars or 0}, длина кода: {len(sub.code)}")
        if sub.away_count is not None:
            metrics.append(f"уходил со страницы: {sub.away_count} раз, всего {sub.away_seconds or 0} с")
    else:
        metrics.append("данных о наборе кода нет (решение старше этой функции)")
    if signals:
        metrics.extend(f"[{kind}] {text}" for kind, text in signals)
    parts.append("## Признаки, собранные платформой\n" + "\n".join(f"- {m}" for m in metrics))

    # Для сравнения стиля — самые ранние другие верные решения того же студента
    # (в начале курса реже списывают, поэтому это лучший образец его собственного стиля)
    own = (Submission.objects.filter(user=sub.user, status__in=Submission.GOOD)
           .exclude(task=task).order_by("created_at")[:6])
    own = [o for o in own if len(o.code) > 40][:2]
    if own:
        parts.append("## Другие решения этого же студента (для сравнения стиля)\n" + "\n".join(
            f"```python\n{_shorten(o.code, 1000)}\n```" for o in own))

    # Похожий код другого студента
    other = getattr(sub, "_similar_other", None)
    if other is not None:
        when = "раньше" if other.created_at < sub.created_at else "позже"
        parts.append(f"## Похожее решение другого студента (Студент B, сдано {when})\n```python\n{_shorten(other.code, 1500)}\n```")

    parts.append(f"Напиши поле reason на {LANG_NAMES.get(lang, 'русском')} языке.")
    return "\n\n".join(parts)


def _post(url, headers, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"content-type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None


def call_llm(system, user):
    if PROVIDER == "openai":
        data = _post("https://api.openai.com/v1/chat/completions",
                     {"authorization": f"Bearer {API_KEY}"},
                     {"model": MODEL, "messages": [{"role": "system", "content": system},
                                                   {"role": "user", "content": user}],
                      "response_format": {"type": "json_object"}})
        return data["choices"][0]["message"]["content"]
    data = _post("https://api.anthropic.com/v1/messages",
                 {"x-api-key": API_KEY, "anthropic-version": "2023-06-01"},
                 {"model": MODEL, "max_tokens": 600, "system": system,
                  "messages": [{"role": "user", "content": user}]})
    return "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")


def parse_answer(text):
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("ответ модели не похож на JSON: " + text[:200])
    data = json.loads(match.group(0))
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in {AIReview.HONEST, AIReview.UNSURE, AIReview.AI, AIReview.COPIED}:
        verdict = AIReview.UNSURE
    try:
        confidence = max(0, min(100, int(data.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = None
    return verdict, confidence, str(data.get("reason", "")).strip()[:2000]


# ---------------------------------------------------------------- очередь

def _review(review_id, similar_other_id=None):
    close_old_connections()
    try:
        review = AIReview.objects.select_related("submission", "submission__task", "submission__task__lesson",
                                                 "submission__user").get(pk=review_id)
        sub = review.submission
        if similar_other_id:
            sub._similar_other = Submission.objects.filter(pk=similar_other_id).first()
        prompt = build_prompt(sub, [tuple(x) for x in review.signals], review.lang)
        verdict, confidence, reason = parse_answer(call_llm(SYSTEM_PROMPT, prompt))
        AIReview.objects.filter(pk=review_id).update(
            status=AIReview.DONE, verdict=verdict, confidence=confidence, reason=reason, error="", model=MODEL)
    except Exception as e:  # ошибка одной оценки не должна останавливать остальные
        AIReview.objects.filter(pk=review_id).update(status=AIReview.ERROR, error=f"{type(e).__name__}: {e}"[:1000])
    finally:
        close_old_connections()


def anonymized_signals(r):
    """Признаки без имён: имя другого студента заменяется на «Студент B»."""
    other = r.get("similar_other")
    signals = []
    for k, t in r["flags"]:
        text = str(t)
        if other is not None:
            text = text.replace(other.user.display_name(), "Студент B")
        signals.append([k, text])
    return signals


def needs_review(r, force=False):
    review = r.get("review")
    if review is None or force:
        return True
    if review.status == AIReview.PENDING:
        return False
    return not (review.status == AIReview.DONE and review.code_hash == code_hash(r["sub"].code))


# ---------------------------------------------------------------- проверка вручную: выгрузка и загрузка

EXPORT_INSTRUCTIONS = (
    "Оцени каждое решение из items по правилам из system_prompt. "
    "Ответь JSON-объектом {\"reviews\": [{\"id\": <id из items>, \"verdict\": \"honest|ai|copied|unsure\", "
    "\"confidence\": 0-100, \"reason\": \"2–4 предложения\"}]} — по одному элементу на каждый id. "
    "Поле student — псевдоним студента в пределах этого файла (одинаковый у всех его решений)."
)


def export_payload(items, lang):
    """Файл для проверки вне сайта: условие, код, история и признаки по каждому решению, без ФИО/ID/IP."""
    pseudo = {}
    out = []
    for r in items:
        sub = r["sub"]
        alias = pseudo.setdefault(sub.user_id, f"S{len(pseudo) + 1}")
        other = r.get("similar_other")
        if other is not None:
            sub._similar_other = other
        out.append({
            "id": sub.pk,
            "student": alias,
            "score": r["score"],
            "prompt": build_prompt(sub, [tuple(x) for x in anonymized_signals(r)], lang),
        })
    return {
        "format": "pylab-ai-review",
        "version": 1,
        "exported_at": timezone.localtime().isoformat(timespec="minutes"),
        "lang": lang,
        "instructions": EXPORT_INSTRUCTIONS,
        "system_prompt": SYSTEM_PROMPT,
        "items": out,
    }


def import_reviews(raw, lang="ru", label="manual"):
    """Загружает ответ {"reviews": [...]} (или просто список). Возвращает (сохранено, пропущено)."""
    match = re.search(r"[\[{].*[\]}]", raw, re.S)
    if not match:
        raise ValueError("не найден JSON")
    data = json.loads(match.group(0))
    rows = data.get("reviews", data.get("items", [])) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("ожидался список reviews")
    saved = skipped = 0
    for row in rows:
        try:
            sub = Submission.objects.get(pk=int(row.get("id")))
        except (Submission.DoesNotExist, TypeError, ValueError, AttributeError):
            skipped += 1
            continue
        verdict, confidence, reason = parse_answer(json.dumps(row, ensure_ascii=False))
        AIReview.objects.update_or_create(submission=sub, defaults={
            "status": AIReview.DONE, "verdict": verdict, "confidence": confidence, "reason": reason,
            "error": "", "model": label, "lang": lang, "code_hash": code_hash(sub.code),
        })
        saved += 1
    return saved, skipped


def enqueue(items, lang, force=False):
    """items — результаты integrity.analyze. Возвращает число поставленных в очередь."""
    queued = 0
    for r in items:
        if queued >= MAX_PER_RUN:
            break
        sub = r["sub"]
        h = code_hash(sub.code)
        review = AIReview.objects.filter(submission=sub).first()
        if review and not force:
            if review.status == AIReview.PENDING:
                continue
            if review.status == AIReview.DONE and review.code_hash == h:
                continue
        other = r.get("similar_other")
        signals = anonymized_signals(r)
        if review is None:
            review = AIReview.objects.create(submission=sub, signals=signals, lang=lang, code_hash=h)
        else:
            AIReview.objects.filter(pk=review.pk).update(status=AIReview.PENDING, signals=signals, lang=lang,
                                                       code_hash=h, error="", verdict="", reason="", confidence=None)
        _executor.submit(_review, review.pk, other.pk if other else None)
        queued += 1
    return queued
