"""Compiler-independent contracts for sandboxed candidate validation."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

_SPEC = importlib.util.spec_from_file_location(
    "growth_validation_test", Path(__file__).resolve().parents[1]
    / "plugins/astrbot_plugin_kouhu_oj/growth_validation.py")
v = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(v)


def candidate():
    return {"id": "increment", "category": "integers", "level": 1,
            "title": "Initial value", "question": "Why is the value incorrect?",
            "buggy_code": "int x = 1;", "answer": "Use the correct initial value.",
            "fix": "int x = 2;", "cpp_standard": "c++17",
            "validation": {"buggy_program": '#include <iostream>\nint main(){int x=1;std::cout<<x;}',
                           "fixed_program": '#include <iostream>\nint main(){int x=2;std::cout<<x;}',
                           "stdin": "", "expected_stdout": "2\n",
                           "buggy_kind": "wrong_output"}}


def completed(code=0, stdout="", stderr=""):
    return {"returncode": code, "stdout": stdout, "stderr": stderr, "output_limit": False}


class GrowthValidationTests(unittest.TestCase):
    def run_stages(self, stages, item=None):
        with patch.object(v.shutil, "which", side_effect=lambda name: "/usr/bin/" + name), \
                patch.object(v, "_execute", side_effect=stages) as execute:
            result = v.validate_candidate(item or candidate())
        return result, execute

    def test_wrong_output_counterexample_and_scope(self):
        result, execute = self.run_stages([
            completed(), completed(), completed(stdout="2\n"),
            completed(), completed(stdout="1\n")])
        self.assertTrue(result["ok"])
        self.assertEqual(result["evidence"]["scope"], "reproducible_counterexample_only")
        self.assertFalse(result["evidence"]["semantic_proof"])
        self.assertEqual([c.kwargs["timeout"] for c in execute.call_args_list], [15, 15, 2, 15, 2])
        fixed_compile = execute.call_args_list[1].args[2]
        self.assertEqual(fixed_compile[1:], ["-std=c++17", "-O0", "/work/program.cpp", "-o", "/work/fixed"])
        self.assertNotEqual(execute.call_args_list[1].args[1], execute.call_args_list[3].args[1])

    def test_compile_error_requires_real_diagnostic_and_fixed_success(self):
        item = candidate()
        item["validation"]["buggy_kind"] = "compile_error"
        for outcome, accepted in ((completed(1, stderr="program.cpp:1: error: invalid"), True),
                                  (completed(), False), (completed(-9), False),
                                  (completed(1, stderr="bwrap: error: denied"), False),
                                  (completed(1, stderr="permission denied"), False),
                                  ({"error": "timeout"}, False)):
            with self.subTest(outcome=outcome):
                result, execute = self.run_stages([
                    completed(), completed(), completed(stdout="2"), outcome], item)
                self.assertEqual(result["ok"], accepted)
                self.assertEqual(execute.call_count, 4)
        result, execute = self.run_stages([completed(), completed(1)], item)
        self.assertFalse(result["ok"])
        self.assertEqual(execute.call_count, 2)

    def test_crash_timeout_or_same_buggy_output_is_not_wrong_output(self):
        for outcome in (completed(-11), completed(1, stdout="1"), {"error": "timeout"},
                        completed(stdout="2\n"), dict(completed(stdout="1"), output_limit=True)):
            with self.subTest(outcome=outcome):
                result, _ = self.run_stages([
                    completed(), completed(), completed(stdout="2"), completed(), outcome])
                self.assertFalse(result["ok"])

    def test_stdout_normalization_only_trailing_newlines(self):
        for output, accepted in (("2\n\n", True), (" 2", False), ("2 ", False),
                                 ("2\r\n", False), ("2\nextra", False)):
            stages = [completed(), completed(), completed(stdout=output)]
            if accepted:
                stages += [completed(), completed(stdout="1")]
            with self.subTest(output=output):
                result, _ = self.run_stages(stages)
                self.assertEqual(result["ok"], accepted)

    def test_snippet_mismatch_rejected_before_tools(self):
        for field in ("buggy_code", "fix"):
            item = candidate()
            item[field] = "int other = 999;"
            with patch.object(v.shutil, "which") as tools:
                result = v.validate_candidate(item)
            self.assertFalse(result["ok"])
            self.assertIn("snippet mismatch", result["reason"])
            tools.assert_not_called()
        self.assertTrue(v._contains("int  x=1; // comment", "int x = 1;"))
        self.assertFalse(v._contains('puts("ab");', 'puts("a b");'))
        self.assertFalse(v._contains("int foobar;", "int foo bar;"))
        self.assertFalse(v._contains("x++ + y;", "x + ++y;"))
        self.assertFalse(v._contains('auto s=R"(a b)";', 'auto s=R"(ab)";'))
        self.assertFalse(v._contains("// int x = 1;\nint main(){}", "int x=1;"))

    def test_schema_and_size_limits(self):
        invalid = [None, {}, dict(candidate(), cpp_standard="c++17;id"),
                   dict(candidate(), level=True), dict(candidate(), answer=""),
                   dict(candidate(), buggy_code="x" * 12001)]
        for name, value in (("stdin", "x" * 10001), ("fixed_program", "x" * 12001),
                            ("buggy_kind", "crash"), ("expected_stdout", "x" * (v.MAX_OUTPUT + 1))):
            item = deepcopy(candidate())
            item["validation"][name] = value
            invalid.append(item)
        with patch.object(v.shutil, "which") as tools:
            for item in invalid:
                with self.subTest(item_type=type(item).__name__):
                    self.assertFalse(v.validate_candidate(item)["ok"])
        tools.assert_not_called()

    def test_missing_tools_fail_closed_without_launch(self):
        for missing in ("bwrap", "g++"):
            with patch.object(v.shutil, "which", side_effect=lambda name: None if name == missing else "/usr/bin/" + name), \
                    patch.object(v.subprocess, "Popen") as run:
                self.assertFalse(v.validate_candidate(candidate())["ok"])
                run.assert_not_called()

    def test_isolation_denied_has_no_fallback(self):
        for failure in (completed(1, stderr="bwrap: creating namespace failed"),
                        {"error": "sandbox_launch_failed"}, {"error": "timeout"}):
            result, execute = self.run_stages([failure,failure])
            self.assertFalse(result["ok"])
            self.assertEqual(execute.call_count, 2)
            self.assertEqual(execute.call_args.args[0],'landlock')

    def test_subprocess_security_contract_and_file_capture(self):
        def fake_run(args, **kwargs):
            self.assertEqual(args[0], "/usr/bin/bwrap")
            for flag in ("--unshare-all", "--die-with-parent", "--new-session", "--clearenv"):
                self.assertIn(flag, args)
            self.assertEqual(kwargs["env"], {"PATH": "/usr/bin:/bin", "LANG": "C"})
            self.assertIs(kwargs["preexec_fn"], v._limits)
            self.assertFalse(kwargs["shell"])
            for stream in ("stdin", "stdout", "stderr"):
                self.assertGreaterEqual(kwargs[stream].fileno(), 0)
            self.assertNotIn("capture_output", kwargs)
            self.assertNotIn("input", kwargs)
            kwargs["stdout"].write(b"bounded output")
            kwargs["stderr"].write(b"diagnostic")
            return SimpleNamespace(returncode=0,pid=999999999,wait=lambda **kw:0)
        with patch.object(v.subprocess, "Popen", side_effect=fake_run):
            result = v._execute("/usr/bin/bwrap", Path("/tmp/work"), ["/work/fixed"], timeout=2)
        self.assertEqual(result["stdout"], "bounded output")
        args = v._sandbox("/usr/bin/bwrap", Path("/tmp/work"), ["/work/fixed"])
        self.assertEqual(args.count("--bind"), 1)
        self.assertEqual(args[args.index("--bind") + 1:args.index("--bind") + 3], ["/tmp/work", "/work"])
        self.assertIn("--proc", args)
        self.assertIn("--dev", args)
        self.assertIn("--tmpfs", args)
        mounts = [args[i + 1] for i, value in enumerate(args) if value == "--ro-bind"]
        self.assertTrue(set(mounts) <= {"/usr", "/lib", "/lib64"})
        self.assertIn("/usr", mounts)

    def test_invalid_utf8_and_output_limit_fail_closed(self):
        for payload, error in ((b"\xff", "invalid_stdout_encoding"),
                               (b"x" * v.MAX_OUTPUT, None)):
            def fake_run(args, **kwargs):
                kwargs["stdout"].write(payload)
                return SimpleNamespace(returncode=0,pid=999999999,wait=lambda **kw:0)
            with patch.object(v.subprocess, "Popen", side_effect=fake_run):
                result = v._execute("/usr/bin/bwrap", Path("/tmp/work"), ["/work/fixed"], timeout=2)
            if error:
                self.assertEqual(result["error"], error)
            else:
                self.assertTrue(result["output_limit"])

    def test_limits_include_all_required_resources(self):
        with patch.object(v.resource, "setrlimit") as limits:
            v._limits()
        values = dict(c.args for c in limits.call_args_list)
        self.assertEqual(values[v.resource.RLIMIT_FSIZE], (v.MAX_OUTPUT, v.MAX_OUTPUT))
        self.assertEqual(values[v.resource.RLIMIT_CORE], (0, 0))
        for kind in (v.resource.RLIMIT_CPU, v.resource.RLIMIT_AS, v.resource.RLIMIT_NPROC):
            self.assertGreater(values[kind][0], 0)
            self.assertEqual(values[kind][0], values[kind][1])

    def test_subprocess_timeout_or_launch_error_returns_failure(self):
        for exception in (OSError("denied"), subprocess.TimeoutExpired("bwrap", 2),
                          subprocess.SubprocessError("preexec failed")):
            with patch.object(v.subprocess, "Popen", side_effect=exception):
                result = v._execute("/usr/bin/bwrap", Path("/tmp/work"), ["/work/fixed"], timeout=2)
            self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
