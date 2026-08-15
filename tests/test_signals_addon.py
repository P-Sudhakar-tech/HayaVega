import unittest

from mangoo.addons.pulse import BUILTIN_EVENTS, PulseEvent


class PulseAddonTests(unittest.TestCase):
    def test_addon_exposes_immutable_event_contract_and_reserved_names(self):
        event = PulseEvent("auth.login_succeeded", actor_id=7, data={"method": "password"})
        self.assertIn(event.name, BUILTIN_EVENTS)
        with self.assertRaises(Exception):
            event.name = "changed"
