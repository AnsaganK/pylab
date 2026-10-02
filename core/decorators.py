from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.utils.translation import gettext as _


def teacher_required(view):
    """Страницы преподавателя. Студента не пугаем ошибкой 403, а отправляем к его задачам."""
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_teacher:
            messages.info(request, _("Эта страница доступна только преподавателю."))
            return redirect("s_lessons")
        return view(request, *args, **kwargs)
    return wrapper
