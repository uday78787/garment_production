from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.RoleBasedLoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("password-change/", views.PasswordChangeView.as_view(), name="password_change"),
    path("password-change/done/", views.PasswordChangeDoneView.as_view(), name="password_change_done"),
    path("password-reset/", views.PasswordResetView.as_view(), name="password_reset"),
    path("password-reset/done/", views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("api/password-reset/request/", views.PasswordResetOTPRequestView.as_view(), name="password_reset_otp_request"),
    path("api/password-reset/confirm/", views.PasswordResetOTPConfirmView.as_view(), name="password_reset_otp_confirm"),
    path(
        "reset/<uidb64>/<token>/",
        views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path("reset/done/", views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
]
