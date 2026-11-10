import re
import tempfile
import unittest
from pathlib import Path

from hayavega.admin import admin_routes
from hayavega.audit import audit_events
from hayavega.auth import create_session, create_superuser, create_user
from hayavega.jobs import _mark_failed, claim_next_job, enqueue
from hayavega.runtime import create_application
from hayavega.testing import TestClient


def _csrf_from(response) -> tuple[str, str]:
    token = re.search(r"name='csrf_token' value='([^']*)'", response.text).group(1)
    cookie_value = re.search(r"hayavega_csrf=([^;]*)", response.headers.get("Set-Cookie", "")).group(1)
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
        headers = {**self.bearer(self.admin_token), "Cookie": f"hayavega_csrf={cookie_value}"}
        response = self.client.post(f"/admin/jobs/{job_id}/retry", form_data={"csrf_token": token}, headers=headers)
        self.assertEqual(response.status_code, 303)

        listing = self.client.get("/admin/jobs?status=pending", headers=self.bearer(self.admin_token))
        self.assertIn("send_email", listing.text)

    def test_retry_is_audited(self):
        job_id = self._dead_letter_job()
        get_response = self.client.get("/admin/jobs", headers=self.bearer(self.admin_token))
        token, cookie_value = _csrf_from(get_response)
        headers = {**self.bearer(self.admin_token), "Cookie": f"hayavega_csrf={cookie_value}"}
        self.client.post(f"/admin/jobs/{job_id}/retry", form_data={"csrf_token": token}, headers=headers)
        events = [e for e in audit_events(self.database) if e["event_type"] == "admin.jobs.retried"]
        self.assertEqual(events[0]["metadata"]["id"], job_id)

    def test_retry_on_non_dead_job_is_a_no_op(self):
        job_id = enqueue(self.database, "send_email")
        headers = {**self.bearer(self.admin_token), "Cookie": "hayavega_csrf=matching-token"}
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
        response = self.client.post(f"/admin/jobs/{job_id}/retry", form_data={}, headers={"Cookie": f"hayavega_session={token}"})
        self.assertEqual(response.status_code, 403)

    def test_retry_url_missing_the_retry_suffix_is_404(self):
        job_id = self._dead_letter_job()
        response = self.client.post(f"/admin/jobs/{job_id}", form_data={}, headers=self.bearer(self.admin_token))
        self.assertEqual(response.status_code, 404)

    def test_retry_with_a_non_numeric_job_id_is_404(self):
        response = self.client.post("/admin/jobs/not-a-number/retry", form_data={}, headers=self.bearer(self.admin_token))
        self.assertEqual(response.status_code, 404)

    def test_retry_method_guard_rejects_a_non_post_request(self):
        """The route itself only ever accepts ``POST`` (``methods=("POST",)``),
        so ``jobs_retry``'s own method guard is unreachable through routing
        -- the view is pulled off the registered routes and called directly
        with a ``GET`` request to exercise it."""
        from hayavega.request import Request

        job_id = self._dead_letter_job()
        routes = admin_routes(self.database, jobs=True)
        retry_route = next(route for route in routes if route.pattern == "admin/jobs/<path>")
        request = Request(method="GET", path=f"/admin/jobs/{job_id}/retry", query_params={}, body=b"", headers=self.bearer(self.admin_token))
        response = retry_route.view(request)
        self.assertEqual(response.status, 405)
        self.assertEqual(dict(response.headers).get("Allow"), "POST")


class SignalsIntegrationTests(JobsAdminSetup):
    def test_jobs_list_viewed_publishes_an_admin_action_event(self):
        from threading import Event

        from hayavega.addons.signals import SignalBus

        bus = SignalBus()
        received = []
        seen = Event()

        def on_action(event):
            received.append(event.data["action"])
            seen.set()

        bus.connect("admin.action", on_action)
        app = create_application(admin_routes(self.database, jobs=True, signals=bus))
        client = TestClient(app)
        client.get("/admin/jobs", headers=self.bearer(self.admin_token))
        seen.wait(2)
        bus.close()
        self.assertIn("jobs.list_viewed", received)

    def test_retrying_a_dead_job_publishes_an_admin_action_event(self):
        from threading import Event

        from hayavega.addons.signals import SignalBus

        bus = SignalBus()
        received = []
        seen = Event()

        def on_action(event):
            received.append(event.data["action"])
            if "jobs.retried" in received:
                seen.set()

        bus.connect("admin.action", on_action)
        app = create_application(admin_routes(self.database, jobs=True, signals=bus))
        client = TestClient(app)
        job_id = self._dead_letter_job()
        get_response = client.get("/admin/jobs", headers=self.bearer(self.admin_token))
        token, cookie_value = _csrf_from(get_response)
        headers = {**self.bearer(self.admin_token), "Cookie": f"hayavega_csrf={cookie_value}"}
        client.post(f"/admin/jobs/{job_id}/retry", form_data={"csrf_token": token}, headers=headers)
        seen.wait(2)
        bus.close()
        self.assertIn("jobs.retried", received)


class JobsPaginationTests(JobsAdminSetup):
    def test_first_page_shows_a_next_link_only(self):
        for i in range(30):
            enqueue(self.database, f"job{i}")
        response = self.client.get("/admin/jobs", headers=self.bearer(self.admin_token))
        self.assertIn("Page 1 of 2", response.text)
        self.assertIn("Next", response.text)
        self.assertNotIn("Previous", response.text)

    def test_last_page_shows_a_previous_link_only(self):
        for i in range(30):
            enqueue(self.database, f"job{i}")
        response = self.client.get("/admin/jobs?page=2", headers=self.bearer(self.admin_token))
        self.assertIn("Page 2 of 2", response.text)
        self.assertIn("Previous", response.text)
        self.assertNotIn("Next", response.text)


if __name__ == "__main__":
    unittest.main()
