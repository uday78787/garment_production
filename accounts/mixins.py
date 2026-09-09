"""
accounts/mixins.py

Reusable access-control building blocks used across the whole project.

Two layers of protection are used together:
1. Role checks (Admin vs Production User) via AdminRequiredMixin /
   admin_required decorator.
2. Line-level checks (a Production User may only see lines they're
   assigned to) via LineAccessRequiredMixin / user_can_access_line.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404


def _is_admin(user):
    return user.is_authenticated and (
        user.is_superuser or (hasattr(user, "profile") and user.profile.is_admin)
    )


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Class-based-view mixin: only Admin users may access the view."""

    raise_exception = True

    def test_func(self):
        return _is_admin(self.request.user)


class SuperuserRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Class-based-view mixin for platform-level setup screens."""

    raise_exception = True

    def test_func(self):
        return self.request.user.is_superuser


def admin_required(view_func):
    """Function-based-view decorator equivalent of AdminRequiredMixin."""

    @login_required
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not _is_admin(request.user):
            raise PermissionDenied("Admin access required.")
        return view_func(request, *args, **kwargs)

    return _wrapped


class LineAccessRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """
    Class-based-view mixin for views operating on a single ProductionLine,
    identified by a `line_pk` (or `pk`) URL kwarg. Admins pass automatically;
    Production Users must have that line in their assigned_lines.
    """

    raise_exception = True
    line_url_kwarg = "line_pk"

    def get_production_line(self):
        from master_data.models import ProductionLine

        pk = self.kwargs.get(self.line_url_kwarg) or self.kwargs.get("pk")
        return get_object_or_404(ProductionLine, pk=pk)

    def test_func(self):
        user = self.request.user
        if not hasattr(user, "profile"):
            return False
        line = self.get_production_line()
        return user.profile.can_access_line(line)
