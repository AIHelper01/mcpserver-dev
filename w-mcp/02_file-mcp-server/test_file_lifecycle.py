"""Run with python -B test_file_lifecycle.py; all operations use temporary files."""

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "file_server", Path(__file__).with_name("w-file_workbench_mcp_server.py")
)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.root = Path(self.workspace.name)
        self.patches = [
            patch.object(server, "HISTORY_FILE", self.root / "state" / "history.json"),
            patch.object(server, "DELETED_FILES_DIR", self.root / "state" / "deleted"),
            patch.object(server, "SNAPSHOTS_DIR", self.root / "state" / "snapshots"),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(self.workspace.cleanup)
        for item in self.patches:
            self.addCleanup(item.stop)

    def test_exact_newlines_and_exclusive_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "中英.ts"
            for text in ["hello\r\n你好\r\n", "hello\n你好\n"]:
                self.assertTrue(
                    server.create_file(str(path), text, overwrite=True)["ok"]
                )
                self.assertEqual(path.read_bytes(), text.encode("utf-8"))
                self.assertEqual(
                    server.create_file(str(path), "other")["error"], "target_exists"
                )

    def test_delete_restore_binary_and_conflict_in_both_languages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(server, "DELETED_FILES_DIR", root / "backups"):
                for language in ["en", "zh"]:
                    path = root / "示例.go"
                    original = b"\x00\xff\r\nhello"
                    path.write_bytes(original)
                    result = server.delete_file(str(path), language=language)
                    self.assertTrue(result["ok"])
                    self.assertFalse(path.exists())
                    token = result["deletion_id"]
                    path.write_bytes(b"keep")
                    self.assertEqual(
                        server.restore_deleted_file(token)["error"], "target_exists"
                    )
                    self.assertEqual(path.read_bytes(), b"keep")
                    path.unlink()
                    self.assertTrue(
                        server.restore_deleted_file(token, language=language)["ok"]
                    )
                    self.assertEqual(path.read_bytes(), original)

    def test_rejections_and_backup_failure_keep_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(server.delete_file(str(root))["error"], "not_a_file")
            self.assertEqual(
                server.delete_file(str(root / "missing"))["error"], "not_found"
            )
            self.assertEqual(
                server.restore_deleted_file("../escape")["error"], "invalid_deletion_id"
            )
            path = root / "source"
            path.write_bytes(b"keep")
            with (
                patch.object(server, "DELETED_FILES_DIR", root / "backup"),
                patch.object(server.shutil, "copy2", side_effect=OSError("disk full")),
            ):
                self.assertEqual(
                    server.delete_file(str(path))["error"], "delete_failed"
                )
                self.assertEqual(path.read_bytes(), b"keep")

    def test_atomic_overwrite_preserves_original_on_failure(self):
        for language in ["en", "zh"]:
            path = self.root / "existing.txt"
            path.write_bytes(b"valuable original")
            result = server.create_file(
                str(path), "\ud800", overwrite=True, language=language
            )
            self.assertFalse(result["ok"])
            self.assertEqual(path.read_bytes(), b"valuable original")
            with patch.object(server.os, "replace", side_effect=OSError("disk full")):
                result = server.create_file(
                    str(path), "replacement", overwrite=True, language=language
                )
            self.assertFalse(result["ok"])
            self.assertEqual(path.read_bytes(), b"valuable original")
            self.assertFalse(list(self.root.glob(".file-mcp-*")))

    def test_restore_interruption_and_corrupt_backup_leave_no_partial_target(self):
        for language in ["en", "zh"]:
            path = self.root / "restore.bin"
            path.write_bytes(b"complete original")
            deletion = server.delete_file(str(path), language=language)
            token = deletion["deletion_id"]

            def fail_copy(source, target):
                target.write(b"partial")
                raise OSError("disk full")

            with patch.object(server.shutil, "copyfileobj", side_effect=fail_copy):
                self.assertFalse(
                    server.restore_deleted_file(token, language=language)["ok"]
                )
            self.assertFalse(path.exists())
            self.assertTrue(server.restore_deleted_file(token, language=language)["ok"])
            path.unlink()
            (server.DELETED_FILES_DIR / token / "content").write_bytes(b"corrupt")
            result = server.restore_deleted_file(token, language=language)
            self.assertEqual(result["error"], "backup_corrupt")
            self.assertFalse(path.exists())

    def test_rename_and_undo_roll_back_when_history_write_fails(self):
        for language in ["en", "zh"]:
            path = self.root / "before.txt"
            path.write_bytes(b"keep")
            with patch.object(
                server, "_save_history", side_effect=OSError("disk full")
            ):
                result = server.rename_file(str(path), "after.txt", language=language)
            self.assertEqual(result["error"], "history_write_failed")
            self.assertTrue(result["rolled_back"])
            self.assertEqual(path.read_bytes(), b"keep")
            self.assertTrue(
                server.rename_file(str(path), "after.txt", language=language)["ok"]
            )
            with patch.object(
                server, "_save_history", side_effect=OSError("disk full")
            ):
                result = server.restore_last_rename(language=language)
            self.assertEqual(result["error"], "history_write_failed")
            self.assertFalse(path.exists())
            self.assertTrue(server.restore_last_rename(language=language)["ok"])

    def test_undo_rejects_replaced_file_and_history_corruption(self):
        for language in ["en", "zh"]:
            with self.subTest(language=language):
                source = self.root / (language + ".txt")
                source.write_bytes(b"original")
                renamed = self.root / (language + "-renamed.txt")
                self.assertTrue(
                    server.rename_file(str(source), renamed.name, language=language)[
                        "ok"
                    ]
                )
                renamed.unlink()
                renamed.write_bytes(b"unrelated replacement")
                result = server.restore_last_rename(language=language)
                self.assertEqual(result["error"], "file_changed")
                self.assertFalse(source.exists())
                server.HISTORY_FILE.write_text("{broken", encoding="utf-8")
                result = server.rename_file(
                    str(renamed), source.name, language=language
                )
                self.assertEqual(result["error"], "history_unreadable")
                self.assertEqual(
                    server.HISTORY_FILE.read_text(encoding="utf-8"), "{broken"
                )
                server.HISTORY_FILE.unlink()

    def test_delete_detects_external_update_and_preserves_new_version(self):
        for language in ["en", "zh"]:
            path = self.root / "changing.txt"
            path.write_bytes(b"old")
            original_copy = server.shutil.copy2

            def update_during_backup(source, target, _copy=original_copy):
                result = _copy(source, target)
                Path(source).write_bytes(b"new version")
                return result

            with patch.object(server.shutil, "copy2", side_effect=update_during_backup):
                result = server.delete_file(str(path), language=language)
            self.assertEqual(result["error"], "file_changed")
            self.assertEqual(path.read_bytes(), b"new version")
            self.assertIsNone(result["quarantine_path"])

    def test_delete_restore_then_undo_rename(self):
        for language in ["en", "zh"]:
            path = self.root / (language + ".txt")
            path.write_bytes(b"example")
            renamed = self.root / (language + ".go")
            self.assertTrue(
                server.rename_file(str(path), renamed.name, language=language)["ok"]
            )
            deleted = server.delete_file(str(renamed), language=language)
            self.assertTrue(
                server.restore_deleted_file(deleted["deletion_id"], language=language)[
                    "ok"
                ]
            )
            self.assertTrue(server.restore_last_rename(language=language)["ok"])
            self.assertEqual(path.read_bytes(), b"example")

    def test_permission_errors_are_structured_and_localized(self):
        for language in ["en", "zh"]:
            with patch.object(
                Path, "iterdir", side_effect=PermissionError("access denied")
            ):
                result = server.list_files(str(self.root), language=language)
            self.assertEqual(result["error"], "permission_denied")
            self.assertIn(
                "操作失败" if language == "zh" else "operation failed",
                result["message"],
            )

    def test_pagination_filters_and_byte_ranges(self):
        for language, folder in [("en", "notes"), ("zh", "资料")]:
            root = self.root / folder
            root.mkdir()
            for name in ["a.txt", "b.txt", "c.py"]:
                self.assertTrue(
                    server.create_file(
                        str(root / name), "你好hello\r\n", language=language
                    )["ok"]
                )
            page = server.list_files(
                str(root), pattern="*.txt", limit=1, language=language
            )
            self.assertEqual(page["total"], 2)
            self.assertEqual(page["items"][0]["name"], "a.txt")
            page = server.list_files(
                str(root),
                pattern="*.txt",
                limit=1,
                offset=page["next_offset"],
                language=language,
            )
            self.assertEqual(page["items"][0]["name"], "b.txt")
            content, offset = "", 0
            while True:
                result = server.read_file(
                    str(root / "a.txt"),
                    offset=offset,
                    max_bytes=4,
                    encoding="utf-8",
                    language=language,
                )
                self.assertTrue(result["ok"], result)
                content += result["content"]
                if result["next_offset"] is None:
                    break
                offset = result["next_offset"]
            self.assertEqual(content, "你好hello\r\n")
            self.assertEqual(
                server.list_files(str(root), limit=0, language=language)["error"],
                "invalid_range",
            )

    def test_snapshot_replays_conversation_and_verifies_hashes(self):
        for language, folder in [("en", "examples"), ("zh", "代码示例")]:
            root = self.root / folder
            root.mkdir()
            (root / "empty").mkdir()
            original = {
                name + ".txt": (name + " / 你好\r\n").encode("utf-8")
                for name in "abcde"
            }
            for name, data in original.items():
                (root / name).write_bytes(data)
            snapshot = server.create_directory_snapshot(str(root), language=language)
            self.assertTrue(snapshot["ok"], snapshot)
            token = snapshot["snapshot_id"]
            self.assertTrue(
                server.rename_file(
                    str(root / "a.txt"), "python_example.py", language=language
                )["ok"]
            )
            self.assertTrue(
                server.delete_file(str(root / "b.txt"), language=language)["ok"]
            )
            self.assertTrue(
                server.create_file(
                    str(root / "example.ts"),
                    "const message: string = 'hello';",
                    language=language,
                )["ok"]
            )
            self.assertTrue(
                server.create_file(
                    str(root / "c.txt"), "changed", overwrite=True, language=language
                )["ok"]
            )
            (root / "empty").rmdir()
            preview = server.restore_directory_snapshot(token, language=language)
            self.assertTrue(preview["dry_run"])
            self.assertEqual((root / "c.txt").read_bytes(), b"changed")
            self.assertEqual(
                server.restore_directory_snapshot(
                    token, dry_run=False, language=language
                )["error"],
                "plan_changed",
            )
            restored = server.restore_directory_snapshot(
                token,
                dry_run=False,
                expected_plan_id=preview["plan_id"],
                language=language,
            )
            self.assertTrue(restored["ok"], restored)
            self.assertEqual(
                {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()},
                original,
            )
            self.assertTrue((root / "empty").is_dir())
            self.assertIn(
                token,
                [
                    item["snapshot_id"]
                    for item in server.list_directory_snapshots(language=language)[
                        "items"
                    ]
                ],
            )

    def test_snapshot_stale_plan_and_corruption_do_not_modify_live_files(self):
        root = self.root / "live"
        root.mkdir()
        path = root / "data.txt"
        path.write_bytes(b"original")
        snapshot = server.create_directory_snapshot(str(root))["snapshot_id"]
        path.write_bytes(b"changed")
        preview = server.restore_directory_snapshot(snapshot)
        path.write_bytes(b"changed again")
        self.assertEqual(
            server.restore_directory_snapshot(snapshot, False, preview["plan_id"])[
                "error"
            ],
            "plan_changed",
        )
        preview = server.restore_directory_snapshot(snapshot)
        (server.SNAPSHOTS_DIR / snapshot / "0").write_bytes(b"corrupt")
        result = server.restore_directory_snapshot(snapshot, False, preview["plan_id"])
        self.assertEqual(result["error"], "backup_corrupt")
        self.assertEqual(path.read_bytes(), b"changed again")

    def test_history_keeps_all_threaded_operations(self):
        sources = []
        for index in range(12):
            path = self.root / f"{index}.txt"
            path.write_bytes(str(index).encode())
            sources.append(path)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(
                pool.map(lambda p: server.rename_file(str(p), p.stem + ".py"), sources)
            )
        self.assertTrue(all(result["ok"] for result in results), results)
        self.assertEqual(server.view_rename_history()["count"], 12)

    def test_backup_capacity_listing_cleanup_and_preview_guard(self):
        for language in ["en", "zh"]:
            path = self.root / (language + ".txt")
            path.write_bytes(b"keep")
            with patch.dict(os.environ, {"FILE_MCP_MAX_BACKUP_BYTES": "1"}):
                self.assertEqual(
                    server.delete_file(str(path), language=language)["error"],
                    "backup_limit",
                )
            self.assertTrue(path.exists())
            deletion = server.delete_file(str(path), language=language)
            records = server.list_deleted_files(str(self.root), language=language)
            self.assertIn(
                deletion["deletion_id"],
                [item["deletion_id"] for item in records["items"]],
            )
            preview = server.cleanup_backups(older_than_days=0, language=language)
            self.assertTrue(
                (server.DELETED_FILES_DIR / deletion["deletion_id"]).exists()
            )
            self.assertEqual(
                server.cleanup_backups(0, False, language=language)["error"],
                "plan_changed",
            )
            result = server.cleanup_backups(
                0, False, preview["plan_id"], language=language
            )
            self.assertTrue(result["ok"], result)
            self.assertFalse(
                (server.DELETED_FILES_DIR / deletion["deletion_id"]).exists()
            )

    def test_mcp_dispatch_for_new_tools_in_both_languages(self):
        async def check():
            for language, folder in [("en", "english"), ("zh", "中文")]:
                root = self.root / folder
                root.mkdir()

                async def call(name, _language=language, **kwargs):
                    result = await server.mcp.call_tool(
                        name, {**kwargs, "language": _language}
                    )
                    content = result[0] if isinstance(result, tuple) else result
                    return json.loads(content[0].text)

                result = await call(
                    "create_file",
                    filepath=str(root / "示例.ts"),
                    content="hello / 你好\r\n",
                )
                self.assertTrue(result["ok"], result)
                snapshot = await call("create_directory_snapshot", directory=str(root))
                self.assertTrue(snapshot["ok"], snapshot)
                deleted = await call("delete_file", filepath=str(root / "示例.ts"))
                self.assertTrue(deleted["ok"], deleted)
                self.assertTrue((await call("list_deleted_files"))["ok"])
                self.assertTrue(
                    (
                        await call(
                            "restore_deleted_file", deletion_id=deleted["deletion_id"]
                        )
                    )["ok"]
                )
                self.assertTrue((await call("list_directory_snapshots"))["ok"])
                self.assertTrue(
                    (
                        await call(
                            "restore_directory_snapshot",
                            snapshot_id=snapshot["snapshot_id"],
                        )
                    )["ok"]
                )
                self.assertTrue((await call("cleanup_backups"))["ok"])
                error = await call("restore_deleted_file", deletion_id="bad")
                self.assertIn(
                    "无效" if language == "zh" else "Invalid", error["message"]
                )

        asyncio.run(check())

    def test_batch_preview_conflicts_and_partial_failure(self):
        for language in ["en", "zh"]:
            directory = self.root / language
            directory.mkdir()
            for name in ["a.txt", "b.txt"]:
                (directory / name).write_bytes(b"keep")
            renames = [
                {"old_path": str(directory / name), "new_name": name + ".py"}
                for name in ["a.txt", "b.txt"]
            ]
            preview = server.batch_rename_files(renames, language=language)
            self.assertTrue(preview["ok"], preview)
            self.assertTrue((directory / "a.txt").exists())
            self.assertEqual(
                server.batch_rename_files(renames, dry_run=False, language=language)[
                    "error"
                ],
                "plan_changed",
            )
            call = server.rename_file

            def fail_second(old_path, new_name, _call=call, **kwargs):
                if Path(old_path).name == "b.txt":
                    return {"ok": False, "error": "injected_failure"}
                return _call(old_path, new_name, **kwargs)

            with patch.object(server, "rename_file", side_effect=fail_second):
                result = server.batch_rename_files(
                    renames,
                    dry_run=False,
                    expected_plan_id=preview["plan_id"],
                    language=language,
                )
            self.assertEqual(result["error"], "batch_partial")
            self.assertEqual(len(result["completed"]), 1)
            self.assertTrue((directory / "a.txt.py").exists())
            self.assertTrue((directory / "b.txt").exists())
            conflict = [{"old_path": str(directory / "b.txt"), "new_name": "a.txt.py"}]
            self.assertEqual(
                server.batch_rename_files(conflict, language=language)["error"],
                "rename_conflict",
            )

    def test_history_keeps_operations_from_multiple_processes(self):
        script = """
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('worker', sys.argv[1])
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
s.HISTORY_FILE = Path(sys.argv[2])
result = s.rename_file(sys.argv[3], sys.argv[4])
assert result['ok'], result
"""
        processes = []
        for index in range(4):
            path = self.root / f"process-{index}.txt"
            path.write_bytes(b"keep")
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        script,
                        server.__file__,
                        str(server.HISTORY_FILE),
                        str(path),
                        path.stem + ".py",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            )
        for process in processes:
            output, error = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, (output, error))
        self.assertEqual(server.view_rename_history()["count"], 4)

    def test_snapshot_handles_file_directory_type_changes(self):
        for language in ["en", "zh"]:
            directory = self.root / language
            directory.mkdir()
            (directory / "was-file").write_bytes(b"file")
            (directory / "was-dir").mkdir()
            (directory / "was-dir" / "child").write_bytes(b"child")
            token = server.create_directory_snapshot(str(directory), language=language)[
                "snapshot_id"
            ]
            (directory / "was-file").unlink()
            (directory / "was-file").mkdir()
            (directory / "was-file" / "extra").write_bytes(b"extra")
            (directory / "was-dir" / "child").unlink()
            (directory / "was-dir").rmdir()
            (directory / "was-dir").write_bytes(b"replacement")
            preview = server.restore_directory_snapshot(token, language=language)
            restored = server.restore_directory_snapshot(
                token, False, preview["plan_id"], language=language
            )
            self.assertTrue(restored["ok"], restored)
            self.assertEqual((directory / "was-file").read_bytes(), b"file")
            self.assertEqual((directory / "was-dir" / "child").read_bytes(), b"child")

    def test_incomplete_deletion_records_are_not_purged(self):
        path = self.root / "preserved"
        path.write_bytes(b"keep")
        with patch.object(server.shutil, "copy2", side_effect=OSError("disk full")):
            failed = server.delete_file(str(path))
        self.assertFalse(failed["ok"])
        preview = server.cleanup_backups(0)
        self.assertNotIn(
            failed["deletion_id"], [item["record_id"] for item in preview["items"]]
        )
        self.assertEqual(path.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
