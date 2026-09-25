from .models import Submission


def best_submissions(user_ids, task_ids):
    """{(user_id, task_id): Submission} — зачтённая, иначе последняя, плюс число попыток."""
    result, attempts = {}, {}
    qs = (Submission.objects
          .filter(user_id__in=user_ids, task_id__in=task_ids)
          .only("id", "user_id", "task_id", "status", "created_at", "tests_passed", "tests_total", "failed_test")
          .order_by("created_at"))
    for s in qs:
        key = (s.user_id, s.task_id)
        attempts[key] = attempts.get(key, 0) + 1
        cur = result.get(key)
        if cur is None or not cur.is_good:
            result[key] = s
    return result, attempts
