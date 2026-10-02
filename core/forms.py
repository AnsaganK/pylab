from django import forms
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model
from django.forms import inlineformset_factory

from .models import ClassSection, Lesson, StudyGroup, Task, TestCase

User = get_user_model()


class SetupForm(forms.Form):
    full_name = forms.CharField(label=_("Ваше имя"), max_length=150)
    username = forms.CharField(label=_("Логин"), max_length=150)
    password = forms.CharField(label=_("Пароль"), widget=forms.PasswordInput, min_length=6)

    def clean_username(self):
        u = self.cleaned_data["username"].strip()
        if User.objects.filter(username=u).exists():
            raise forms.ValidationError(_("Такой логин уже есть."))
        return u


class GroupForm(forms.ModelForm):
    class Meta:
        model = StudyGroup
        fields = ["name", "description"]


class StudentForm(forms.ModelForm):
    password = forms.CharField(
        label=_("Пароль"), required=False,
        help_text=_("Оставьте пустым — сгенерируется автоматически."),
    )

    class Meta:
        model = User
        fields = ["full_name", "username", "group", "sections"]
        labels = {"username": _("Логин / ID")}
        help_texts = {"username": ""}
        widgets = {"sections": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["full_name"].required = True
        self.fields["group"].required = True


class BulkStudentsForm(forms.Form):
    group = forms.ModelChoiceField(StudyGroup.objects.all(), label=_("Группа"))
    names = forms.CharField(
        label=_("Список студентов"),
        widget=forms.Textarea(attrs={"rows": 12, "placeholder": _("Фамилия Имя Отчество\nФамилия Имя\n…")}),
        help_text=_("Одна строка — один студент. Логины и пароли создадутся автоматически."),
    )


class LessonForm(forms.ModelForm):
    class Meta:
        model = Lesson
        fields = ["title_ru", "title_kk", "title_en", "topic_ru", "topic_kk", "topic_en",
                  "order", "open_for_sections", "open_for"]
        widgets = {
            "topic_ru": forms.Textarea(attrs={"rows": 3}),
            "topic_kk": forms.Textarea(attrs={"rows": 3}),
            "topic_en": forms.Textarea(attrs={"rows": 3}),
            "open_for": forms.CheckboxSelectMultiple,
            "open_for_sections": forms.CheckboxSelectMultiple,
        }

    def clean(self):
        data = super().clean()
        if not any((data.get(f"title_{c}") or "").strip() for c in ("ru", "kk", "en")):
            raise forms.ValidationError(_("Заполните название хотя бы на одном языке."))
        return data


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = [
            "lesson",
            "title_ru", "title_kk", "title_en",
            "statement_ru", "statement_kk", "statement_en",
            "input_format_ru", "input_format_kk", "input_format_en",
            "output_format_ru", "output_format_kk", "output_format_en",
            "kind",
            "time_limit", "memory_limit", "starter_code", "order", "is_open",
        ]
        widgets = {
            **{f"statement_{c}": forms.Textarea(attrs={"rows": 8}) for c in ("ru", "kk", "en")},
            **{f"input_format_{c}": forms.Textarea(attrs={"rows": 2}) for c in ("ru", "kk", "en")},
            **{f"output_format_{c}": forms.Textarea(attrs={"rows": 2}) for c in ("ru", "kk", "en")},
            "starter_code": forms.Textarea(attrs={"rows": 4, "class": "mono"}),
        }

    def clean(self):
        data = super().clean()
        if not any((data.get(f"title_{c}") or "").strip() for c in ("ru", "kk", "en")):
            raise forms.ValidationError(_("Заполните название хотя бы на одном языке."))
        return data


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
    comment = forms.CharField(label=_("Комментарий"), required=False, widget=forms.Textarea(attrs={"rows": 3}))


class ImportTasksForm(forms.Form):
    lesson = forms.ModelChoiceField(Lesson.objects.all(), label=_("Урок"))
    file = forms.FileField(label=_("Файл с задачами (.json)"), required=False)
    text = forms.CharField(
        label=_("…или вставьте содержимое файла"), required=False,
        widget=forms.Textarea(attrs={"rows": 8, "class": "mono"}),
    )
    language = forms.ChoiceField(
        label=_("Язык условий"), choices=[("ru", "Русский"), ("kk", "Қазақша"), ("en", "English")], initial="ru",
        help_text=_("Для текстов, заданных строкой. Тексты в виде {\"ru\": …, \"kk\": …, \"en\": …} раскладываются по своим языкам сами."),
    )
    is_open = forms.BooleanField(label=_("Сразу открыть задачи"), required=False, initial=True)
    check_solutions = forms.BooleanField(
        label=_("Прогнать эталонные решения на тестах (если есть в файле)"), required=False, initial=True,
    )

    def clean(self):
        data = super().clean()
        raw = ""
        if data.get("file"):
            raw = data["file"].read().decode("utf-8-sig", errors="replace")
        elif data.get("text"):
            raw = data["text"]
        if not raw.strip():
            raise forms.ValidationError(_("Загрузите файл или вставьте его содержимое."))
        data["raw"] = raw
        return data


class RosterForm(forms.Form):
    text = forms.CharField(
        label=_("Список из расписания"),
        widget=forms.Textarea(attrs={"rows": 14, "class": "mono",
                                     "placeholder": "1) Иванов Иван Иванович (МИК241) 10001\n2) …"}),
        help_text=_("Вставьте список как есть. Строки без ID (например, заголовок дисциплины) пропускаются."),
    )
    group = forms.ModelChoiceField(
        StudyGroup.objects.all(), label=_("Группа для строк без группы"), required=False,
        help_text=_("Если в строке указана группа (МИК241), она найдётся среди ваших групп (МИК-241) или создастся."),
    )


class SectionForm(forms.ModelForm):
    class Meta:
        model = ClassSection
        fields = ["code", "title"]
