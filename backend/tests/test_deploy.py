from io import StringIO

import pytest
from django.core.management import call_command
from django.test import RequestFactory

from apps.core.middleware import client_ip
from apps.iam.models import User


def _req(xff=None):
    meta = {"REMOTE_ADDR": "172.18.0.8"}
    if xff:
        meta["HTTP_X_FORWARDED_FOR"] = xff
    return RequestFactory().get("/", **meta)


def test_client_ip_ignores_forwarded_header_without_trusted_proxies(settings):
    settings.ERP_TRUSTED_PROXIES = 0
    assert client_ip(_req("6.6.6.6")) == "172.18.0.8"


def test_client_ip_takes_the_entry_added_by_the_outermost_proxy(settings):
    settings.ERP_TRUSTED_PROXIES = 2
    # client forged "6.6.6.6"; Caddy appended the real client, nginx appended Caddy
    assert client_ip(_req("6.6.6.6, 41.33.10.5, 172.18.0.9")) == "41.33.10.5"
    assert client_ip(_req("41.33.10.5")) == "172.18.0.8"  # fewer hops than proxies: not trusted


@pytest.mark.django_db
def test_bootstrap_creates_company_and_admin_once():
    out = StringIO()
    args = dict(company_name="Delta Foods", company_code="DELTA", admin_email="cfo@delta.example", stdout=out)
    call_command("bootstrap", **args)
    user = User.objects.get(username="admin")
    assert user.must_change_password and not user.is_superuser
    m = user.membership(user.memberships.get().company_id)
    assert {r.name for r in m.roles.all()} == {"CFO / Finance Director", "System Administrator"}
    assert m.company.base_currency_id == "EGP"
    assert "One-time password" in out.getvalue()

    out = StringIO()
    call_command("bootstrap", **{**args, "stdout": out})  # re-run keeps everything and prints no password
    assert "One-time password" not in out.getvalue()
    assert User.objects.count() == 1
