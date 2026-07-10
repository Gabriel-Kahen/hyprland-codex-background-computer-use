import json
import os
import subprocess
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]


class RepositorySmokeTests(TestCase):
    def test_plugin_metadata_references_an_executable_launcher(self) -> None:
        manifest = json.loads((ROOT / ".codex-plugin/plugin.json").read_text())
        mcp = json.loads((ROOT / manifest["mcpServers"]).read_text())
        command = mcp["mcpServers"]["same-session-computer-use"]["command"]
        launcher = ROOT / command

        self.assertTrue(launcher.is_file())
        self.assertTrue(os.access(launcher, os.X_OK))

    def test_mcp_initialize_tools_and_ping(self) -> None:
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        ]
        proc = subprocess.run(
            [str(ROOT / "bin/same-session-computer-use-mcp")],
            input="".join(json.dumps(request) + "\n" for request in requests),
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        )
        responses = {response["id"]: response for response in map(json.loads, proc.stdout.splitlines())}

        self.assertEqual(responses[1]["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(responses[3]["result"], {})
        tools = responses[2]["result"]["tools"]
        names = [tool["name"] for tool in tools]
        self.assertEqual(len(names), 11)
        self.assertEqual(len(names), len(set(names)))
        for tool in tools:
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertLessEqual(set(schema.get("required", [])), set(schema.get("properties", {})))
            self.assertIn("annotations", tool)
