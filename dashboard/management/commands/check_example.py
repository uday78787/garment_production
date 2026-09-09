from django.core.management.base import BaseCommand
from planning.services import calculations as c
from decimal import Decimal

class Command(BaseCommand):
    help = 'Run production example checks'

    def handle(self, *args, **options):
        sam=5.0
        manpower=25
        working_minutes=480
        start_eff=0.60
        daily_growth=0.05
        max_eff=0.80
        actual_output_day1=1200
        order_qty=50000

        avail = c.available_minutes(manpower, working_minutes)
        planned_eff_day1 = c.planned_efficiency(start_eff, daily_growth, 1, max_eff)
        planned_prod_min = c.planned_productive_minutes(avail, planned_eff_day1)
        target_output = c.target_output(planned_prod_min, sam)
        produced_min = c.produced_minutes(actual_output_day1, sam)
        actual_eff = c.actual_efficiency(produced_min, avail)
        gain_loss = c.gain_loss_minutes(produced_min, planned_prod_min)
        forecast_day1 = c.forecast_daily_output(manpower, working_minutes, sam, planned_eff_day1)

        self.stdout.write(f'Available minutes: {avail}')
        self.stdout.write(f'Planned efficiency (day1): {planned_eff_day1}')
        self.stdout.write(f'Planned productive minutes: {planned_prod_min}')
        self.stdout.write(f'Target output (day1): {target_output}')
        self.stdout.write(f'Produced minutes (actual 1200): {produced_min}')
        self.stdout.write(f'Actual efficiency: {actual_eff}')
        self.stdout.write(f'Gain/loss minutes: {gain_loss}')
        self.stdout.write(f'Forecast daily output: {forecast_day1}')

        remaining = Decimal(order_qty)
        day=1
        cumulative=Decimal(0)
        self.stdout.write('\nSimulating day-by-day forecast until order complete:')
        while remaining>0 and day<1000:
            eff = c.planned_efficiency(start_eff, daily_growth, day, max_eff)
            daily_forecast = c.forecast_daily_output(manpower, working_minutes, sam, eff)
            cumulative += daily_forecast
            remaining = Decimal(order_qty) - cumulative
            self.stdout.write(f'Day {day}: eff={eff:.2f}, forecast={daily_forecast:.0f}, cumulative={cumulative:.0f}, remaining={max(remaining,Decimal(0)):.0f}')
            day+=1

        self.stdout.write(f'\nDays simulated: {day-1}')
