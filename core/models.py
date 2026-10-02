from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

import constants


class StudyGroup(models.Model):
    name = models.CharField(_("Название"), max_length=50, unique=True)
    description = models.CharField(_("Описание"), max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("Группа")
        verbose_name_plural = _("Группы")

    def __str__(self):
        return self.name


class ClassSection(models.Model):
    """Занятие (пара) из расписания: «43554) Программирование на Python ЛЗ».
    Студенты разных групп (МИК, ФИК) могут сидеть на одном занятии."""
    code = models.CharField(_("Код занятия"), max_length=20, unique=True)
    title = models.CharField(_("Название"), max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]
        verbose_name = _("Занятие")
        verbose_name_plural = _("Занятия")

    def __str__(self):
        return f"{self.code} {self.title}".strip()


class User(AbstractUser):
    """Преподаватель — is_staff=True. Студент — обычный пользователь с группой."""
    full_name = models.CharField(_("ФИО"), max_length=150, blank=True)
    group = models.ForeignKey(
        StudyGroup, verbose_name=_("Группа"), null=True, blank=True,
        on_delete=models.SET_NULL, related_name="students",
    )
    sections = models.ManyToManyField(
        ClassSection, verbose_name=_("Занятия"), blank=True, related_name="students",
    )
    language = models.CharField(_("Язык интерфейса"), max_length=5, blank=True, default="")
    # Студенты из расписания входят только по своему ID (он же username)
    id_login = models.BooleanField(_("Вход по ID"), default=False)
    # ключ сессии последнего входа — для режима «один вход на аккаунт»
    session_key = models.CharField(max_length=40, blank=True, default="")

    class Meta:
        ordering = ["full_name", "username"]

    @property
    def is_teacher(self):
        return self.is_staff

    def display_name(self):
        return self.full_name or self.username

    def __str__(self):
        return self.display_name()


CONTENT_LANGS = ("ru", "kk", "en")


def _current_lang():
    from django.utils.translation import get_language
    return (get_language() or "ru")[:2]


class Multilang:
    """Поля контента на трёх языках: title_ru / title_kk / title_en и т.д.
    Свойство title (и другие) отдаёт текст на языке интерфейса, а если его нет —
    на русском, затем на любом заполненном."""

    ML_FIELDS = ()

    def localized(self, field, lang=None):
        lang = lang or _current_lang()
        order = [lang] + [x for x in CONTENT_LANGS if x != lang]
        for code in order:
            value = getattr(self, f"{field}_{code}", "") or ""
            if value.strip():
                return value, code
        return "", lang

    def filled_langs(self, field="title"):
        return [code for code in CONTENT_LANGS if (getattr(self, f"{field}_{code}", "") or "").strip()]


def _ml_property(field):
    return property(lambda self: self.localized(field)[0])


class Lesson(Multilang, models.Model):
    ML_FIELDS = ("title", "topic")
    title_ru = models.CharField(format_lazy("{} (RU)", _("Название урока")), max_length=200, blank=True)
    title_kk = models.CharField(format_lazy("{} (KK)", _("Название урока")), max_length=200, blank=True)
    title_en = models.CharField(format_lazy("{} (EN)", _("Название урока")), max_length=200, blank=True)
    topic_ru = models.TextField(format_lazy("{} (RU)", _("Тема / описание")), blank=True)
    topic_kk = models.TextField(format_lazy("{} (KK)", _("Тема / описание")), blank=True)
    topic_en = models.TextField(format_lazy("{} (EN)", _("Тема / описание")), blank=True)
    order = models.PositiveIntegerField(_("Порядок"), default=0)
    open_for = models.ManyToManyField(
        StudyGroup, verbose_name=_("Открыт для групп"), blank=True, related_name="open_lessons",
    )
    open_for_sections = models.ManyToManyField(
        ClassSection, verbose_name=_("Открыт для занятий"), blank=True, related_name="open_lessons",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]

    title = _ml_property("title")
    topic = _ml_property("topic")

    def __str__(self):
        return self.title

    @staticmethod
    def visible_q(user):
        """Условие «урок открыт студенту»: для его группы или для одного из его занятий."""
        q = models.Q(open_for_sections__students=user)
        if user.group_id:
            q |= models.Q(open_for=user.group_id)
        return q


class Task(Multilang, models.Model):
    KIND_IO = "io"
    KIND_MANUAL = "manual"
    KIND_CHOICES = [
        (KIND_IO, _("Автопроверка на тестах")),
        (KIND_MANUAL, _("Вручную, преподавателем")),
    ]

    lesson = models.ForeignKey(Lesson, verbose_name=_("Урок"), on_delete=models.CASCADE, related_name="tasks")
    title_ru = models.CharField(format_lazy("{} (RU)", _("Название")), max_length=200, blank=True)
    title_kk = models.CharField(format_lazy("{} (KK)", _("Название")), max_length=200, blank=True)
    title_en = models.CharField(format_lazy("{} (EN)", _("Название")), max_length=200, blank=True)
    statement_ru = models.TextField(format_lazy("{} (RU)", _("Условие")), blank=True, help_text=_("Поддерживается Markdown: **жирный**, `код`, списки, блоки кода."))
    statement_kk = models.TextField(format_lazy("{} (KK)", _("Условие")), blank=True, help_text=_("Поддерживается Markdown: **жирный**, `код`, списки, блоки кода."))
    statement_en = models.TextField(format_lazy("{} (EN)", _("Условие")), blank=True, help_text=_("Поддерживается Markdown: **жирный**, `код`, списки, блоки кода."))
    input_format_ru = models.TextField(format_lazy("{} (RU)", _("Формат ввода")), blank=True)
    input_format_kk = models.TextField(format_lazy("{} (KK)", _("Формат ввода")), blank=True)
    input_format_en = models.TextField(format_lazy("{} (EN)", _("Формат ввода")), blank=True)
    output_format_ru = models.TextField(format_lazy("{} (RU)", _("Формат вывода")), blank=True)
    output_format_kk = models.TextField(format_lazy("{} (KK)", _("Формат вывода")), blank=True)
    output_format_en = models.TextField(format_lazy("{} (EN)", _("Формат вывода")), blank=True)
    kind = models.CharField(_("Тип проверки"), max_length=10, choices=KIND_CHOICES, default=KIND_IO)
    time_limit = models.FloatField(_("Лимит времени, с"), default=constants.DEFAULT_TIME_LIMIT)
    memory_limit = models.PositiveIntegerField(_("Лимит памяти, МБ"), default=constants.DEFAULT_MEMORY_LIMIT)
    starter_code = models.TextField(_("Начальный код"), blank=True)
    order = models.PositiveIntegerField(_("Порядок"), default=0)
    is_open = models.BooleanField(_("Задача открыта"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]

    ML_FIELDS = ("title", "statement", "input_format", "output_format")
    title = _ml_property("title")
    statement = _ml_property("statement")
    input_format = _ml_property("input_format")
    output_format = _ml_property("output_format")

    @property
    def statement_lang(self):
        """Язык, на котором реально показывается условие (для пометки «доступно только на …»)."""
        for field in ("statement", "title"):
            value, code = self.localized(field)
            if value:
                return code
        return _current_lang()

    def content_langs(self):
        """Языки, на которых у задачи заполнены название и условие."""
        return [code for code in CONTENT_LANGS
                if (getattr(self, f"title_{code}") or "").strip()
                and ((getattr(self, f"statement_{code}") or "").strip() or not any(
                    (getattr(self, f"statement_{c}") or "").strip() for c in CONTENT_LANGS))]

    def lang_badges(self):
        filled = set(self.content_langs())
        return [(code, code in filled) for code in CONTENT_LANGS]

    def __str__(self):
        return self.title

    @property
    def is_io(self):
        return self.kind == self.KIND_IO

    def is_visible_to(self, user):
        if user.is_teacher:
            return True
        return self.is_open and Lesson.objects.filter(Lesson.visible_q(user), pk=self.lesson_id).exists()


class TestCase(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="tests")
    input_data = models.TextField(_("Входные данные"), blank=True)
    output_data = models.TextField(_("Ожидаемый вывод"), blank=True)
    is_sample = models.BooleanField(_("Пример"), default=False)
    order = models.PositiveIntegerField(_("Порядок"), default=0)

    class Meta:
        ordering = ["order", "id"]


class Submission(models.Model):
    # Автопроверка
    PENDING = "pending"
    RUNNING = "running"
    OK = "OK"
    WA = "WA"
    TLE = "TLE"
    MLE = "MLE"
    RE = "RE"
    CE = "CE"
    OLE = "OLE"
    SE = "SE"
    # Ручная проверка
    REVIEW = "review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"

    STATUS_CHOICES = [
        (PENDING, _("В очереди")),
        (RUNNING, _("Проверяется")),
        (OK, _("Верно")),
        (WA, _("Неверный ответ")),
        (TLE, _("Превышено время")),
        (MLE, _("Превышена память")),
        (RE, _("Ошибка выполнения")),
        (CE, _("Синтаксическая ошибка")),
        (OLE, _("Слишком большой вывод")),
        (SE, _("Ошибка проверяющей системы")),
        (REVIEW, _("Ждёт проверки")),
        (ACCEPTED, _("Зачтено")),
        (REJECTED, _("Не зачтено")),
    ]
    GOOD = {OK, ACCEPTED}
    IN_PROGRESS = {PENDING, RUNNING}
    NEUTRAL = {PENDING, RUNNING, REVIEW}

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="submissions")
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="submissions")
    code = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    tests_total = models.PositiveIntegerField(default=0)
    tests_passed = models.PositiveIntegerField(default=0)
    failed_test = models.PositiveIntegerField(null=True, blank=True)
    failed_is_sample = models.BooleanField(default=False)
    max_time = models.FloatField(null=True, blank=True)
    details = models.TextField(blank=True)          # stderr / сообщение об ошибке
    failed_input = models.TextField(blank=True)
    failed_expected = models.TextField(blank=True)
    failed_output = models.TextField(blank=True)
    teacher_comment = models.TextField(_("Комментарий преподавателя"), blank=True)
    ip = models.GenericIPAddressField("IP", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_good(self):
        return self.status in self.GOOD

    @property
    def in_progress(self):
        return self.status in self.IN_PROGRESS

    @property
    def tone(self):
        """Цвет плашки статуса в интерфейсе."""
        if self.status in self.GOOD:
            return "good"
        if self.status in self.NEUTRAL:
            return "wait"
        if self.status == self.TLE or self.status == self.MLE or self.status == self.OLE:
            return "limit"
        return "bad"

    def strip_cells(self):
        """Полоска тестов: ok / fail / skip для каждого теста."""
        cells = []
        for i in range(1, self.tests_total + 1):
            if i <= self.tests_passed:
                cells.append("ok")
            elif self.failed_test == i:
                cells.append("fail")
            else:
                cells.append("skip")
        return cells


class AccessLog(models.Model):
    """Журнал действий студентов с IP-адресами."""
    LOGIN = "login"
    LOGIN_FAIL = "login_fail"
    RUN = "run"
    SUBMIT = "submit"
    KICKED = "kicked"
    ACTION_CHOICES = [
        (LOGIN, _("Вход")),
        (LOGIN_FAIL, _("Неудачный вход")),
        (KICKED, _("Вытеснен другим входом")),
        (RUN, _("Запуск")),
        (SUBMIT, _("Отправка")),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.CASCADE, related_name="access_logs")
    ip = models.GenericIPAddressField(null=True, blank=True)
    action = models.CharField(max_length=12, choices=ACTION_CHOICES)
    details = models.CharField(max_length=200, blank=True)
    user_agent = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["ip", "created_at"]), models.Index(fields=["user", "created_at"])]


class SiteSettings(models.Model):
    """Настройки, которые преподаватель меняет прямо на сайте. Всегда одна запись (pk=1)."""
    single_session = models.BooleanField(
        _("Один вход на аккаунт студента"), default=True,
        help_text=_("При входе под ID на другом компьютере прежний компьютер выходит из аккаунта."),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Настройки сайта")

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        _settings_cache.clear()

    @classmethod
    def get(cls):
        import time
        cached = _settings_cache.get("obj")
        if cached and time.monotonic() - cached[1] < 5:
            return cached[0]
        obj, _ = cls.objects.get_or_create(pk=1)
        _settings_cache["obj"] = (obj, time.monotonic())
        return obj


_settings_cache = {}
