from django.db import migrations, models

# Расписание, которое назвал преподаватель; заполняется, только если поле пустое
KNOWN = {"74469": "Вт 15:40-17:40", "43554": "Пт 14:40-16:40"}


def fill(apps, schema_editor):
    ClassSection = apps.get_model("core", "ClassSection")
    for code, value in KNOWN.items():
        ClassSection.objects.filter(code=code, schedule="").update(schedule=value)


class Migration(migrations.Migration):

    dependencies = [("core", "0009_ai_review")]

    operations = [
        migrations.AddField(
            model_name="classsection",
            name="schedule",
            field=models.CharField(blank=True, help_text="Например: Вт 15:40-17:40. Несколько пар — через «;». Решения в это время считаются сделанными на паре, в остальное — дома.", max_length=200, verbose_name="Расписание пар"),
        ),
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
