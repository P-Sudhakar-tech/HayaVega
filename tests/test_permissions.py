import tempfile
import unittest
from pathlib import Path

from hayavega.auth import add_user_to_group, create_group, create_superuser, create_user, get_group_by_name, remove_user_from_group, user_permissions
from hayavega.security import has_permission_required

PASSWORD = "Correct-Horse-7!"


class GroupTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "users.sqlite3"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_create_group_stores_sorted_unique_permissions(self):
        group = create_group(self.database, "editors", ["blog.change_post", "blog.add_post", "blog.add_post"])
        self.assertEqual(group.name, "editors")
        self.assertEqual(group.permissions, ("blog.add_post", "blog.change_post"))

    def test_duplicate_group_name_is_rejected(self):
        create_group(self.database, "editors", [])
        with self.assertRaisesRegex(ValueError, "already exists"):
            create_group(self.database, "editors", [])

    def test_get_group_by_name_round_trips(self):
        create_group(self.database, "editors", ["blog.add_post"])
        found = get_group_by_name(self.database, "editors")
        self.assertIsNotNone(found)
        self.assertEqual(found.permissions, ("blog.add_post",))

    def test_get_group_by_name_returns_none_when_missing(self):
        self.assertIsNone(get_group_by_name(self.database, "does-not-exist"))


class UserPermissionsTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "users.sqlite3"
        self.user = create_user(self.database, "alice", "alice@example.com", PASSWORD)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_user_with_no_groups_has_no_permissions(self):
        self.assertEqual(user_permissions(self.database, self.user), set())

    def test_permissions_are_the_union_of_every_group_the_user_belongs_to(self):
        editors = create_group(self.database, "editors", ["blog.add_post", "blog.change_post"])
        reviewers = create_group(self.database, "reviewers", ["blog.change_post", "blog.publish_post"])
        add_user_to_group(self.database, self.user, editors)
        add_user_to_group(self.database, self.user, reviewers)
        self.assertEqual(user_permissions(self.database, self.user), {"blog.add_post", "blog.change_post", "blog.publish_post"})

    def test_adding_the_same_membership_twice_does_not_duplicate(self):
        editors = create_group(self.database, "editors", ["blog.add_post"])
        add_user_to_group(self.database, self.user, editors)
        add_user_to_group(self.database, self.user, editors)
        self.assertEqual(user_permissions(self.database, self.user), {"blog.add_post"})

    def test_removing_a_user_from_a_group_drops_its_permissions(self):
        editors = create_group(self.database, "editors", ["blog.add_post"])
        add_user_to_group(self.database, self.user, editors)
        remove_user_from_group(self.database, self.user, editors)
        self.assertEqual(user_permissions(self.database, self.user), set())


class HasPermissionRequiredTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "users.sqlite3"
        self.user = create_user(self.database, "alice", "alice@example.com", PASSWORD)
        self.superuser = create_superuser(self.database, "root", "root@example.com", PASSWORD)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _call(self, view, request):
        return view(request)

    def test_anonymous_request_is_rejected(self):
        view = has_permission_required(self.database, "blog.add_post")(lambda request: "ok")
        response = self._call(view, _FakeRequest(token=None))
        self.assertEqual(response.status, 401)

    def test_user_without_the_permission_is_rejected(self):
        view = has_permission_required(self.database, "blog.add_post")(lambda request: "ok")
        response = self._call(view, _FakeRequest(token=_session_token(self.database, self.user)))
        self.assertEqual(response.status, 403)

    def test_user_with_the_permission_is_allowed(self):
        editors = create_group(self.database, "editors", ["blog.add_post"])
        add_user_to_group(self.database, self.user, editors)
        view = has_permission_required(self.database, "blog.add_post")(lambda request: "ok")
        response = self._call(view, _FakeRequest(token=_session_token(self.database, self.user)))
        self.assertEqual(response, "ok")

    def test_superuser_bypasses_the_permission_check(self):
        view = has_permission_required(self.database, "blog.add_post")(lambda request: "ok")
        response = self._call(view, _FakeRequest(token=_session_token(self.database, self.superuser)))
        self.assertEqual(response, "ok")


def _session_token(database, user) -> str:
    from hayavega.auth import create_session

    return create_session(database, user)


class _FakeRequest:
    def __init__(self, token: str | None):
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.cookies: dict[str, str] = {}


if __name__ == "__main__":
    unittest.main()
