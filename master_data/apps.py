from django.apps import AppConfig


class MasterDataConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "master_data"
    verbose_name = "Master Data"

    def ready(self):
        import master_data.signals  # noqa: F401
