import re
import tempfile
import unittest
from pathlib import Path

from mangoo.admin import admin_routes
from mangoo.audit import audit_events
from mangoo.auth import create_session, create_superuser, create_user
from mangoo.jobs import _mark_failed, claim_next_job, enqueue
from mangoo.runtime import create_application
from mangoo.testing import TestClient


def _csrf_from(response) -> tuple[str, str]:
    token = re.search(r"name='csrf_token' value='([^']*)'", response.text).group(1)
    cookie_value = re.search(r"mangoo_csrf=([^;]*)", response.headers.get("Set-Cookie", "")).group(1)
    return token, cookie_value


class JobsAdminSetup(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "app.sqlite3"
        self.admin_user = create_superuser(self.database, "admin", "admin@example.com", "Strong-Password-7!")
        self.member = create_user(self.database, "member", "member@example.com", "Strong-Password-7!")
        self.admin_token = create_session(self.database, self.admin_user)
        self.member_token = create_session(self.database, self.member)
        self.app = create_application(admin_routes(self.database, jobs=True))
        self.client = TestClient(self.app)

    def tearDown(self):
        self.directory.cleanup()

    def bearer(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def _dead_letter_job(self, name: str = "send_email") -> int:
        job_id = enqueue(self.database, name, max_attempts=1)
        claim_next_job(self.database)
        _mark_failed(self.database, job_id, attempts=1, max_attempts=1, exc=ValueError("boom"))
        return job_id


class RegistrationAndPermissionTests(JobsAdminSetup):
    def test_index_links_to_jobs_when_enabled(self):
        response = self.client.get("/admin", headers=self.bearer(self.admin_token))
        self.assertIn("/admin/jobs", response.text)

    def test_index_does_not_link_to_jobs_when_disabled(self):
        app = create_application(admin_routes(self.database))
        client = TestClient(app)
        response = client.get("/admin", headers=self.bearer(self.admin_token))
        self.assertNotIn("/admin/jobs", response.text)

    def test_non_superuser_is_denied(self):
        response = self.client.get("/admin/jobs", headers=self.bearer(self.member_token))
        self.assertEqual(response.status_code, 403)

    def test_jobs_route_404s_when_not_enabled(self):
        app = create_application(admin_routes(self.database))
        client = TestClient(app)
        response = client.get("/admin/jobs", headers=self.bearer(self.admin_token))
        self.assertEqual(response.status_code, 404)


class ListViewTests(JobsAdminSetup):
    def test_lists_enqueued_jobs(self):
        enqueue(self.database, "send_email", {"to": "a@example.com"})
        response = self.client.get("/admin/jobs", headers=self.bearer(self.admin_token))
        self.assertIn("send_email", response.text)
        self.assertIn("pending", response.text)

    def test_filters_by_status(self):
        self._dead_letter_job("dead_job")
        enqueue(self.database, "pending_job")
        response = self.client.get("/admin/jobs?status=dead", headers=self.bearer(self.admin_token))
        self.assertIn("dead_job", response.text)
        self.assertNotIn("pending_job", response.text)

    def test_unknown_status_falls_back_to_all(self):
        enqueue(self.database, "send_email")
        response = self.client.get("/admin/jobs?status=bogus", headers=self.bearer(self.admin_token))
        self.assertIn("send_email", response.text)

    def test_pagination(self):
        for i in range(3):
            enqueue(self.database, f"job{i}")
        response = self.client.get("/admin/jobs?page=999", headers=self.bearer(self.admin_token))
        self.assertIn("Page 1 of 1", response.text)

    def test_list_viewed_is_audited(self):
        self.client.get("/admin/jobs", headers=self.bearer(self.admin_token))
        events = [e for e in audit_events(self.database) if e["event_type"] == "admin.jobs.list_viewed"]
        self.assertEqual(len(events), 1)


class RetryTests(JobsAdminSetup):
    def test_retry_resets_dead_job_to_pending(self):
        job_id = self._dead_letter_job()
        get_response = self.client.get("/admin/jobs", headers=self.bearer(self.admin_token))
        token, cookie_value = _csrf_from(get_response)
        headers = {**self.bearer(self.admin_token), "Cookie": f"mangoo_csrf={cookie_value}"}
        response = self.client.post(f"/admin/jobs/{job_id}/retry", form_data={"csrf_token": token}, headers=headers)
        self.assertEqual(response.status_code, 303)

        listing = self.client.get("/admin/jobs?status=pending", headers=self.bearer(self.admin_token))
        self.assertIn("send_email", listing.text)

    def test_retry_is_audited(self):
        job_id = self._dead_letter_job()
        get_response = self.client.get("/admin/jobs", headers=self.bearer(self.admin_token))
        token, cookie_value = _csrf_from(get_response)
        headers = {**self.bearer(self.admin_token), "Cookie": f"mangoo_csrf={cookie_value}"}
        self.client.post(f"/admin/jobs/{job_id}/retry", form_data={"csrf_token": token}, headers=headers)
        events = [e for e in audit_events(self.database) if e["event_type"] == "admin.jobs.retried"]
        self.assertEqual(events[0]["metadata"]["id"], job_id)

    def test_retry_on_non_dead_job_is_a_no_op(self):
        job_id = enqueue(self.database, "send_email")
        headers = {**self.bearer(self.admin_token), "Cookie": "mangoo_csrf=matching-token"}
        self.client.post(f"/admin/jobs/{job_id}/retry", form_data={"csrf_token": "matching-token"}, headers=headers)
        events = [e for e in audit_events(self.database) if e["event_type"] == "admin.jobs.retried"]
        self.assertEqual(events, [])

    def test_retry_via_get_is_method_not_allowed(self):
        job_id = self._dead_letter_job()
        response = self.client.get(f"/admin/jobs/{job_id}/retry", headers=self.bearer(self.admin_token))
        self.assertEqual(response.status_code, 405)

    def test_retry_without_csrf_token_is_rejected_for_cookie_sessions(self):
        job_id = self._dead_letter_job()
        token = create_session(self.database, self.admin_user)
        response = self.client.post(f"/admin/jobs/{job_id}/retry", form_data={}, headers={"Cookie": f"mangoo_session={token}"})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
