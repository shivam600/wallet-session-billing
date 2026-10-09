from django.urls import path

from .views import RechargeCallbackView, RechargeInitiateView, WalletEntryListView, WalletView

urlpatterns = [
    path("", WalletView.as_view()),
    path("entries/", WalletEntryListView.as_view()),
    path("recharge/", RechargeInitiateView.as_view()),
    path("recharge/callback/", RechargeCallbackView.as_view()),
]
