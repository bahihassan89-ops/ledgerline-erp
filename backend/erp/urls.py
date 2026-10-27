from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter

from apps.approvals.views import ApprovalRequestViewSet, ApprovalRuleViewSet
from apps.billing.views import CustomerInvoiceViewSet, PaymentViewSet, VendorBillViewSet
from apps.core.views import (AuditLogViewSet, BranchViewSet, CompanyApprovalViewSet, CompanyProvisioningViewSet,
                             CompanyViewSetCurrent, CostCenterViewSet, CurrencyViewSet, ExchangeRateViewSet,
                             NotificationViewSet, PushSubscriptionViewSet)
from apps.iam import auth_views
from apps.iam.views import (GlobalPermissionCatalogView, GlobalRoleViewSet, HeartbeatView, PermissionCatalogView,
                            RoleViewSet, TeamActivityViewSet, UserViewSet)
from apps.integrations.views import ApiKeyViewSet, WebhookDeliveryViewSet, WebhookViewSet
from apps.inventory.views import (LotViewSet, ProductCategoryViewSet, ProductViewSet, SerialNumberViewSet,
                                  StockMoveViewSet, StockQuantViewSet, WarehouseViewSet)
from apps.ledger.dashboard import DashboardView
from apps.ledger.views import (AccountingSettingsView, AccountViewSet, FiscalPeriodViewSet, FiscalYearViewSet,
                               JournalEntryViewSet, JournalViewSet, ReportView, TaxViewSet)
from apps.partners.views import PartnerViewSet, PaymentTermViewSet
from apps.purchasing.views import PurchaseOrderViewSet
from apps.sales.views import SalesOrderViewSet
from apps.assets.api import AssetCategoryViewSet, AssetViewSet
from apps.banking.api import BankStatementViewSet
from apps.core.attachments import AttachmentViewSet
from apps.einvoice.api import EtaDocumentViewSet, EtaSettingsView
from apps.inventory.views import LandedCostViewSet
from apps.ledger.period_views import ChecklistView, ImportView, RevaluationView
from apps.manufacturing.api import (BomViewSet, ManufacturingOrderViewSet, WorkCenterCalendarExceptionViewSet,
                                    WorkCenterViewSet)
from apps.planning.api import BudgetViewSet, RecurringViewSet

r = DefaultRouter()
r.register("currencies", CurrencyViewSet, basename="currency")
r.register("company", CompanyViewSetCurrent, basename="company")
r.register("exchange-rates", ExchangeRateViewSet)
r.register("cost-centers", CostCenterViewSet)
r.register("branches", BranchViewSet)
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
r.register("lots", LotViewSet)
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
r.register("asset-categories", AssetCategoryViewSet)
r.register("assets", AssetViewSet)
r.register("bank-statements", BankStatementViewSet)
r.register("attachments", AttachmentViewSet, basename="attachment")
r.register("eta-documents", EtaDocumentViewSet)
r.register("landed-costs", LandedCostViewSet)
r.register("work-centers", WorkCenterViewSet)
r.register("work-center-calendar-exceptions", WorkCenterCalendarExceptionViewSet)
r.register("boms", BomViewSet)
r.register("manufacturing-orders", ManufacturingOrderViewSet)
r.register("serial-numbers", SerialNumberViewSet)
r.register("budgets", BudgetViewSet)
r.register("recurring-entries", RecurringViewSet)
r.register("global-roles", GlobalRoleViewSet)
r.register("company-provisioning", CompanyProvisioningViewSet, basename="company-provisioning")
r.register("company-approvals", CompanyApprovalViewSet, basename="company-approval")
r.register("team-activity", TeamActivityViewSet, basename="team-activity")
r.register("notifications", NotificationViewSet, basename="notification")
r.register("push-subscriptions", PushSubscriptionViewSet, basename="push-subscription")

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
    path("api/global-permissions/", GlobalPermissionCatalogView.as_view()),
    path("api/presence/heartbeat/", HeartbeatView.as_view()),
    path("api/push/public-key/", lambda request: JsonResponse({"key": settings.WEBPUSH_PUBLIC_KEY})),
    path("api/reports/<slug:name>/", ReportView.as_view()),
    path("api/eta-settings/", EtaSettingsView.as_view()),
    path("api/close-checklist/", ChecklistView.as_view()),
    path("api/fx-revaluation/", RevaluationView.as_view()),
    path("api/imports/<slug:kind>/", ImportView.as_view()),
    path("api/", include(r.urls)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema")),
    path("health/", lambda request: JsonResponse({"status": "ok"})),
    path("django-admin/", admin.site.urls),
]
