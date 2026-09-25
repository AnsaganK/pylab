from django.contrib.auth import authenticate, get_user_model, login
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from . import netlog
from .forms import SetupForm
from .models import AccessLog

User = get_user_model()


def _teacher_exists():
    return User.objects.filter(is_staff=True).exists()


def _next_url(request):
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return "home"


def login_view(request):
    """Студенты входят по ID, преподаватель (и студенты с паролем) — по логину и паролю."""
    if not _teacher_exists():
        return redirect("setup")
    if request.user.is_authenticated:
        return redirect("home")

    mode = request.POST.get("mode") or request.GET.get("mode") or ("id" if netlog.ID_LOGIN_ENABLED else "password")
    if not netlog.ID_LOGIN_ENABLED:
        mode = "password"
    error = ""
    value = ""

    if request.method == "POST":
        ip = netlog.client_ip(request)
        if mode == "id":
            value = "".join(request.POST.get("sid", "").split())
            user = User.objects.filter(username=value, id_login=True, is_staff=False, is_active=True).first() if value else None
            if not netlog.id_login_allowed_from(ip):
                error = "Вход по ID разрешён только из учебной аудитории."
                netlog.log(request, AccessLog.LOGIN_FAIL, user=user, details=f"ID {value}: не та сеть")
            elif user is None:
                error = "Студент с таким ID не найден. Проверьте номер или обратитесь к преподавателю."
                netlog.log(request, AccessLog.LOGIN_FAIL, details=f"ID {value}"[:200])
            else:
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                netlog.log(request, AccessLog.LOGIN, user=user, details="по ID")
                return redirect(_next_url(request))
        else:
            value = request.POST.get("username", "").strip()
            user = authenticate(request, username=value, password=request.POST.get("password", ""))
            if user is None:
                error = "Неверный логин или пароль."
                netlog.log(request, AccessLog.LOGIN_FAIL, details=f"логин {value}"[:200])
            else:
                login(request, user)
                netlog.log(request, AccessLog.LOGIN, user=user, details="по паролю")
                return redirect(_next_url(request))

    return render(request, "core/login.html", {
        "mode": mode, "error": error, "value": value,
        "next": request.GET.get("next", request.POST.get("next", "")),
        "id_enabled": netlog.ID_LOGIN_ENABLED,
    })


def setup(request):
    """Первый запуск: создание аккаунта преподавателя."""
    if _teacher_exists():
        return redirect("login")
    form = SetupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = User.objects.create_user(
            username=form.cleaned_data["username"],
            password=form.cleaned_data["password"],
            full_name=form.cleaned_data["full_name"],
            is_staff=True, is_superuser=True,
        )
        login(request, user)
        return redirect("home")
    return render(request, "core/setup.html", {"form": form})


def home(request):
    if not request.user.is_authenticated:
        return redirect("login")
    if request.user.is_teacher:
        return redirect("t_dashboard")
    return redirect("s_lessons")
