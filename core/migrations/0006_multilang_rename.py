from django.db import migrations


class Migration(migrations.Migration):
    """Существующие тексты уроков и задач становятся русской версией."""

    dependencies = [("core", "0005_user_language")]

    operations = [
        migrations.RenameField("lesson", "title", "title_ru"),
        migrations.RenameField("lesson", "topic", "topic_ru"),
        migrations.RenameField("task", "title", "title_ru"),
        migrations.RenameField("task", "statement", "statement_ru"),
        migrations.RenameField("task", "input_format", "input_format_ru"),
        migrations.RenameField("task", "output_format", "output_format_ru"),
    ]
