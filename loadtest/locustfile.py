"""
Нагрузочный тест PyLab: виртуальные студенты входят по ID, запускают и отправляют код.

Запуск с веб-интерфейсом (http://localhost:8089):
    locust -f locustfile.py --host https://ваш-сайт StudentUser

Проверка «конца пары» — все отправляют решение одновременно:
    locust -f locustfile.py --host https://ваш-сайт --headless -u 30 -r 30 -t 2m PeakUser

Запускайте с СВОЕГО компьютера, а не на сервере: иначе тест сам съест процессор сервера.
"""
import itertools
import threading
import time

from locust import HttpUser, between, constant, events, task

# --- Настройки теста -------------------------------------------------------------
TASK_ID = 1                    # задача, открытая для тестовой группы (номер из адреса /task/N/)
ID_FROM, ID_TO = 90001, 90030  # ID тестовых студентов (см. test_students.txt)
STDIN = "4\n"

GOOD_CODE = """n = int(input())
if n % 2 == 0:
    print('EVEN')
else:
    print('ODD')
"""
WRONG_CODE = "n = int(input())\nprint('EVEN')\n"
INFINITE_CODE = "while True:\n    pass\n"   # студент с бесконечным циклом
VERDICT_TIMEOUT = 180                        # сколько ждать вердикта, с
# ---------------------------------------------------------------------------------

_ids = itertools.cycle(range(ID_FROM, ID_TO + 1))
_ids_lock = threading.Lock()


def next_id():
    with _ids_lock:
        return str(next(_ids))


class _Student(HttpUser):
    abstract = True

    def on_start(self):
        self.sid = next_id()
        self.client.get("/login/?mode=id", name="вход: страница")
        with self.client.post("/login/", data={"mode": "id", "sid": self.sid, "csrfmiddlewaretoken": self._csrf()},
                              headers=self._headers("/login/"), allow_redirects=False,
                              name="вход: по ID", catch_response=True) as r:
            if r.status_code != 302 or "/login" in r.headers.get("Location", ""):
                r.failure(f"ID {self.sid} не вошёл — загружен ли список test_students.txt?")
        self.open_task()

    def _csrf(self):
        return self.client.cookies.get("csrftoken", "")

    def _headers(self, path):
        # Для HTTPS Django сверяет Referer с адресом сайта
        return {"X-CSRFToken": self._csrf(), "Referer": self.host.rstrip("/") + path}

    def open_task(self):
        self.client.get(f"/task/{TASK_ID}/", name="страница задачи")

    def run_code(self, code, label):
        with self.client.post(f"/task/{TASK_ID}/run/", data={"code": code, "stdin": STDIN},
                              headers=self._headers(f"/task/{TASK_ID}/"), name=label, catch_response=True) as r:
            if r.status_code == 503:
                r.failure("сервер занят (очередь запусков переполнена)")
            elif r.status_code != 200:
                r.failure(f"HTTP {r.status_code}")

    def submit_and_wait(self, code, label):
        with self.client.post(f"/task/{TASK_ID}/submit/", data={"code": code},
                              headers=self._headers(f"/task/{TASK_ID}/"), name=label, catch_response=True) as r:
            if r.status_code != 200:
                r.failure(f"HTTP {r.status_code}: {r.text[:100]}")
                return
        # Ждём вердикт так же, как страница: опрашиваем список отправок
        started = time.monotonic()
        while time.monotonic() - started < VERDICT_TIMEOUT:
            time.sleep(1.2)
            frag = self.client.get(f"/task/{TASK_ID}/subs/", name="опрос статуса")
            if "data-busy=\"1\"" not in frag.text:
                events.request.fire(request_type="ПРОВЕРКА", name="ожидание вердикта",
                                    response_time=(time.monotonic() - started) * 1000,
                                    response_length=0, exception=None, context={})
                return
        events.request.fire(request_type="ПРОВЕРКА", name="ожидание вердикта",
                            response_time=VERDICT_TIMEOUT * 1000, response_length=0,
                            exception=Exception("вердикт не пришёл"), context={})


class StudentUser(_Student):
    """Обычная пара: студент думает, запускает, иногда отправляет."""
    wait_time = between(5, 20)

    @task(6)
    def run_good(self):
        self.run_code(GOOD_CODE, "запуск")

    @task(3)
    def reopen(self):
        self.open_task()

    @task(2)
    def submit_good(self):
        self.submit_and_wait(GOOD_CODE, "отправка (верно)")

    @task(1)
    def submit_wrong(self):
        self.submit_and_wait(WRONG_CODE, "отправка (WA)")

    @task(1)
    def run_infinite(self):
        self.run_code(INFINITE_CODE, "запуск (бесконечный цикл)")


class PeakUser(_Student):
    """Конец пары: все отправляют решение почти одновременно, потом ждут вердикт."""
    wait_time = constant(60)

    @task
    def submit(self):
        self.submit_and_wait(GOOD_CODE, "отправка (все сразу)")
