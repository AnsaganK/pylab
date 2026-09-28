"""IP-адреса запросов и журнал действий студентов."""
import ipaddress
from collections import defaultdict

import constants

from .models import AccessLog

# Необязательные настройки (можно добавить в constants.py):
#   ID_LOGIN_ENABLED = True                 — вход студентов по ID без пароля
#   ID_LOGIN_ALLOWED_NETS = ["192.168.0.0/16"] — с каких сетей разрешён вход по ID ([] — отовсюду)
#   TRUST_X_FORWARDED_FOR = False           — True, если сайт стоит за nginx
ID_LOGIN_ENABLED = getattr(constants, "ID_LOGIN_ENABLED", True)
ID_LOGIN_ALLOWED_NETS = getattr(constants, "ID_LOGIN_ALLOWED_NETS", [])
TRUST_X_FORWARDED_FOR = getattr(constants, "TRUST_X_FORWARDED_FOR", False)


def client_ip(request):
    ip = request.META.get("REMOTE_ADDR", "")
    if TRUST_X_FORWARDED_FOR:
        fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if fwd:
            ip = fwd.split(",")[0].strip()
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return None
    return ip


def id_login_allowed_from(ip):
    if not ID_LOGIN_ALLOWED_NETS:
        return True
    if not ip:
        return False
    addr = ipaddress.ip_address(ip)
    return any(addr in ipaddress.ip_network(n, strict=False) for n in ID_LOGIN_ALLOWED_NETS)


def log(request, action, user=None, details=""):
    user = user if user is not None else (request.user if request.user.is_authenticated else None)
    if user is not None and user.is_staff:
        return  # действия преподавателя не пишем
    AccessLog.objects.create(
        user=user, ip=client_ip(request), action=action, details=details[:200],
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:200],
    )


def suspicious(since):
    """Подозрительные совпадения за период.

    shared_ids — один аккаунт использовался с нескольких адресов;
    shared_ips — с одного адреса работали несколько студентов.
    """
    rows = (AccessLog.objects
            .filter(created_at__gte=since, user__isnull=False, ip__isnull=False)
            .exclude(action=AccessLog.LOGIN_FAIL)
            .select_related("user", "user__group")
            .order_by("created_at"))
    by_user = defaultdict(lambda: defaultdict(list))
    by_ip = defaultdict(lambda: defaultdict(list))
    users = {}
    for r in rows:
        users[r.user_id] = r.user
        by_user[r.user_id][r.ip].append(r.created_at)
        by_ip[r.ip][r.user_id].append(r.created_at)

    def summary(times):
        return {"first": times[0], "last": times[-1], "count": len(times)}

    shared_ids = [
        {"user": users[uid], "ips": sorted(({"ip": ip, **summary(t)} for ip, t in ips.items()),
                                           key=lambda x: x["first"])}
        for uid, ips in by_user.items() if len(ips) > 1
    ]
    shared_ips = [
        {"ip": ip, "users": sorted(({"user": users[uid], **summary(t)} for uid, t in us.items()),
                                   key=lambda x: x["first"])}
        for ip, us in by_ip.items() if len(us) > 1
    ]
    shared_ids.sort(key=lambda x: -len(x["ips"]))
    shared_ips.sort(key=lambda x: -len(x["users"]))
    return shared_ids, shared_ips


def kicked(since):
    """Студенты, которых вытесняли другим входом: [{user, count, last, ips}]."""
    rows = (AccessLog.objects.filter(created_at__gte=since, action=AccessLog.KICKED, user__isnull=False)
            .select_related("user", "user__group").order_by("created_at"))
    by_user = {}
    for r in rows:
        item = by_user.setdefault(r.user_id, {"user": r.user, "count": 0, "last": None, "ips": set()})
        item["count"] += 1
        item["last"] = r.created_at
        if r.ip:
            item["ips"].add(r.ip)
    return sorted(by_user.values(), key=lambda x: -x["count"])
