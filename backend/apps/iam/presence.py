"""Presence/activity tracking: who's online, in which company, doing what, and for how long.

`heartbeat()` is called by the frontend every ~30s while a tab is open. It updates the live
UserPresence row (overwritten in place) and appends to the permanent PresenceEvent log on any
meaningful transition (login, company/module switch, or a periodic checkpoint) rather than on
every single heartbeat, so the log stays a genuine history instead of 30-second noise -- while
still being append-only and never pruned (see PresenceEvent docstring)."""
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import DailyActivitySummary, PresenceEvent, UserPresence

ACTIVE_GAP_MAX_MINUTES = 5  # a gap bigger than this means the user was away; don't count it as active time
CHECKPOINT_MINUTES = 5      # log a "heartbeat" checkpoint at most this often even with no change


def _bump_daily(user, company, on_date, minutes):
    if not company or minutes <= 0:
        return
    row, _ = DailyActivitySummary.objects.get_or_create(user=user, company=company, date=on_date)
    DailyActivitySummary.objects.filter(pk=row.pk).update(
        active_minutes=F("active_minutes") + Decimal(str(round(minutes, 2))))


def heartbeat(user, company, module_code="", section=""):
    now = timezone.now()
    with transaction.atomic():
        pres = UserPresence.objects.select_for_update().filter(user=user).first()
        created = pres is None
        prev_ended, prev_last = (None, None) if created else (pres.ended_at, pres.last_heartbeat)
        if created:
            pres = UserPresence(user=user, last_heartbeat=now)
        gap = None if created else (now - prev_last).total_seconds() / 60
        reconnecting = created or (prev_ended is not None and prev_ended >= prev_last)
        changed = (created or pres.company_id != (company.pk if company else None) or
                  pres.module_code != module_code or pres.section != section)
        if gap is not None and 0 < gap <= ACTIVE_GAP_MAX_MINUTES and not reconnecting:
            _bump_daily(user, pres.company or company, now.date(), gap)
        due_checkpoint = gap is not None and gap >= CHECKPOINT_MINUTES
        pres.company = company
        pres.module_code = module_code
        pres.section = section
        pres.last_heartbeat = now
        pres.ended_at = None
        pres.save()
    if created or reconnecting:
        event = "login"
    elif changed:
        event = "switch"
    elif due_checkpoint:
        event = "heartbeat"
    else:
        event = None
    if event:
        PresenceEvent.objects.create(user=user, company=company, module_code=module_code, section=section,
                                     event=event, at=now)
    return pres


def end_session(user):
    with transaction.atomic():
        pres = UserPresence.objects.select_for_update().filter(user=user).first()
        if not pres or pres.ended_at:
            return
        now = timezone.now()
        gap = (now - pres.last_heartbeat).total_seconds() / 60
        if pres.company and 0 < gap <= ACTIVE_GAP_MAX_MINUTES:
            _bump_daily(user, pres.company, now.date(), gap)
        pres.ended_at = now
        pres.save(update_fields=["ended_at"])
    PresenceEvent.objects.create(user=user, company=pres.company, module_code=pres.module_code,
                                 section=pres.section, event="logout", at=now)


def team_snapshot():
    """All known presence rows, for the 'who's online right now' endpoint."""
    return UserPresence.objects.select_related("user", "company").order_by("user__username")


def today_minutes(user, company=None) -> Decimal:
    qs = DailyActivitySummary.objects.filter(user=user, date=timezone.localdate())
    if company:
        qs = qs.filter(company=company)
    return sum((row.active_minutes for row in qs), Decimal("0"))
