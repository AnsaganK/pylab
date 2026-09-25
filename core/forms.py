from django import forms
from django.contrib.auth import get_user_model
from django.forms import inlineformset_factory

from .models import Lesson, StudyGroup, Task, TestCase

User = get_user_model()


class SetupForm(forms.Form):
    full_name = forms.CharField(label="Ваше имя", max_length=150)
    username = forms.CharField(label="Логин", max_length=150)
    password = forms.CharField(label="Пароль", widget=forms.PasswordInput, min_length=6)

    def clean_username(self):
        u = self.cleaned_data["username"].strip()
        if User.objects.filter(username=u).exists():
            raise forms.ValidationError("Такой логин уже есть.")
        return u


class GroupForm(forms.ModelForm):
    class Meta:
        model = StudyGroup
        fields = ["name", "description"]


class StudentForm(forms.ModelForm):
    password = forms.CharField(
        label="Пароль", required=False,
        help_text="Оставьте пустым — сгенерируется автоматически.",
    )

    class Meta:
        model = User
        fields = ["full_name", "username", "group"]
        labels = {"username": "Логин"}
        help_texts = {"username": ""}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["full_name"].required = True
        self.fields["group"].required = True


class BulkStudentsForm(forms.Form):
    group = forms.ModelChoiceField(StudyGroup.objects.all(), label="Группа")
    names = forms.CharField(
        label="Список студентов",
        widget=forms.Textarea(attrs={"rows": 12, "placeholder": "Фамилия Имя Отчество\nФамилия Имя\n…"}),
        help_text="Одна строка — один студент. Логины и пароли создадутся автоматически.",
    )


class LessonForm(forms.ModelForm):
    class Meta:
        model = Lesson
        fields = ["title", "topic", "order", "open_for"]
        widgets = {
            "open_for": forms.CheckboxSelectMultiple,
            "topic": forms.Textarea(attrs={"rows": 3}),
        }


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = [
            "lesson", "title", "statement", "input_format", "output_format", "kind",
            "time_limit", "memory_limit", "starter_code", "order", "is_open",
        ]
        widgets = {
            "statement": forms.Textarea(attrs={"rows": 8}),
            "input_format": forms.Textarea(attrs={"rows": 2}),
            "output_format": forms.Textarea(attrs={"rows": 2}),
            "starter_code": forms.Textarea(attrs={"rows": 4, "class": "mono"}),
        }


TestCaseFormSet = inlineformset_factory(
    Task, TestCase,
    fields=["input_data", "output_data", "is_sample"],
    widgets={
        "input_data": forms.Textarea(attrs={"rows": 4, "class": "mono"}),
        "output_data": forms.Textarea(attrs={"rows": 4, "class": "mono"}),
    },
    extra=0, can_delete=True,
)


class ReviewForm(forms.Form):
    comment = forms.CharField(label="Комментарий", required=False, widget=forms.Textarea(attrs={"rows": 3}))


class ImportTasksForm(forms.Form):
    lesson = forms.ModelChoiceField(Lesson.objects.all(), label="Урок")
    file = forms.FileField(label="Файл с задачами (.json)", required=False)
    text = forms.CharField(
        label="…или вставьте содержимое файла", required=False,
        widget=forms.Textarea(attrs={"rows": 8, "class": "mono"}),
    )
    is_open = forms.BooleanField(label="Сразу открыть задачи", required=False, initial=True)
    check_solutions = forms.BooleanField(
        label="Прогнать эталонные решения на тестах (если есть в файле)", required=False, initial=True,
    )

    def clean(self):
        data = super().clean()
        raw = ""
        if data.get("file"):
            raw = data["file"].read().decode("utf-8-sig", errors="replace")
        elif data.get("text"):
            raw = data["text"]
        if not raw.strip():
            raise forms.ValidationError("Загрузите файл или вставьте его содержимое.")
        data["raw"] = raw
        return data


class RosterForm(forms.Form):
    text = forms.CharField(
        label="Список из расписания",
        widget=forms.Textarea(attrs={"rows": 14, "class": "mono",
                                     "placeholder": "1) Аханбай Данира Нышанбайқызы (МИК241) 49562\n2) …"}),
        help_text="Вставьте список как есть. Строки без ID (например, заголовок дисциплины) пропускаются.",
    )
    group = forms.ModelChoiceField(
        StudyGroup.objects.all(), label="Группа для строк без группы", required=False,
        help_text="Если в строке указана группа (МИК241), она найдётся среди ваших групп (МИК-241) или создастся.",
    )
