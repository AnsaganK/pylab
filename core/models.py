from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models

import constants


class StudyGroup(models.Model):
    name = models.CharField("Название", max_length=50, unique=True)
    description = models.CharField("Описание", max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Группа"
        verbose_name_plural = "Группы"

    def __str__(self):
        return self.name


class ClassSection(models.Model):
    """Занятие (пара) из расписания: «43554) Программирование на Python ЛЗ».
    Студенты разных групп (МИК, ФИК) могут сидеть на одном занятии."""
    code = models.CharField("Код занятия", max_length=20, unique=True)
    title = models.CharField("Название", max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Занятие"
        verbose_name_plural = "Занятия"

    def __str__(self):
        return f"{self.code} {self.title}".strip()


class User(AbstractUser):
    """Преподаватель — is_staff=True. Студент — обычный пользователь с группой."""
    full_name = models.CharField("ФИО", max_length=150, blank=True)
    group = models.ForeignKey(
        StudyGroup, verbose_name="Группа", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="students",
    )
    sections = models.ManyToManyField(
        ClassSection, verbose_name="Занятия", blank=True, related_name="students",
    )
    # Студенты из расписания входят только по своему ID (он же username)
    id_login = models.BooleanField("Вход по ID", default=False)
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


class Lesson(models.Model):
    title = models.CharField("Название урока", max_length=200)
    topic = models.TextField("Тема / описание", blank=True)
    order = models.PositiveIntegerField("Порядок", default=0)
    open_for = models.ManyToManyField(
        StudyGroup, verbose_name="Открыт для групп", blank=True, related_name="open_lessons",
    )
    open_for_sections = models.ManyToManyField(
        ClassSection, verbose_name="Открыт для занятий", blank=True, related_name="open_lessons",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title

    @staticmethod
    def visible_q(user):
        """Условие «урок открыт студенту»: для его группы или для одного из его занятий."""
        q = models.Q(open_for_sections__students=user)
        if user.group_id:
            q |= models.Q(open_for=user.group_id)
        return q


class Task(models.Model):
    KIND_IO = "io"
    KIND_MANUAL = "manual"
    KIND_CHOICES = [
        (KIND_IO, "Автопроверка на тестах"),
        (KIND_MANUAL, "Вручную, преподавателем"),
    ]

    lesson = models.ForeignKey(Lesson, verbose_name="Урок", on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField("Название", max_length=200)
    statement = models.TextField("Условие", blank=True, help_text="Поддерживается Markdown: **жирный**, `код`, списки, блоки кода.")
    input_format = models.TextField("Формат ввода", blank=True)
    output_format = models.TextField("Формат вывода", blank=True)
    kind = models.CharField("Тип проверки", max_length=10, choices=KIND_CHOICES, default=KIND_IO)
    time_limit = models.FloatField("Лимит времени, с", default=constants.DEFAULT_TIME_LIMIT)
    memory_limit = models.PositiveIntegerField("Лимит памяти, МБ", default=constants.DEFAULT_MEMORY_LIMIT)
    starter_code = models.TextField("Начальный код", blank=True)
    order = models.PositiveIntegerField("Порядок", default=0)
    is_open = models.BooleanField("Задача открыта", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]

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
    input_data = models.TextField("Входные данные", blank=True)
    output_data = models.TextField("Ожидаемый вывод", blank=True)
    is_sample = models.BooleanField("Пример", default=False)
    order = models.PositiveIntegerField("Порядок", default=0)

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
        (PENDING, "В очереди"),
        (RUNNING, "Проверяется"),
        (OK, "Верно"),
        (WA, "Неверный ответ"),
        (TLE, "Превышено время"),
        (MLE, "Превышена память"),
        (RE, "Ошибка выполнения"),
        (CE, "Синтаксическая ошибка"),
        (OLE, "Слишком большой вывод"),
        (SE, "Ошибка проверяющей системы"),
        (REVIEW, "Ждёт проверки"),
        (ACCEPTED, "Зачтено"),
        (REJECTED, "Не зачтено"),
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
    teacher_comment = models.TextField("Комментарий преподавателя", blank=True)
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
        (LOGIN, "Вход"),
        (LOGIN_FAIL, "Неудачный вход"),
        (KICKED, "Вытеснен другим входом"),
        (RUN, "Запуск"),
        (SUBMIT, "Отправка"),
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
        "Один вход на аккаунт студента", default=True,
        help_text="При входе под ID на другом компьютере прежний компьютер выходит из аккаунта.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Настройки сайта"

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
