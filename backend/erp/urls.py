from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter

from apps.approvals.views import ApprovalRequestViewSet, ApprovalRuleViewSet
from apps.billing.views import CustomerInvoiceViewSet, PaymentViewSet, VendorBillViewSet
from apps.core.views import (AuditLogViewSet, CompanyViewSetCurrent, CostCenterViewSet, CurrencyViewSet,
                             ExchangeRateViewSet)
from apps.iam import auth_views
from apps.iam.views import PermissionCatalogView, RoleViewSet, UserViewSet
from apps.integrations.views import ApiKeyViewSet, WebhookDeliveryViewSet, WebhookViewSet
from apps.inventory.views import (ProductCategoryViewSet, ProductViewSet, StockMoveViewSet, StockQuantViewSet,
                                  WarehouseViewSet)
from apps.ledger.dashboard import DashboardView
from apps.ledger.views import (AccountingSettingsView, AccountViewSet, FiscalPeriodViewSet, FiscalYearViewSet,
                               JournalEntryViewSet, JournalViewSet, ReportView, TaxViewSet)
from apps.partners.views import PartnerViewSet, PaymentTermViewSet
from apps.purchasing.views import PurchaseOrderViewSet
from apps.sales.views import SalesOrderViewSet

r = DefaultRouter()
r.register("currencies", CurrencyViewSet, basename="currency")
r.register("company", CompanyViewSetCurrent, basename="company")
r.register("exchange-rates", ExchangeRateViewSet)
r.register("cost-centers", CostCenterViewSet)
r.register("audit-log", AuditLogViewSet)
r.register("users", UserViewSet, basename="user")
r.register("roles", RoleViewSet)
r.register("accounts", AccountViewSet)
r.register("journals", JournalViewSet)
r.register("taxes", TaxViewSet)
r.register("fiscal-years", FiscalYearViewSet)
r.register("fiscal-periods", FiscalPeriodViewSet)
r.register("journal-entries", JournalEntryViewSet)
r.register("partners", PartnerViewSet)
r.register("payment-terms", PaymentTermViewSet)
r.register("product-categories", ProductCategoryViewSet)
r.register("products", ProductViewSet)
r.register("warehouses", WarehouseViewSet)
r.register("stock-moves", StockMoveViewSet)
r.register("stock-levels", StockQuantViewSet)
r.register("invoices", CustomerInvoiceViewSet, basename="invoice")
r.register("bills", VendorBillViewSet, basename="bill")
r.register("payments", PaymentViewSet)
r.register("sales-orders", SalesOrderViewSet)
r.register("purchase-orders", PurchaseOrderViewSet)
r.register("approval-rules", ApprovalRuleViewSet)
r.register("approval-requests", ApprovalRequestViewSet)
r.register("api-keys", ApiKeyViewSet)
r.register("webhooks", WebhookViewSet)
r.register("webhook-deliveries", WebhookDeliveryViewSet)

auth = [
    path("login/", auth_views.LoginView.as_view()),
    path("mfa/verify/", auth_views.MfaVerifyView.as_view()),
    path("mfa/setup/", auth_views.MfaSetupView.as_view()),
    path("mfa/confirm/", auth_views.MfaConfirmView.as_view()),
    path("refresh/", auth_views.RefreshView.as_view()),
    path("logout/", auth_views.LogoutView.as_view()),
    path("change-password/", auth_views.ChangePasswordView.as_view()),
    path("me/", auth_views.MeView.as_view()),
]

urlpatterns = [
    path("api/auth/", include(auth)),
    path("api/dashboard/", DashboardView.as_view()),
    path("api/accounting-settings/", AccountingSettingsView.as_view()),
    path("api/permissions/", PermissionCatalogView.as_view()),
    path("api/reports/<slug:name>/", ReportView.as_view()),
    path("api/", include(r.urls)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema")),
    path("health/", lambda request: JsonResponse({"status": "ok"})),
    path("django-admin/", admin.site.urls),
]
