"""
common/models.py

Shared abstract base models and the system-wide AuditLog model.
"""

from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base class adding created/updated timestamps to any model."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditLog(models.Model):
    """
    Generic audit trail for important changes across the application.

    Rather than relying on a third-party package, this simple model is
    written to explicitly by services/views whenever a tracked change
    happens (e.g. DailyProductionEntry edits, LineAssignment status
    changes, user role changes). See common/utils.py for the
    `write_audit_log()` helper used to populate this model consistently.
    """

    class Action(models.TextChoices):
        CREATE = "CREATE", "Create"
        UPDATE = "UPDATE", "Update"
        DELETE = "DELETE", "Delete"
        LOGIN = "LOGIN", "Login"
        LOGOUT = "LOGOUT", "Logout"
        OTHER = "OTHER", "Other"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=10, choices=Action.choices)
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=50, blank=True, null=True)
    old_value = models.JSONField(blank=True, null=True)
    new_value = models.JSONField(blank=True, null=True)
    action_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-action_date"]
        indexes = [
            models.Index(fields=["model_name", "object_id"]),
            models.Index(fields=["action_date"]),
        ]

    def __str__(self):
        return f"{self.action} {self.model_name}#{self.object_id} by {self.user}"
