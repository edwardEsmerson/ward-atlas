"""Attachment safety expectations; defined before the launcher implementation."""
import importlib
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class ExistingServerTests(unittest.TestCase):
    def setUp(self):
        self.attach = importlib.import_module("wardatlas_attach")
        self.state = ROOT / ".local" / "unit-attachment-state"

    def test_expected_groups(self):
        expected = json.loads((ROOT / "contracts/existing-server/expected-results.json").read_text())
        matrix = json.loads((ROOT / "contracts/existing-server/adversarial-matrix.json").read_text())
        self.assertGreaterEqual(len(expected["groups"]), 8)
        self.assertEqual([g["id"] for g in expected["groups"]], [g["id"] for g in matrix["groups"]])
        self.assertTrue(all(g["checks"] for g in matrix["groups"]))

    def test_default_ephemeral_app_port(self):
        args = self.attach.parser().parse_args(["--db-host", "localhost", "--db-port", "6379"])
        self.assertEqual(args.app_port, 0)

    def test_explicit_ports_preserved(self):
        args = self.attach.parser().parse_args(["--db-host", "localhost", "--db-port", "18732", "--app-port", "18733"])
        self.assertEqual((args.db_port, args.app_port), (18732, 18733))

    def test_db_host_required(self):
        with self.assertRaises(SystemExit):
            self.attach.parser().parse_args(["--db-port", "6379"])

    def test_db_port_required(self):
        with self.assertRaises(SystemExit):
            self.attach.parser().parse_args(["--db-host", "localhost"])

    def test_port_validation(self):
        for value in (-1, 65536):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.attach.validate_app_port(value)
        for value in (0, 18733, 65535):
            self.assertEqual(self.attach.validate_app_port(value), value)

    def test_unique_resources(self):
        first = self.attach.Attachment("localhost", 18732)
        second = self.attach.Attachment("localhost", 18732)
        self.assertNotEqual(first.graph, second.graph)
        self.assertNotEqual(first.state, second.state)
        self.assertTrue(first.graph.startswith("wardatlas_attach_"))
        self.assertTrue(first.state.is_relative_to(ROOT))

    def test_legacy_constructor(self):
        from wardatlas_app import WardAtlasService
        service = WardAtlasService(18732, "wardatlas_unit_legacy", self.state)
        self.assertEqual(service.db_host, "127.0.0.1")

    def test_explicit_constructor_host(self):
        from wardatlas_app import WardAtlasService
        service = WardAtlasService(18732, "wardatlas_unit_host", self.state, db_host="localhost")
        with patch("wardatlas_app.RespConnection") as connect:
            service._connection()
        connect.assert_called_once_with("localhost", 18732, timeout=3.0)

    def test_invalid_host(self):
        from wardatlas_app import WardAtlasService
        for host in ("", "http://localhost", "bad host", "a\nb"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                WardAtlasService(18732, "wardatlas_unit_host", self.state, db_host=host)

    def test_state_escape_rejected(self):
        from wardatlas_app import WardAtlasService
        with self.assertRaises(ValueError):
            WardAtlasService(18732, "wardatlas_unit_path", Path("/tmp/outside-wardatlas"))

    def test_no_graph_cleanup_without_ownership(self):
        item = self.attach.Attachment("localhost", 18732)
        with patch.object(item.service, "_connection") as connect:
            item.cleanup()
        connect.assert_not_called()

    def test_module_required_before_any_graph_command(self):
        item = self.attach.Attachment("localhost", 18732)
        connection = Mock()
        connection.command.return_value = []
        with patch.object(item.service, "_connection", return_value=connection):
            with self.assertRaises(RuntimeError):
                item.prepare()
        self.assertEqual(connection.command.call_args_list[0].args, ("MODULE", "LIST"))
        self.assertFalse(any(str(c.args[0]).startswith("GRAPH") for c in connection.command.call_args_list))
        self.assertFalse(item.graph_owned)
        self.assertFalse(item.state.exists())

    def test_existing_graph_refused_without_deletion(self):
        item = self.attach.Attachment("localhost", 18732)
        connection = Mock()
        connection.command.side_effect = [[[b"name", b"graph", b"ver", 60002]], 1]
        with patch.object(item.service, "_connection", return_value=connection):
            with self.assertRaises(RuntimeError):
                item.prepare()
            item.cleanup()
        self.assertFalse(item.graph_owned)
        self.assertFalse(any(c.args[0] == "GRAPH.DELETE" for c in connection.command.call_args_list))

    def test_collision_never_connects_db(self):
        with socket.socket() as occupant:
            occupant.bind(("127.0.0.1", 0))
            occupant.listen()
            port = occupant.getsockname()[1]
            with patch("wardatlas_app.RespConnection") as connection:
                code = self.attach.main(["--db-host", "localhost", "--db-port", "18732", "--app-port", str(port)])
            self.assertEqual(code, 2)
            connection.assert_not_called()
            occupant.settimeout(0.05)
            with self.assertRaises(socket.timeout):
                occupant.accept()

    def test_partial_seed_failure_owns_graph_for_cleanup(self):
        item = self.attach.Attachment("localhost", 18732)
        connection = Mock()
        connection.command.side_effect = [[[b"name", b"graph", b"ver", 60002]], 0, []]
        with patch.object(item.service, "_connection", return_value=connection), patch.object(item.service, "reset_scenario", side_effect=RuntimeError("partial seed")):
            with self.assertRaises(RuntimeError):
                item.prepare()
        self.assertTrue(item.graph_owned)
        connection.command.side_effect = None
        with patch.object(item.service, "_connection", return_value=connection):
            item.cleanup()
        connection.command.assert_called_with("GRAPH.DELETE", item.graph)
        self.assertFalse(item.state.exists())

    def test_legacy_cli_host_plumbing(self):
        import wardatlas_app
        with patch.object(sys, "argv", ["wardatlas_app.py", "--app-port", "18733", "--db-host", "localhost", "--db-port", "18732", "--graph-name", "wardatlas_cli_unit"]), patch.object(wardatlas_app, "WardAtlasService") as service, patch.object(wardatlas_app, "WardAtlasHTTPServer", side_effect=OSError("occupied")):
            self.assertEqual(wardatlas_app.main(), 2)
        self.assertEqual(service.call_args.kwargs["db_host"], "localhost")


if __name__ == "__main__":
    unittest.main()
