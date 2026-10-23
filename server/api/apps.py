from django.apps import AppConfig, apps
from django.db.models.signals import post_migrate


class ApiConfig(AppConfig):
    name = "api"

    def ready(self):
        from api.query import grant_read_access

        # after every migrate, so tables added since are readable by Claude's SQL (api.query). Sent for apps with
        # models only, so on jobs: the last of those installed, when every table exists.
        post_migrate.connect(grant_read_access, sender=apps.get_app_config("jobs"))
