from django.urls import path

from .views import RechargeCallbackView, RechargeInitiateView, WalletView

urlpatterns = [
    path("", WalletView.as_view()),
    path("recharge/", RechargeInitiateView.as_view()),
    path("recharge/callback/", RechargeCallbackView.as_view()),
]
