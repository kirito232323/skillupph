"""Regression tests for bugs fixed in the tracker app.

Run with:  python manage.py test tracker
"""
import os
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .course_curriculum import get_program_curriculum
from .id_crypto import decrypt_bytes, encrypt_bytes, is_encrypted
from .job_presets import match_preset_key
from .models import (
    CentralizedSkill,
    JobVacancy,
    Profile,
    Referral,
    TrainingEnrollment,
    TrainingProgram,
)


def make_user(email, role, **profile_kwargs):
    user = User.objects.create_user(username=email, email=email, password="TestPass123!")
    defaults = {
        "role": role,
        "is_verified": True,
        "is_profile_complete": role != "applicant",
    }
    defaults.update(profile_kwargs)
    profile = Profile.objects.create(user=user, **defaults)
    return user, profile


class SignupSecurityTests(TestCase):
    def test_public_signup_cannot_create_admin(self):
        response = self.client.post(
            reverse("signup"),
            {
                "firstname": "Evil",
                "lastname": "Admin",
                "email": "role_probe@example.com",
                "password": "TestPass123!",
                "confirm_password": "TestPass123!",
                "role": "admin",
            },
        )
        self.assertEqual(response.status_code, 302)
        pending = self.client.session.get("pending_signup")
        self.assertEqual(pending["role"], "applicant")

        self.client.post(reverse("verify"), {"code": pending["otp"]})
        profile = Profile.objects.get(user__username="role_probe@example.com")
        self.assertEqual(profile.role, "applicant")


class ResetDatabaseAuthorizationTests(TestCase):
    def test_anonymous_and_applicant_cannot_open_reset_page(self):
        url = reverse("reset_db_view")
        self.assertEqual(self.client.get(url).status_code, 302)

        user, _ = make_user("reset_probe@example.com", "applicant", is_profile_complete=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_admin_can_open_reset_page(self):
        user, _ = make_user("reset_admin@example.com", "admin")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("reset_db_view")).status_code, 200)


class JobApplicationTests(TestCase):
    def setUp(self):
        self.employer_user, self.employer = make_user("emp@example.com", "employer")
        self.applicant_user, self.applicant = make_user(
            "app@example.com", "applicant", is_profile_complete=True
        )
        self.vacancy = JobVacancy.objects.create(
            title="Last Slot Role",
            employer=self.employer,
            status="Open",
            slots=1,
            remaining_slots=1,
        )
        self.client.force_login(self.applicant_user)

    def test_filling_last_slot_does_not_withdraw_the_application(self):
        response = self.client.post(reverse("apply_to_job", args=[self.vacancy.id]))
        self.assertEqual(response.status_code, 302)

        self.vacancy.refresh_from_db()
        referral = Referral.objects.get(applicant=self.applicant, job_vacancy=self.vacancy)
        self.assertEqual(self.vacancy.status, "Closed")
        self.assertEqual(self.vacancy.remaining_slots, 0)
        self.assertEqual(referral.status, "Pending")


class CourseProgressTests(TestCase):
    def setUp(self):
        self.user, self.profile = make_user(
            "learner@example.com", "applicant", is_profile_complete=True
        )
        self.program = TrainingProgram.objects.create(title="Test Program")
        self.enrollment = TrainingEnrollment.objects.create(
            profile=self.profile, training_program=self.program
        )
        self.curriculum = get_program_curriculum(self.program)
        self.lessons = [les for m in self.curriculum["modules"] for les in m["lessons"]]
        self.assessment = next(les for les in self.lessons if les["type"] == "assessment")
        self.content_lesson = next(les for les in self.lessons if les["type"] != "assessment")
        self.client.force_login(self.user)

    def _complete(self, lesson_id):
        return self.client.post(
            reverse("applicant_complete_lesson", args=[self.enrollment.id]),
            data='{"lesson_id": "%s"}' % lesson_id,
            content_type="application/json",
        )

    def test_assessment_lesson_cannot_be_marked_complete_directly(self):
        response = self._complete(self.assessment["id"])
        self.assertEqual(response.status_code, 400)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.progress_percentage, 0)

    def test_unknown_lesson_id_is_rejected(self):
        response = self._complete("bogus_lesson")
        self.assertEqual(response.status_code, 400)
        self.enrollment.refresh_from_db()
        self.assertLessEqual(self.enrollment.progress_percentage, 100)

    def test_certificate_is_locked_until_assessment_passes(self):
        self.enrollment.progress_percentage = 100
        self.enrollment.time_spent_seconds = self.enrollment.required_training_seconds
        self.enrollment.assessment_results = {"passed": False}
        self.enrollment.save()

        response = self.client.get(
            reverse("applicant_download_certificate", args=[self.enrollment.id])
        )
        self.assertEqual(response.status_code, 302)
        self.enrollment.refresh_from_db()
        self.assertIsNone(self.enrollment.certificate_code)


class OfferGuardTests(TestCase):
    def setUp(self):
        self.employer_user, self.employer = make_user("offer_emp@example.com", "employer")
        self.applicant_user, self.applicant = make_user(
            "offer_app@example.com", "applicant", is_profile_complete=True
        )
        self.vacancy = JobVacancy.objects.create(
            title="Offer Role",
            employer=self.employer,
            status="Open",
            slots=3,
            remaining_slots=2,
        )
        self.client.force_login(self.applicant_user)

    def test_declining_offer_restores_slot_only_once(self):
        referral = Referral.objects.create(
            applicant=self.applicant,
            job_vacancy=self.vacancy,
            status="Accepted — Awaiting Onboarding",
        )
        for _ in range(2):
            self.client.post(
                reverse("applicant_dashboard"),
                {"action": "decline_offer", "referral_id": referral.id, "decline_reason": "Test"},
            )
            self.vacancy.refresh_from_db()
        self.assertEqual(self.vacancy.remaining_slots, 3)

    def test_cannot_confirm_an_offer_that_was_never_sent(self):
        referral = Referral.objects.create(
            applicant=self.applicant, job_vacancy=self.vacancy, status="Pending"
        )
        self.client.post(
            reverse("applicant_dashboard"),
            {"action": "confirm_offer", "referral_id": referral.id},
        )
        referral.refresh_from_db()
        self.assertEqual(referral.status, "Pending")

TEST_MEDIA_ROOT = str(Path(__file__).resolve().parent.parent / "scratch" / "test_media")
os.makedirs(TEST_MEDIA_ROOT, exist_ok=True)


class NameCapitalizationTests(TestCase):
    def test_signup_capitalizes_first_and_last_name(self):
        response = self.client.post(
            reverse("signup"),
            {
                "firstname": "juan carlos",
                "lastname": "dela cruz",
                "email": "name_probe@example.com",
                "password": "TestPass123!",
                "confirm_password": "TestPass123!",
                "role": "applicant",
            },
        )
        self.assertEqual(response.status_code, 302)
        pending = self.client.session.get("pending_signup")
        self.assertEqual(pending["firstname"], "Juan Carlos")
        self.assertEqual(pending["lastname"], "Dela Cruz")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class SignupIdScanTests(TestCase):
    def _fake_fields(self):
        return {
            "id_type": "Philippine Passport",
            "id_number": "P1234567A",
            "first_name": "Juan",
            "last_name": "Dela Cruz",
            "middle_name": "Santos",
            "full_name": "Juan Santos Dela Cruz",
            "birthdate": "1998-04-12",
            "address": "123 Mabini St., Malolos, Bulacan",
            "sex": "M",
            "confidence": 92,
        }

    def test_id_scan_prefills_and_verifies_account(self):
        upload = SimpleUploadedFile(
            "valid_id.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, content_type="image/png"
        )
        with patch("tracker.ai_services.extract_id_document", return_value=self._fake_fields()):
            response = self.client.post(reverse("signup_scan_id"), {"id_document": upload})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["fields"]["id_number"], "P1234567A")
        token = payload["token"]

        response = self.client.post(
            reverse("signup"),
            {
                "firstname": "juan",
                "lastname": "dela cruz",
                "email": "idscan_probe@example.com",
                "password": "TestPass123!",
                "confirm_password": "TestPass123!",
                "role": "applicant",
                "id_scan_token": token,
                "id_type": "Philippine Passport",
                "id_number": "P1234567A",
            },
        )
        self.assertEqual(response.status_code, 302)
        pending = self.client.session.get("pending_signup")
        self.client.post(reverse("verify"), {"code": pending["otp"]})

        profile = Profile.objects.get(user__username="idscan_probe@example.com")
        self.assertTrue(profile.id_verified)
        self.assertTrue(profile.id_document.name.startswith("id_documents/"))
        self.assertEqual(profile.id_number, "P1234567A")
        self.assertEqual(profile.address, "123 Mabini St., Malolos, Bulacan")

    def test_id_scan_rejects_unsupported_file_type(self):
        upload = SimpleUploadedFile("payload.exe", b"x", content_type="application/octet-stream")
        response = self.client.post(reverse("signup_scan_id"), {"id_document": upload})
        self.assertEqual(response.status_code, 400)


class TransferableSkillsTests(TestCase):
    def test_transferable_skills_are_seeded(self):
        self.assertTrue(
            CentralizedSkill.objects.filter(category="Transferable Skills").exists()
        )
        self.assertTrue(CentralizedSkill.objects.filter(name="API Integration").exists())


class JobPresetTests(TestCase):
    def test_title_aliases_map_to_presets(self):
        self.assertEqual(match_preset_key("Web Developer"), "software_dev")
        self.assertEqual(match_preset_key("Senior Full Stack Developer"), "software_dev")
        self.assertEqual(match_preset_key("Customer Service Representative"), "bpo_support")
        self.assertIsNone(match_preset_key("Astronaut"))

    def test_job_management_page_exposes_preset_payload(self):
        user, _ = make_user("preset_emp@example.com", "employer")
        self.client.force_login(user)
        response = self.client.get(reverse("job_management_employer"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "software_dev")
        self.assertContains(response, "applyRolePreset")


class HiringTrackerScoreCardTests(TestCase):
    def test_candidate_cards_show_score_card(self):
        employer_user, employer = make_user("score_emp@example.com", "employer")
        applicant_user, applicant = make_user(
            "score_app@example.com", "applicant", is_profile_complete=True
        )
        vacancy = JobVacancy.objects.create(
            title="Score Card Role", employer=employer, status="Open", slots=2, remaining_slots=2
        )
        Referral.objects.create(applicant=applicant, job_vacancy=vacancy, status="Pending")
        self.client.force_login(employer_user)

        response = self.client.get(reverse("hiring_tracker_employer"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Score Card")
        self.assertContains(response, "% fit")


class AdminNotificationPageTests(TestCase):
    def test_admin_notification_button_target_renders(self):
        user, _ = make_user("notify_admin@example.com", "admin")
        self.client.force_login(user)
        response = self.client.get(reverse("admin_notifications"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PESO Admin Notifications")


class IDDocumentEncryptionTests(TestCase):
    def test_id_crypto_roundtrip_and_tamper_detection(self):
        plaintext = b"PHILIPPINE NATIONAL ID - CONFIDENTIAL"
        blob = encrypt_bytes(plaintext)
        self.assertTrue(is_encrypted(blob))
        self.assertNotIn(b"CONFIDENTIAL", blob)
        self.assertEqual(decrypt_bytes(blob), plaintext)

        tampered = bytearray(blob)
        tampered[-1] ^= 0x01
        with self.assertRaises(ValueError):
            decrypt_bytes(bytes(tampered))
