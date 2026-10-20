"""Real Streamable HTTP checks with an isolated server. Run: python -B test_file_http.py."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


class HttpTests(unittest.TestCase):
    def test_bilingual_file_lifecycle_over_http(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            environment = {
                **os.environ,
                "FILE_MCP_HOST": "127.0.0.1",
                "FILE_MCP_PORT": str(port),
                "FILE_MCP_STATE_DIR": str(root / "state"),
                "FILE_MCP_ENABLE_DNS_REBINDING_PROTECTION": "false",
            }
            with (root / "server.log").open("w+b") as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-B",
                        str(Path(__file__).with_name("w-file_workbench_mcp_server.py")),
                    ],
                    env=environment,
                    stdout=log,
                    stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                try:
                    asyncio.run(self.check_server(root, port, process))
                except Exception:
                    log.flush()
                    log.seek(0)
                    print(log.read().decode("utf-8", errors="replace"))
                    raise
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

    async def check_server(self, root, port, process):
        url = f"http://127.0.0.1:{port}/mcp"
        async with httpx.AsyncClient() as client:
            for _ in range(150):
                if process.poll() is not None:
                    self.fail("Isolated server exited during startup")
                try:
                    await client.get(url, timeout=0.2)
                    break
                except httpx.TransportError:
                    await asyncio.sleep(0.05)
            else:
                self.fail("Isolated server startup timed out")
        async with streamable_http_client(url) as (read, write, _):  # noqa: SIM117
            async with ClientSession(read, write) as session:
                await session.initialize()
                definitions = await session.list_tools()
                names = {tool.name for tool in definitions.tools}
                self.assertTrue(
                    {
                        "batch_rename_files",
                        "list_deleted_files",
                        "cleanup_backups",
                        "create_directory_snapshot",
                        "restore_directory_snapshot",
                    }
                    <= names
                )
                for language, folder in [("en", "examples"), ("zh", "示例")]:
                    directory = root / folder
                    directory.mkdir()

                    async def call(name, _language=language, **arguments):
                        response = await session.call_tool(
                            name, {**arguments, "language": _language}
                        )
                        self.assertFalse(response.isError, response)
                        return json.loads(response.content[0].text)

                    original = "Hello / 你好\r\n"
                    path = directory / (
                        "example.txt" if language == "en" else "示例.txt"
                    )
                    self.assertTrue(
                        (
                            await call(
                                "create_file", filepath=str(path), content=original
                            )
                        )["ok"]
                    )
                    self.assertEqual(
                        (await call("read_file", filepath=str(path)))["content"],
                        original,
                    )
                    snapshot = await call(
                        "create_directory_snapshot", directory=str(directory)
                    )
                    self.assertTrue(snapshot["ok"], snapshot)
                    renamed = directory / (
                        "example.ts" if language == "en" else "示例.ts"
                    )
                    renames = [{"old_path": str(path), "new_name": renamed.name}]
                    preview = await call("batch_rename_files", renames=renames)
                    result = await call(
                        "batch_rename_files",
                        renames=renames,
                        dry_run=False,
                        expected_plan_id=preview["plan_id"],
                    )
                    self.assertTrue(result["ok"], result)
                    deleted = await call("delete_file", filepath=str(renamed))
                    self.assertTrue(deleted["ok"], deleted)
                    self.assertTrue(
                        (await call("list_deleted_files", filter_dir=str(directory)))[
                            "ok"
                        ]
                    )
                    self.assertTrue(
                        (
                            await call(
                                "restore_deleted_file",
                                deletion_id=deleted["deletion_id"],
                            )
                        )["ok"]
                    )
                    preview = await call(
                        "restore_directory_snapshot",
                        snapshot_id=snapshot["snapshot_id"],
                    )
                    restored = await call(
                        "restore_directory_snapshot",
                        snapshot_id=snapshot["snapshot_id"],
                        dry_run=False,
                        expected_plan_id=preview["plan_id"],
                    )
                    self.assertTrue(restored["ok"], restored)
                    self.assertEqual(path.read_bytes(), original.encode("utf-8"))
                    self.assertEqual([p.name for p in directory.iterdir()], [path.name])
                    self.assertTrue((await call("cleanup_backups"))["dry_run"])
                    error = await call("restore_deleted_file", deletion_id="invalid")
                    self.assertEqual(error["error"], "invalid_deletion_id")
                    self.assertIn(
                        "无效" if language == "zh" else "Invalid", error["message"]
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
