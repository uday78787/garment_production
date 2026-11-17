from datetime import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from master_data.models import Factory, FactoryShift


class ShiftManagementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="shift-admin",
            email="shift-admin@example.com",
            password="secure-test-password",
        )
        self.factory = Factory.objects.create(name="North Factory", code="NORTH")
        self.other_factory = Factory.objects.create(name="South Factory", code="SOUTH")
        self.url = reverse("dashboard:shift_management")
        self.client.force_login(self.user)

    @staticmethod
    def shift_payload(factory, **overrides):
        payload = {
            "factory": factory.pk,
            "shift_name": "Day Shift",
            "start_time": "09:00",
            "end_time": "19:00",
            "schedule_editor": "1",
            "hour_count": "10",
            "active": "on",
            "description": "Factory sewing shift",
        }
        for hour in range(1, 11):
            payload[f"hour_{hour}_start"] = f"{9 + (hour - 1):02d}:00"
            payload[f"hour_{hour}_end"] = f"{9 + hour:02d}:00"
            payload[f"hour_{hour}_name"] = f"Hour {hour}"
            payload[f"hour_{hour}_break_start"] = ""
            payload[f"hour_{hour}_break_end"] = ""
        payload["hour_4_break_start"] = "12:00"
        payload["hour_4_break_end"] = "12:30"
        payload.update(overrides)
        return payload

    def test_page_lists_factory_shifts_with_editable_hour_rows(self):
        shift = FactoryShift.objects.create(
            factory=self.factory,
            shift_name="Existing Shift",
            start_time="09:00",
            end_time="17:00",
            standard_hours=8,
            hourly_schedule=[
                {"hour": 1, "start": "09:00", "end": "10:00", "break_start": "", "break_end": ""},
            ],
        )

        response = self.client.get(self.url, {"factory": self.factory.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Existing Shift")
        self.assertContains(response, f'name="shift_id" value="{shift.pk}"')
        self.assertContains(response, "ADD SHIFT HOUR")
        self.assertContains(response, "Break Timing")
        self.assertContains(response, 'id="show-create-shift"')
        self.assertContains(response, 'id="create-shift-form"')
        self.assertContains(response, "South Factory</option>")
        self.assertNotContains(response, "South Shift")

    def test_create_form_starts_with_only_name_and_shift_times(self):
        response = self.client.get(self.url, {"factory": self.factory.pk})
        content = response.content.decode()
        create_form = content.split('<form method="post" id="create-shift-form"', 1)[1].split("</form>", 1)[0]

        self.assertIn('name="shift_name"', create_form)
        self.assertIn('name="start_time"', create_form)
        self.assertIn('name="end_time"', create_form)
        self.assertNotIn("standard_hours", create_form)
        self.assertNotIn("hour_1_start", create_form)
        self.assertIn("hidden", create_form.split(">", 1)[0])

    def test_simple_create_generates_hour_schedule_from_start_and_end(self):
        response = self.client.post(
            self.url,
            {
                "factory": self.factory.pk,
                "simple_create": "1",
                "shift_name": "Morning Shift",
                "start_time": "08:00",
                "end_time": "16:00",
            },
        )

        self.assertRedirects(response, f"{self.url}?factory={self.factory.pk}")
        shift = FactoryShift.objects.get(factory=self.factory, shift_name="Morning Shift")
        self.assertEqual(shift.shift_minutes, 480)
        self.assertEqual(shift.standard_hours, 8)
        self.assertEqual(shift.overtime_hours, 0)
        self.assertEqual(len(shift.hourly_schedule), 8)
        self.assertEqual(shift.hourly_schedule[0]["name"], "Hour 1")
        self.assertEqual(shift.hourly_schedule[0]["start"], "08:00")
        self.assertEqual(shift.hourly_schedule[-1]["end"], "16:00")

    def test_simple_create_supports_overnight_shift_hours(self):
        response = self.client.post(
            self.url,
            {
                "factory": self.factory.pk,
                "simple_create": "1",
                "shift_name": "Night Shift",
                "start_time": "22:00",
                "end_time": "06:00",
            },
        )

        self.assertRedirects(response, f"{self.url}?factory={self.factory.pk}")
        shift = FactoryShift.objects.get(factory=self.factory, shift_name="Night Shift")
        self.assertEqual(shift.shift_minutes, 480)
        self.assertEqual(shift.hourly_schedule[1]["start"], "23:00")
        self.assertEqual(shift.hourly_schedule[2]["end"], "01:00")
        current_time = timezone.make_aware(datetime(2026, 11, 16, 1, 30))
        self.assertEqual(shift.current_schedule_status(current_dt=current_time)["label"], "Hour 4")

    def test_create_saves_hourly_breaks_and_total_working_minutes(self):
        response = self.client.post(self.url, self.shift_payload(self.factory))

        self.assertRedirects(response, f"{self.url}?factory={self.factory.pk}")
        shift = FactoryShift.objects.get(factory=self.factory, shift_name="Day Shift")
        self.assertEqual(shift.end_time.strftime("%H:%M"), "19:00")
        self.assertEqual(shift.overtime_start_time.strftime("%H:%M"), "17:00")
        self.assertEqual(shift.shift_minutes, 570)
        self.assertEqual(len(shift.hourly_schedule), 10)
        self.assertEqual(shift.hourly_schedule[3]["break_start"], "12:00")
        self.assertEqual(shift.hourly_schedule[3]["break_end"], "12:30")

    def test_custom_hour_name_is_saved_and_used_as_the_current_dashboard_label(self):
        response = self.client.post(
            self.url,
            self.shift_payload(self.factory, hour_1_name="Sewing Setup"),
        )
        self.assertEqual(response.status_code, 302)
        shift = FactoryShift.objects.get(factory=self.factory, shift_name="Day Shift")
        self.assertEqual(shift.hourly_schedule[0]["name"], "Sewing Setup")

        current_time = timezone.make_aware(datetime(2026, 11, 15, 9, 30))
        status = shift.current_schedule_status(current_dt=current_time)

        self.assertEqual(status["label"], "Sewing Setup")
        self.assertEqual(status["number"], 1)

        shift.start_time = "00:00"
        shift.end_time = "23:59"
        shift.hourly_schedule = [{
            "hour": 1,
            "name": "Sewing Setup",
            "start": "00:00",
            "end": "23:59",
            "break_start": "",
            "break_end": "",
        }]
        shift.save()
        session = self.client.session
        session["selected_factory_id"] = self.factory.pk
        session.save()

        dashboard_response = self.client.get(reverse("dashboard:admin_dashboard"))

        self.assertContains(dashboard_response, "Sewing Setup")

    def test_edit_updates_only_the_shift_in_the_selected_factory(self):
        shift = FactoryShift.objects.create(
            factory=self.factory,
            shift_name="Day Shift",
            start_time="09:00",
            end_time="17:00",
            standard_hours=8,
            hourly_schedule=[],
        )
        payload = self.shift_payload(
            self.factory,
            shift_id=str(shift.pk),
            shift_name="Updated Day Shift",
            hour_1_end="09:45",
        )

        response = self.client.post(self.url, payload)

        self.assertRedirects(response, f"{self.url}?factory={self.factory.pk}")
        shift.refresh_from_db()
        self.assertEqual(shift.shift_name, "Updated Day Shift")
        self.assertEqual(shift.hourly_schedule[0]["end"], "09:45")
        self.assertEqual(shift.shift_minutes, 555)

    def test_shift_editor_saves_added_hour_names_and_day_offsets(self):
        shift = FactoryShift.objects.create(
            factory=self.factory,
            shift_name="Night Shift",
            start_time="22:00",
            end_time="06:00",
            standard_hours=8,
            hourly_schedule=[],
        )
        payload = self.shift_payload(
            self.factory,
            shift_id=str(shift.pk),
            shift_name="Night Shift",
            start_time="22:00",
            end_time="07:00",
            schedule_editor="1",
            hour_count="9",
        )
        payload["hour_1_start"] = "22:00"
        payload["hour_1_end"] = "23:00"
        payload["hour_2_start"] = "23:00"
        payload["hour_2_end"] = "00:00"
        payload["hour_2_end_next_day"] = "1"
        for hour in range(1, 11):
            payload[f"hour_{hour}_break_start"] = ""
            payload[f"hour_{hour}_break_end"] = ""
        payload["hour_4_break_enabled"] = "1"
        payload["hour_4_break_start"] = "01:15"
        payload["hour_4_break_end"] = "01:30"
        for hour in range(3, 10):
            payload[f"hour_{hour}_start"] = f"{hour - 3:02d}:00"
            payload[f"hour_{hour}_end"] = f"{hour - 2:02d}:00"
            payload[f"hour_{hour}_start_next_day"] = "1"
            payload[f"hour_{hour}_end_next_day"] = "1"
        payload["hour_9_name"] = "Final Packing"
        payload["hour_9_start"] = "06:00"
        payload["hour_9_end"] = "07:00"
        payload["hour_9_start_next_day"] = "1"
        payload["hour_9_end_next_day"] = "1"

        response = self.client.post(self.url, payload)

        self.assertRedirects(response, f"{self.url}?factory={self.factory.pk}")
        shift.refresh_from_db()
        self.assertEqual(len(shift.hourly_schedule), 9)
        self.assertEqual(shift.hourly_schedule[1]["end_day"], 1)
        self.assertEqual(shift.hourly_schedule[8]["name"], "Final Packing")
        self.assertEqual(shift.hourly_schedule[8]["start_day"], 1)
        self.assertEqual(shift.hourly_schedule[8]["end"], "07:00")
        self.assertEqual(shift.end_time.strftime("%H:%M"), "07:00")
        self.assertEqual(shift.hourly_schedule[3]["break_start"], "01:15")
        self.assertEqual(shift.hourly_schedule[3]["break_end"], "01:30")

    def test_shift_from_another_factory_cannot_be_edited(self):
        shift = FactoryShift.objects.create(
            factory=self.other_factory,
            shift_name="South Shift",
            start_time="09:00",
            end_time="17:00",
            standard_hours=8,
        )

        response = self.client.post(
            self.url,
            self.shift_payload(
                self.factory,
                shift_id=str(shift.pk),
                shift_name="Changed",
            ),
        )

        self.assertRedirects(response, f"{self.url}?factory={self.factory.pk}")
        shift.refresh_from_db()
        self.assertEqual(shift.shift_name, "South Shift")

    def test_non_superuser_cannot_open_shift_management(self):
        user = get_user_model().objects.create_user(
            username="production-user",
            email="production@example.com",
            password="secure-test-password",
        )
        self.client.force_login(user)

        response = self.client.get(self.url)

        self.assertRedirects(response, reverse("dashboard:user_dashboard"))
