from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Lesson, StudyGroup, Submission, Task, TestCase, User


@admin.register(User)
class PyLabUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("PyLab", {"fields": ("full_name", "group")}),)
    list_display = ("username", "full_name", "group", "is_staff")
    list_filter = ("group", "is_staff")


class TestCaseInline(admin.TabularInline):
    model = TestCase
    extra = 0


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "lesson", "kind", "is_open")
    inlines = [TestCaseInline]


admin.site.register(StudyGroup)
admin.site.register(Lesson)


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "task", "status", "created_at")
    list_filter = ("status",)
