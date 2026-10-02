from django.contrib import messages
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils import translation
from django.utils.translation import gettext_lazy

from . import netlog
from .models import AccessLog, SiteSettings

KICK_MESSAGE = gettext_lazy("Под этим ID вошли на другом компьютере, поэтому здесь вы вышли из аккаунта. "
                     "Если это были не вы — сообщите преподавателю.")


class SingleSessionMiddleware:
    """Режим «один вход на аккаунт»: устаревшая сессия студента завершается."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (user is not None and user.is_authenticated and not user.is_staff
                and user.session_key and request.session.session_key
                and request.session.session_key != user.session_key
                and SiteSettings.get().single_session):
            netlog.log(request, AccessLog.KICKED, user=user, details="на этом ПК вышел: вход в другом месте")
            logout(request)
            if request.headers.get("X-Requested-With") == "fetch":
                return JsonResponse({"error": str(KICK_MESSAGE), "kicked": True}, status=401)
            messages.warning(request, str(KICK_MESSAGE))
            return redirect("login")
        return self.get_response(request)


class UserLanguageMiddleware:
    """Язык, выбранный студентом, хранится в его профиле: на общем компьютере
    после входа каждый сразу видит сайт на своём языке."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.language:
            translation.activate(user.language)
            request.LANGUAGE_CODE = user.language
        return self.get_response(request)
