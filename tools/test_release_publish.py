#!/usr/bin/env python3
"""Exercise release cleanup guards without network, credentials or mutations."""
import unittest

from publish_single_release import GitHub, check_tag, clean_old, plan


class FakeGitHub:
    def __init__(self, entries):
        self.entries = dict(entries)
        self.deletions = []

    def call(self, path, method="GET", body=None, missing_ok=False):
        if method == "DELETE":
            self.deletions.append(path)
            self.entries.pop(path)
            return None
        if path not in self.entries and not missing_ok:
            raise ValueError("Missing mock object")
        return self.entries.get(path)


class ReleaseGuards(unittest.TestCase):
    def setUp(self):
        self.request = {"version": "1.2.0", "remove_releases_after_verified_publication": [
            {"id": 1, "tag": "v1.0.0", "expected_asset_bytes": 123}],
            "remove_obsolete_actions_artifacts": []}
        self.current = {"id": 2, "tag_name": "v1.2.0", "draft": False}
        self.old = {"id": 1, "tag_name": "v1.0.0", "draft": False,
                    "immutable": False, "assets": [{"size": 123}]}

    def test_actual_plan_is_only_old_versions(self):
        self.assertEqual(plan()["pack"], "moe")

    def test_no_cleanup_before_publication(self):
        api = FakeGitHub({"/releases/1": self.old})
        with self.assertRaises(ValueError):
            clean_old(api, self.request, {**self.current, "draft": True})
        self.assertEqual(api.deletions, [])

    def test_never_delete_replacement(self):
        api = FakeGitHub({"/releases/1": self.old})
        with self.assertRaises(ValueError):
            clean_old(api, self.request, {**self.current, "id": 1})
        self.assertEqual(api.deletions, [])

    def test_changed_release_is_preserved(self):
        for change in ({"tag_name": "v2.0.0"}, {"immutable": True},
                       {"assets": [{"size": 124}]}, {"draft": True}):
            api = FakeGitHub({"/releases/1": {**self.old, **change}})
            with self.assertRaises(ValueError):
                clean_old(api, self.request, self.current)
            self.assertEqual(api.deletions, [])

    def test_cleanup_is_idempotent_and_never_changes_refs(self):
        api = FakeGitHub({"/releases/1": self.old})
        result = clean_old(api, self.request, self.current)
        self.assertEqual(result["release_bytes_removed"], 123)
        self.assertFalse(result["git_history_and_tags_deleted"])
        self.assertEqual(api.deletions, ["/releases/1"])
        again = clean_old(api, self.request, self.current)
        self.assertEqual(again["release_bytes_removed"], 0)
        self.assertEqual(again["releases"][0]["status"], "already_absent")

    def test_existing_conflicting_tag_fails(self):
        api = FakeGitHub({"/git/ref/tags/v1.2.0": {"object": {"type": "commit", "sha": "a" * 40}}})
        with self.assertRaises(ValueError):
            check_tag(api, "1.2.0", "b" * 40)
        self.assertEqual(api.deletions, [])

    def test_remote_digest_and_upload_state_are_required(self):
        item = {"state": "uploaded", "size": 123, "digest": "sha256:" + "a" * 64}
        GitHub.check_asset(item, 123, "a" * 64)
        for change in ({"state": "starter"}, {"size": 124}, {"digest": "sha256:" + "b" * 64}):
            with self.assertRaises(ValueError):
                GitHub.check_asset({**item, **change}, 123, "a" * 64)


if __name__ == "__main__":
    unittest.main()
