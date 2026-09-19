"""
accounts/models.py

We use Django's built-in User model (settings.AUTH_USER_MODEL) rather than a
custom user model, and attach a UserProfile for the extra fields this
project needs (role + assigned production lines). Roles are additionally
mirrored as Django Groups ("Admin", "Production User") so that
`permission_required` / `Group`-based permission checks work out of the box
alongside our own `role` field, which is used for quick role checks in
templates and views without extra queries.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class UserProfile(models.Model):
    class Role(models.TextChoices):
        SUPERUSER = "SUPERUSER", "Superuser"
        FACTORY_ADMIN = "FACTORY_ADMIN", "Factory Admin"
        DASHBOARD_VIEWER = "DASHBOARD_VIEWER", "Dashboard Viewer"
        SUPERVISOR = "SUPERVISOR", "Supervisor"
        QA_PERMISSION = "QA_PERMISSION", "QA Permission"
        IE_PERMISSION = "IE_PERMISSION", "IE Department"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    factory = models.ForeignKey(
        "master_data.Factory",
        on_delete=models.PROTECT,
        related_name="users",
        null=True,
        blank=True,
        help_text="Factory/location this user belongs to.",
    )
    accessible_factories = models.ManyToManyField(
        "master_data.Factory",
        blank=True,
        related_name="authorized_users",
        help_text="Factories this user is allowed to access. Superusers can access all factories.",
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.SUPERVISOR)
    assigned_lines = models.ManyToManyField(
        "master_data.ProductionLine",
        blank=True,
        related_name="assigned_users",
        help_text="Production lines this user may view/enter data for. "
                  "Ignored for Admin users, who can access all lines.",
    )

    class Meta:
        verbose_name = "User Profile"
        verbose_name_plural = "User Profiles"
        constraints = [
            models.UniqueConstraint(
                fields=("factory",),
                condition=models.Q(role="FACTORY_ADMIN"),
                name="one_factory_admin_per_factory",
            )
        ]

    def __str__(self):
        return f"{self.user.get_username()} ({self.get_role_display()})"

    @property
    def is_admin(self) -> bool:
        return self.role == self.Role.FACTORY_ADMIN

    @property
    def can_manage_styles(self) -> bool:
        return self.role in {
            self.Role.FACTORY_ADMIN,
            self.Role.SUPERVISOR,
        }

    def can_access_line(self, production_line) -> bool:
        """Admins can access every line; Production Users only assigned ones."""
        if self.is_admin:
            return True
        return self.assigned_lines.filter(pk=production_line.pk).exists()

    @property
    def can_manage_manpower(self) -> bool:
        return self.role == self.Role.IE_PERMISSION


class PasswordResetOTP(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    email = models.EmailField(db_index=True)
    otp_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_usable(self):
        return self.used_at is None and not self.is_expired and self.attempts < 5
