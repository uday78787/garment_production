"""
accounts/views.py

Login/logout/password reset views. We reuse Django's built-in
auth views wherever possible (they already implement secure password
hashing, CSRF protection, and password-reset token flows) and only
customise the LoginView to redirect based on role.
"""

import json
import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.http import JsonResponse
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .models import PasswordResetOTP


class RoleBasedLoginView(auth_views.LoginView):
    """Standard Django login, but redirects by role after success."""

    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        user = self.request.user
        messages.success(self.request, f"Welcome back, {user.get_username()}!")
        if hasattr(user, "profile"):
            if user.profile.is_admin:
                return reverse_lazy("dashboard:admin_dashboard")
            if user.profile.role == "QA_PERMISSION":
                return reverse_lazy("dashboard:qa_dashboard")
        return reverse_lazy("dashboard:user_dashboard")


class LogoutView(auth_views.LogoutView):
    next_page = reverse_lazy("accounts:login")


class PasswordChangeView(LoginRequiredMixin, auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:password_change_done")


class PasswordChangeDoneView(LoginRequiredMixin, auth_views.PasswordChangeDoneView):
    template_name = "accounts/password_change_done.html"


class PasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "accounts/password_reset_email.html"
    success_url = reverse_lazy("accounts:password_reset_done")


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


def _json_body(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return None


@method_decorator(csrf_exempt, name="dispatch")
class PasswordResetOTPRequestView(View):
    def post(self, request):
        data = _json_body(request)
        email = str(data.get("email", "")).strip().lower() if isinstance(data, dict) else ""
        if not email:
            return JsonResponse({"detail": "A valid email is required."}, status=400)
        try:
            validate_email(email)
        except Exception:
            return JsonResponse({"detail": "Enter a valid email address."}, status=400)

        User = get_user_model()
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user is None:
            return JsonResponse(
                {"detail": "If an active account exists, an OTP will be sent shortly."},
                status=200,
            )

        recent = PasswordResetOTP.objects.filter(
            user=user,
            created_at__gte=timezone.now() - timedelta(minutes=1),
            used_at__isnull=True,
        ).exists()
        if recent:
            return JsonResponse(
                {"detail": "An OTP was already sent recently. Please wait before requesting another."},
                status=429,
            )

        otp = f"{secrets.randbelow(1_000_000):06d}"
        PasswordResetOTP.objects.filter(user=user, used_at__isnull=True).update(
            used_at=timezone.now()
        )
        reset = PasswordResetOTP.objects.create(
            user=user,
            email=email,
            otp_hash=make_password(otp),
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        try:
            send_mail(
                subject="Your Garment Production password reset OTP",
                message=(
                    f"Your password reset OTP is {otp}. It expires in 10 minutes. "
                    "If you did not request this, ignore this email."
                ),
                from_email=None,
                recipient_list=[email],
                fail_silently=False,
            )
        except Exception:
            reset.used_at = timezone.now()
            reset.save(update_fields=["used_at"])
            return JsonResponse(
                {"detail": "OTP could not be sent. Check the email server configuration and try again."},
                status=503,
            )
        return JsonResponse({"detail": "OTP sent successfully. It expires in 10 minutes."})


@method_decorator(csrf_exempt, name="dispatch")
class PasswordResetOTPConfirmView(View):
    def post(self, request):
        data = _json_body(request)
        if not isinstance(data, dict):
            return JsonResponse({"detail": "Invalid JSON body."}, status=400)

        email = str(data.get("email", "")).strip().lower()
        otp = str(data.get("otp", "")).strip()
        new_password = str(data.get("new_password", ""))
        if not email or len(otp) != 6 or not otp.isdigit() or not new_password:
            return JsonResponse(
                {"detail": "email, 6-digit otp, and new_password are required."},
                status=400,
            )

        reset = PasswordResetOTP.objects.filter(
            email__iexact=email, used_at__isnull=True
        ).select_related("user").first()
        if reset is None or not reset.is_usable or not check_password(otp, reset.otp_hash):
            if reset is not None and reset.is_usable:
                reset.attempts += 1
                reset.save(update_fields=["attempts"])
            return JsonResponse({"detail": "Invalid or expired OTP."}, status=400)

        try:
            validate_password(new_password, reset.user)
        except Exception as error:
            return JsonResponse({"detail": error.messages}, status=400)

        reset.user.set_password(new_password)
        reset.user.save(update_fields=["password"])
        reset.used_at = timezone.now()
        reset.save(update_fields=["used_at"])
        return JsonResponse({"detail": "Password reset successfully."})
