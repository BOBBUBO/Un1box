"""Host-neutral record safety; synthetic paths only, no state copying."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lighttouch_desktop.library import RecordError, check_collection, check_update, display_title

canonical = lambda value: Path(value).resolve()


def record(identity="a", base="/prepared/n72", state="/devices/a/state"):
    return dict(id=identity * 32, name="My iPod", base=base, state=state)


class RecordTests(unittest.TestCase):
    def test_named_profiles_may_share_base_but_not_writable_identity(self):
        a, b = record(), record("b", state="/devices/b/state")
        check_collection([a, b], b["id"], canonical)
        for state in [a["state"], a["state"] + "/child", "/devices/a"]:
            with self.subTest(state=state), self.assertRaisesRegex(RecordError, "own writable"):
                check_update(dict(b, state=state), [a], canonical)

    def test_rename_keeps_system_binding_and_version_switch_needs_new_record(self):
        a = record()
        check_update(dict(a, name="Renamed"), [a], canonical)
        for changed in [dict(a, base="/prepared/n81"), dict(a, state="/other/state")]:
            with self.assertRaisesRegex(RecordError, "Add a new configuration"):
                check_update(changed, [a], canonical)
        b = record("b", base="/prepared/n81", state="/devices/b/state")
        check_update(b, [a], canonical)

    def test_state_may_not_overlap_any_saved_base(self):
        a = record()
        for b in [record("b", base="/prepared/n81", state=a["base"] + "/state"), record("b", base=a["state"], state="/other")]:
            with self.assertRaisesRegex(RecordError, "every saved prepared base"):
                check_update(b, [a], canonical)

    def test_invalid_or_missing_selection_does_not_mutate_records(self):
        a = record()
        for rows, selected in [([a, a], ""), ([dict(a, id=[])], ""), ([a], "b" * 32)]:
            with self.assertRaises(RecordError):
                check_collection(rows, selected, canonical)
        self.assertEqual(a, record())

    def test_titles_distinguish_build_without_claiming_qualification(self):
        title = display_title("Demo", "n81ap", "8C148")
        self.assertIn("4.2.1", title)
        self.assertIn("prepared", title)
        self.assertIn("iPod touch (4th generation)", title)
        self.assertNotIn("n81ap", title)
        self.assertNotIn("native checks", title)
        self.assertNotIn("qualified", title)
        self.assertIn("UNKNOWN", display_title("Demo", "n81ap", "UNKNOWN"))


if __name__ == "__main__":
    unittest.main()
