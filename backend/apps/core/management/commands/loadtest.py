"""Volume / scale testing for the ledger engine.

This is throwaway tooling for answering one question: does the system hold up under
hundreds of thousands to millions of posted journal entries? It is NOT meant to be run
against a real environment -- point it at a disposable company/database.

Three phases, each independently skippable:

  --bulk N      Fast-path generation of N posted journal entries, bypassing the Python
                service layer (no Sequence numbering, no audit trail) so we can reach
                realistic data volumes quickly. Still goes through the real tables and the
                real deferred DB trigger that validates every entry balances at COMMIT
                (apps/ledger/migrations/0003_integrity_triggers.py: erp_entry_balanced).
                This measures raw insert throughput the storage engine can sustain.

  --real N      N entries posted through the ACTUAL service layer
                (apps.ledger.services.create_entry / post_entry), i.e. with gapless
                Sequence numbering, the full validation pass, and a hash-chained audit
                log write per entry (serialized via pg_advisory_xact_lock). This measures
                the throughput of the code path real users actually hit.

  --bench       Times the key report/dashboard queries against whatever volume of data
                already exists for the company (trial balance, income statement,
                balance sheet, general ledger, AR aging, journal entry list, dashboard).

Usage (inside a disposable environment):
    python manage.py loadtest --company loadtest --bulk 1000000 --real 2000 --bench
"""
import random
import time
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.core.models import Company
from apps.iam.models import User
from apps.ledger import services as svc
from apps.ledger.models import ACCOUNT_TYPES, Account, AccountClass, EntryState, Journal, JournalEntry, JournalLine

INCOME_TYPES = [t for t, cls in ACCOUNT_TYPES.items() if cls == AccountClass.INCOME]
EXPENSE_TYPES = [t for t, cls in ACCOUNT_TYPES.items() if cls == AccountClass.EXPENSE]

START_DATE = date(2023, 1, 1)
END_DATE = date(2026, 10, 22)
SPAN_DAYS = (END_DATE - START_DATE).days


class Command(BaseCommand):
    help = "Generate large volumes of posted journal entries and benchmark report queries."

    def add_arguments(self, p):
        p.add_argument("--company", default="loadtest")
        p.add_argument("--bulk", type=int, default=0, help="entries to fast-path generate")
        p.add_argument("--real", type=int, default=0, help="entries to post through the real service layer")
        p.add_argument("--batch-size", type=int, default=5000)
        p.add_argument("--bench", action="store_true")

    def handle(self, *a, **o):
        try:
            company = Company.objects.get(code=o["company"])
        except Company.DoesNotExist:
            raise CommandError(f"Run setup_company --code {o['company']} first.")
        self.accounts = list(
            Account.objects.filter(company=company, active=True, is_control=False)
            .exclude(account_type__in=["group", "receivable", "payable"]))
        if len(self.accounts) < 2:
            raise CommandError("Need at least 2 postable leaf accounts; run setup_company first.")
        self.journal = Journal.objects.get(company=company, code="GEN")
        self.user = User.objects.filter(memberships__company=company).first() or User.objects.first()

        if o["bulk"]:
            self.bulk_generate(company, o["bulk"], o["batch_size"])
        if o["real"]:
            self.real_throughput(company, o["real"])
        if o["bench"]:
            self.bench_queries(company)

    # ------------------------------------------------------------------ bulk fast-path

    def bulk_generate(self, company, n, batch_size):
        self.stdout.write(f"Bulk-generating {n:,} posted journal entries (batch size {batch_size:,})...")
        base_number = (JournalEntry.objects.filter(company=company).count()) + 1
        t0 = time.monotonic()
        done = 0
        while done < n:
            this_batch = min(batch_size, n - done)
            self._bulk_batch(company, this_batch, base_number + done)
            done += this_batch
            elapsed = time.monotonic() - t0
            rate = done / elapsed if elapsed else 0
            self.stdout.write(f"  {done:,}/{n:,} entries  ({elapsed:6.1f}s elapsed, {rate:8.0f} entries/s)")
        elapsed = time.monotonic() - t0
        self.stdout.write(self.style.SUCCESS(
            f"Bulk-generated {n:,} entries in {elapsed:.1f}s ({n / elapsed:.0f} entries/s, "
            f"{3 * n / elapsed:.0f} lines/s)"))

    @transaction.atomic
    def _bulk_batch(self, company, count, start_number):
        rng = random.Random()
        currency_id = company.base_currency_id
        today_year = END_DATE.year

        # 1) draft entries, 3 lines each (2 random legs + 1 balancing leg), date spread over SPAN_DAYS.
        entries = []
        for i in range(count):
            d = START_DATE + timedelta(days=rng.randrange(0, SPAN_DAYS + 1))
            entries.append(JournalEntry(
                company=company, journal=self.journal, date=d, state=EntryState.DRAFT,
                currency_id=currency_id, rate=Decimal("1"), reference=f"LOADTEST-{start_number + i}",
                narration="Synthetic load-test entry", total_debit=Decimal("0"), total_credit=Decimal("0"),
            ))
        JournalEntry.objects.bulk_create(entries, batch_size=2000)

        lines = []
        totals = []
        for e in entries:
            a1, a2, a3 = rng.sample(self.accounts, 3)
            amt = Decimal(rng.randrange(1000, 500000)) / 100  # 10.00 .. 5000.00
            # Two debit legs, one credit leg for the full amount -> always balances.
            split = (Decimal(rng.randrange(10, 90)) / 100 * amt).quantize(Decimal("0.01"))
            rest = (amt - split).quantize(Decimal("0.01"))
            lines.append(JournalLine(entry=e, company=company, sequence=1, account=a1, debit=split, credit=0,
                                     currency_id=currency_id, amount_currency=split, tax_base=0, quantity=0,
                                     date=e.date, posted=True, account_type=a1.account_type))
            lines.append(JournalLine(entry=e, company=company, sequence=2, account=a2, debit=rest, credit=0,
                                     currency_id=currency_id, amount_currency=rest, tax_base=0, quantity=0,
                                     date=e.date, posted=True, account_type=a2.account_type))
            lines.append(JournalLine(entry=e, company=company, sequence=3, account=a3, debit=0, credit=amt,
                                     currency_id=currency_id, amount_currency=-amt, tax_base=0, quantity=0,
                                     date=e.date, posted=True, account_type=a3.account_type))
            totals.append(amt)
        JournalLine.objects.bulk_create(lines, batch_size=3000)

        # 2) flip entries to posted with matching totals + a (non-gapless, but unique) number.
        #    Real posting uses Sequence.next_number (gapless, per journal/year); for this synthetic
        #    fast-path we only need uniqueness so the number_unique constraint holds.
        for i, e in enumerate(entries):
            e.state = EntryState.POSTED
            e.total_debit = totals[i]
            e.total_credit = totals[i]
            e.number = f"LT/{today_year}/{start_number + i:08d}"
        JournalEntry.objects.bulk_update(entries, ["state", "total_debit", "total_credit", "number"],
                                         batch_size=2000)
        # Deferred constraint trigger (erp_entry_balanced) fires here, at COMMIT of this atomic block.

    # ------------------------------------------------------------------ real service-layer throughput

    def real_throughput(self, company, n):
        self.stdout.write(f"Posting {n:,} entries through the real service layer "
                          f"(Sequence numbering + hash-chained audit log)...")
        t0 = time.monotonic()
        rng = random.Random()
        for i in range(n):
            a1, a2 = rng.sample(self.accounts, 2)
            amt = Decimal(rng.randrange(1000, 500000)) / 100
            d = START_DATE + timedelta(days=rng.randrange(0, SPAN_DAYS + 1))
            entry = svc.create_entry(company, self.journal, d, [
                svc.LineSpec(account=a1, debit=amt),
                svc.LineSpec(account=a2, credit=amt),
            ], user=self.user, narration="Load-test real-path entry")
            svc.post_entry(entry, self.user)
            if (i + 1) % max(1, n // 10) == 0:
                elapsed = time.monotonic() - t0
                self.stdout.write(f"  {i + 1:,}/{n:,}  ({elapsed:.1f}s, {(i + 1) / elapsed:.1f} entries/s)")
        elapsed = time.monotonic() - t0
        self.stdout.write(self.style.SUCCESS(
            f"Real-path posted {n:,} entries in {elapsed:.1f}s ({n / elapsed:.1f} entries/s, "
            f"{1000 * elapsed / n:.1f} ms/entry)"))

    # ------------------------------------------------------------------ report/dashboard benchmarks

    def bench_queries(self, company):
        from django.db.models import Sum
        from django.db.models.functions import TruncMonth

        from apps.ledger import reports

        self.stdout.write(f"Current volume for {company.name}: "
                          f"{JournalEntry.objects.filter(company=company).count():,} entries, "
                          f"{JournalLine.objects.filter(company=company).count():,} lines.")

        def timed(label, fn):
            t0 = time.monotonic()
            result = fn()
            elapsed = time.monotonic() - t0
            size = ""
            try:
                size = f" ({len(result)} rows)"
            except TypeError:
                pass
            self.stdout.write(f"  {label:<45s} {elapsed * 1000:9.1f} ms{size}")
            return result

        timed("reports.trial_balance() (full year)", lambda: reports.trial_balance(company, START_DATE, END_DATE))
        timed("reports.income_statement() (full span)",
              lambda: reports.income_statement(company, START_DATE, END_DATE))
        timed("reports.balance_sheet() (as of today)", lambda: reports.balance_sheet(company, END_DATE))
        timed("reports.general_ledger() (one account)",
              lambda: reports.general_ledger(company, self.accounts[0], START_DATE, END_DATE))
        timed("journal entry list (latest 50, paginated)", lambda: list(
            JournalEntry.objects.filter(company=company).select_related("journal")
            .order_by("-date", "-id")[:50]))
        timed("journal entry search by number prefix", lambda: list(
            JournalEntry.objects.filter(company=company, number__startswith="LT/2026").order_by("-date")[:50]))
        # These two mirror apps/ledger/dashboard.py's actual queries (denormalized
        # JournalLine.account_type, not a join to ledger_account; .order_by() cleared before
        # the plain aggregate -- see dashboard._bal()) rather than a stale simulation of them,
        # so this harness catches regressions in the real dashboard code, not a stand-in for it.
        timed("dashboard-style 12-month income/expense trend", lambda: list(
            JournalLine.objects.filter(company=company, posted=True, date__gte=date(2025, 11, 1),
                                       account_type__in=INCOME_TYPES + EXPENSE_TYPES)
            .annotate(m=TruncMonth("date")).values("m", "account_type")
            .annotate(d=Sum("debit"), cr=Sum("credit"))))
        timed("dashboard-style cash balance aggregate", lambda: JournalLine.objects.filter(
            company=company, posted=True, account_type__in=["bank", "cash"])
            .order_by().aggregate(d=Sum("debit"), c=Sum("credit")))
        with connection.cursor() as cur:
            cur.execute("SELECT pg_size_pretty(pg_total_relation_size('ledger_journalentry')), "
                       "pg_size_pretty(pg_total_relation_size('ledger_journalline')), "
                       "pg_size_pretty(pg_total_relation_size('core_auditlog'))")
            je_size, jl_size, al_size = cur.fetchone()
            self.stdout.write(f"  table sizes: journalentry={je_size}  journalline={jl_size}  auditlog={al_size}")
