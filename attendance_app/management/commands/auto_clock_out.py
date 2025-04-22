from django.core.management.base import BaseCommand
from attendance_app.views import auto_clock_out_at_shift_end

class Command(BaseCommand):
    help = 'Automatically clocks out users at 7 AM, ends breaks/lunch, and resets allocations'

    def handle(self, *args, **kwargs):
        self.stdout.write('Running automatic end-of-shift process...')
        result = auto_clock_out_at_shift_end()
        self.stdout.write(self.style.SUCCESS(result))