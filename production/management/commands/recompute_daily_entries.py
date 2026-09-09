from django.core.management.base import BaseCommand
from production.models import DailyProductionEntry

class Command(BaseCommand):
    help = 'Recompute derived fields for all DailyProductionEntry rows and save.'

    def handle(self, *args, **options):
        qs = DailyProductionEntry.objects.all()
        total = qs.count()
        self.stdout.write(f'Recomputing {total} daily entries...')
        for i, entry in enumerate(qs, 1):
            entry.recompute_derived_fields()
            entry.save()
            if i % 100 == 0:
                self.stdout.write(f'  processed {i}/{total}')
        self.stdout.write('Done.')
