from datetime import date
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core import context
from apps.core.management.commands.setup_company import ensure_currencies, setup
from apps.core.models import Company
from apps.iam.models import Membership, Role, User
from apps.inventory.models import Product, ProductCategory, Warehouse
from apps.ledger.models import Account, Journal, Tax
from apps.partners.models import Partner

PWD = "Str0ng!Password#1"
YEAR = timezone.localdate().year


def D(x):
    return Decimal(str(x))


class Env:
    pass


def make_user(company, username, role_name):
    u = User.objects.create_user(username=username, email=f"{username}@t.local", password=PWD)
    m = Membership.objects.create(user=u, company=company, is_default=True)
    m.roles.set([Role.objects.get(company=company, name=role_name)])
    return u


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture(autouse=True)
def _clear_cache():
    from django.core.cache import cache
    cache.clear()  # resets throttle counters between tests


@pytest.fixture
def env(db):
    ensure_currencies()
    c = Company.objects.create(name="Test Co", code="test", base_currency_id="USD")
    acc, journals, taxes = setup(c, Decimal("15"), YEAR)
    e = Env()
    e.c = c
    e.A = {a.code: a for a in Account.objects.filter(company=c)}
    e.J = {j.code: j for j in Journal.objects.filter(company=c)}
    e.vat_s, e.vat_p = taxes["S"], taxes["P"]
    e.wh = Warehouse.objects.get(company=c, code="MAIN")
    e.fifo = ProductCategory.objects.get(company=c, cost_method="fifo")
    e.avg = ProductCategory.objects.get(company=c, name__startswith="Merchandise (Average")
    e.clerk = make_user(c, "clerk", "Accounting Clerk")
    e.accountant = make_user(c, "accountant", "Accountant")
    e.senior = make_user(c, "senior", "Senior Accountant")
    e.controller = make_user(c, "controller", "Financial Controller")
    e.cfo = make_user(c, "cfo", "CFO / Finance Director")
    e.admin = make_user(c, "sysadmin", "System Administrator")
    e.customer = Partner.objects.create(company=c, code="C1", name="Customer One", is_customer=True)
    e.vendor = Partner.objects.create(company=c, code="V1", name="Vendor One", is_vendor=True)
    e.fifo_prod = Product.objects.create(company=c, sku="F1", name="Fifo item", category=e.fifo, sale_price=100)
    e.avg_prod = Product.objects.create(company=c, sku="A1", name="Avg item", category=e.avg, sale_price=100)
    e.d = lambda m, d: date(YEAR, m, d)
    token = context.set_context(context.RequestContext(user=e.cfo, company_id=c.pk, actor_label="test"))
    yield e
    context.reset(token)


@pytest.fixture
def api(env):
    def client_for(user):
        cl = APIClient()
        cl.force_authenticate(user)
        cl.credentials(HTTP_X_COMPANY=str(env.c.pk))
        return cl
    return client_for


@pytest.fixture
def eg(db):
    """An Egyptian company (EGP, VAT 14%, withholding codes, ETA configured for pre-production)."""
    from apps.einvoice.models import EtaSettings
    ensure_currencies()
    c = Company.objects.create(name="Nile Trading SAE", name_ar="النيل للتجارة", code="nile", base_currency_id="EGP",
                               country="EG", tax_number="123456789")
    acc, journals, taxes = setup(c, Decimal("14"), YEAR)
    e = Env()
    e.c = c
    e.A = {a.code: a for a in Account.objects.filter(company=c)}
    e.J = {j.code: j for j in Journal.objects.filter(company=c)}
    e.T = {t.code: t for t in Tax.objects.filter(company=c)}
    e.wh = Warehouse.objects.get(company=c, code="MAIN")
    e.fifo = ProductCategory.objects.get(company=c, cost_method="fifo")
    e.avg = ProductCategory.objects.get(company=c, name__startswith="Merchandise (Average")
    e.raw = ProductCategory.objects.get(company=c, name="Raw Materials")
    e.svc_cat = ProductCategory.objects.get(company=c, name="Services")
    e.cfo = make_user(c, "eg_cfo", "CFO / Finance Director")
    e.accountant = make_user(c, "eg_acc", "Accountant")
    e.customer = Partner.objects.create(company=c, code="C1", name="Cairo Retail", is_customer=True,
                                        tax_number="987654321", governate="Cairo", region_city="Nasr City",
                                        street="Abbas El Akkad", building_number="12")
    e.vendor = Partner.objects.create(company=c, code="V1", name="Delta Services", is_vendor=True,
                                      tax_number="555666777", withholding_tax=e.T["WHT3-SRV-P"])
    e.item = Product.objects.create(company=c, sku="P1", name="Widget", category=e.fifo, sale_price=1000,
                                    eta_item_code="EG-123456789-P1", sale_tax=e.T["VAT14-S"])
    e.service = Product.objects.create(company=c, sku="S1", name="Consulting", type="service", category=e.svc_cat,
                                       sale_price=5000, eta_item_code="EG-123456789-S1", sale_tax=e.T["VAT14-S"])
    e.eta = EtaSettings.objects.create(company=c, enabled=False, environment="preprod", client_id="cid",
                                       client_secret="secret", taxpayer_activity_code="4620", governate="Cairo",
                                       region_city="Maadi", street="Road 9", building_number="5",
                                       signer_mode="unsigned_preprod", auto_submit=False)
    e.d = lambda m, d: date(YEAR, m, d)
    token = context.set_context(context.RequestContext(user=e.cfo, company_id=c.pk, actor_label="test"))
    yield e
    context.reset(token)


def tb(env):
    from apps.ledger import reports
    return {r["code"]: D(r["closing"]) for r in reports.trial_balance(env.c, date(YEAR, 1, 1), date(YEAR, 12, 31))["rows"]}
