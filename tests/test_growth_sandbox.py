"""Real sandbox probes, with explicit skips when kernel isolation is absent."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

_SPEC = importlib.util.spec_from_file_location(
    "growth_sandbox_test", Path(__file__).resolve().parents[1]
    / "plugins/astrbot_plugin_kouhu_oj/growth_sandbox.py")
sandbox = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sandbox)


class SandboxContractTests(unittest.TestCase):
    def test_prefix_is_absolute_shell_free_and_preserves_arguments(self):
        with tempfile.TemporaryDirectory(prefix="sandbox work ") as directory:
            prefix = sandbox.command_prefix(Path(directory))
            self.assertEqual(prefix, [sys.executable, str(Path(sandbox.__file__).resolve()),
                                      "--run", str(Path(directory).resolve()), "--"])
            self.assertEqual(prefix, sandbox.sandbox_prefix(Path(directory)))

    def test_missing_isolation_fails_closed_before_exec(self):
        # Run in a separate interpreter: _run changes cwd, environment, FDs
        # and process privileges before either mandatory protection is loaded.
        script = (
            "import importlib.util,pathlib,sys\n"
            "from unittest.mock import patch\n"
            "spec=importlib.util.spec_from_file_location('helper',sys.argv[1])\n"
            "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)\n"
            "with patch.object(m,'_landlock') as landlock, patch.object(m,'_seccomp') as seccomp, "
            "patch.object(m.os,'execvpe') as execute:\n"
            " target=landlock if sys.argv[3]=='_landlock' else seccomp\n"
            " target.side_effect=m.SandboxUnavailable('unsupported')\n"
            " assert m._main(['helper','--run',sys.argv[2],'--','/usr/bin/true'])==125\n"
            " execute.assert_not_called()\n")
        with tempfile.TemporaryDirectory() as directory:
            for stage in ("_landlock", "_seccomp"):
                with self.subTest(stage=stage):
                    result = subprocess.run([sys.executable, "-c", script,
                                             sandbox.__file__, directory, stage],
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_bad_cli_fails_closed(self):
        with patch("sys.stderr"):
            for argv in (["helper"], ["helper", "--run", "/tmp", "--"],
                         ["helper", "--bad", "/tmp", "--", "true"]):
                self.assertEqual(sandbox._main(argv), 125)

    def test_old_landlock_abi_rejected_before_creating_ruleset(self):
        from unittest.mock import Mock
        libc = Mock()
        libc.syscall.return_value = 2
        with patch.object(sandbox.platform, "system", return_value="Linux"), \
                patch.object(sandbox.platform, "machine", return_value="x86_64"):
            with self.assertRaisesRegex(sandbox.SandboxUnavailable, "ABI 3"):
                sandbox._landlock(libc, Path("/tmp"))
        libc.syscall.assert_called_once_with(444, 0, 0, 1)

    def test_missing_seccomp_library_rejected(self):
        with patch.object(sandbox.ctypes, "CDLL", side_effect=OSError("missing")):
            with self.assertRaises(sandbox.SandboxUnavailable):
                sandbox._seccomp()


class LocalSandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.python = shutil.which("python3", path="/usr/bin:/bin")
        if not cls.python or not Path("/usr/bin/true").exists():
            raise unittest.SkipTest("system Python and /usr/bin/true required")
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(sandbox.sandbox_prefix(Path(directory)) + ["/usr/bin/true"],
                                    capture_output=True, text=True, timeout=10)
        if result.returncode == 125:
            raise unittest.SkipTest("mandatory sandbox unavailable: " + result.stderr.strip())
        if result.returncode != 0:
            raise AssertionError(f"sandbox probe failed: {result.returncode}: {result.stderr}")

    def setUp(self):
        self.root = tempfile.TemporaryDirectory(prefix="kouhu-sandbox-test-")
        self.addCleanup(self.root.cleanup)
        self.root_path = Path(self.root.name)
        self.work = self.root_path / "work"
        self.work.mkdir()
        self.secret = self.root_path / "host-secret"
        self.secret.write_text("host-only-secret", encoding="utf-8")

    def run_python(self, code, *arguments, **kwargs):
        return subprocess.run(sandbox.sandbox_prefix(self.work)
                              + [self.python, "-c", code, *map(str, arguments)],
                              capture_output=True, text=True, timeout=15, **kwargs)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_true_and_child_environment(self):
        # Use env directly: Python can itself add LC_CTYPE during startup.
        result = subprocess.run(sandbox.sandbox_prefix(self.work) + ["/usr/bin/env"],
                                capture_output=True, text=True, timeout=10,
                                env={"HOST_SECRET": "do-not-inherit", "PATH": "/usr/bin:/bin"})
        self.assert_success(result)
        self.assertEqual(dict(line.split("=", 1) for line in result.stdout.splitlines()),
                         {"PATH": "/usr/bin:/bin", "LANG": "C",
                          "TMPDIR": str(self.work), "HOME": str(self.work)})
        self.assert_success(self.run_python("import os,sys; assert os.getcwd()==sys.argv[1]",
                                            self.work))

    def test_secret_proc_and_symlink_escape_denied(self):
        (self.work / "secret-link").symlink_to(self.secret)
        result = self.run_python(
            "import errno,sys\n"
            "for path in sys.argv[1:]:\n"
            " try:\n"
            "  open(path,'rb').read()\n"
            " except OSError as exc:\n"
            "  assert exc.errno in (errno.EACCES,errno.EPERM), (path,exc)\n"
            " else: raise AssertionError('read escaped: '+path)\n",
            self.secret, self.work / "secret-link", "/proc/self/maps", "/proc/self/environ")
        self.assert_success(result)

    def test_proc_fd_cannot_reopen_forbidden_regular_stdio(self):
        with self.secret.open("rb") as secret:
            result = self.run_python(
                "import errno\n"
                "try: open('/proc/self/fd/0','rb').read()\n"
                "except OSError as exc: assert exc.errno in (errno.EACCES,errno.EPERM)\n"
                "else: raise AssertionError('proc FD bypassed Landlock')\n", stdin=secret)
        self.assert_success(result)

    def test_metadata_changes_outside_workdir_are_denied(self):
        before = self.secret.stat()
        result = self.run_python(
            "import errno,os,sys\n"
            "for operation in (lambda:os.chmod(sys.argv[1],0o777),"
            "lambda:os.utime(sys.argv[1],(0,0))):\n"
            " try: operation()\n"
            " except OSError as exc: assert exc.errno==errno.EPERM, exc\n"
            " else: raise AssertionError('host metadata changed')\n", self.secret)
        self.assert_success(result)
        after = self.secret.stat()
        self.assertEqual(before.st_mode, after.st_mode)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)

    def test_write_truncate_and_rename_escape_denied(self):
        outside = self.root_path / "escaped"
        result = self.run_python(
            "import errno,os,sys\n"
            "for path in sys.argv[1:]:\n"
            " try:\n"
            "  fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)\n"
            " except OSError as exc:\n"
            "  assert exc.errno in (errno.EACCES,errno.EPERM), (path,exc)\n"
            " else:\n"
            "  os.close(fd); raise AssertionError('write escaped: '+path)\n"
            "open('allowed','w').write('ok')\n"
            "try: os.rename('allowed',sys.argv[1])\n"
            "except OSError: pass\n"
            "else: raise AssertionError('rename escaped')\n", outside, self.secret)
        self.assert_success(result)
        self.assertFalse(outside.exists())
        self.assertEqual(self.secret.read_text(), "host-only-secret")
        self.assertEqual((self.work / "allowed").read_text(), "ok")

    def test_network_and_dangerous_syscalls_forbidden(self):
        result = self.run_python(
            "import ctypes,errno,os,socket\n"
            "for family in (socket.AF_INET,socket.AF_INET6,socket.AF_UNIX):\n"
            " try: socket.socket(family,socket.SOCK_STREAM)\n"
            " except OSError as exc: assert exc.errno==errno.EPERM, exc\n"
            " else: raise AssertionError('socket allowed')\n"
            "try: socket.socketpair()\n"
            "except OSError as exc: assert exc.errno==errno.EPERM, exc\n"
            "else: raise AssertionError('socketpair allowed')\n"
            "libc=ctypes.CDLL(None,use_errno=True)\n"
            "assert libc.ptrace(0,0,0,0)==-1 and ctypes.get_errno()==errno.EPERM\n"
            "try: os.kill(os.getpid(),0)\n"
            "except OSError as exc: assert exc.errno==errno.EPERM, exc\n"
            "else: raise AssertionError('kill allowed')\n")
        self.assert_success(result)

    def test_inherited_descriptors_are_closed(self):
        with self.secret.open("rb") as secret:
            result = self.run_python(
                "import os,sys\n"
                "try: os.read(int(sys.argv[1]),100)\n"
                "except OSError: pass\n"
                "else: raise AssertionError('inherited host FD usable')\n",
                secret.fileno(), pass_fds=(secret.fileno(),))
        self.assert_success(result)

    def test_gpp_compiles_and_runs_with_inherited_restrictions(self):
        compiler = shutil.which("g++", path="/usr/bin:/bin")
        if not compiler:
            self.skipTest("g++ unavailable")
        source = self.work / "main.cpp"
        source.write_text(
            '#include <fstream>\n#include <iostream>\n#include <sys/socket.h>\n'
            '#include <cerrno>\nint main(int argc,char**argv){'
            'std::ifstream secret(argv[1]); if(secret)return 10;'
            'errno=0; if(socket(AF_INET,SOCK_STREAM,0)!=-1 || errno!=EPERM)return 11;'
            'std::ofstream local("result");local<<"ok";std::cout<<"sandbox-ok\\n";}\n')
        binary = self.work / "program"
        # Metadata syscalls are denied globally because Landlock cannot scope
        # them. The trusted caller supplies the executable mode before linking.
        binary.touch(mode=0o700)
        result = subprocess.run(sandbox.sandbox_prefix(self.work)
                                + [compiler, "-std=c++17", str(source), "-o", str(binary)],
                                capture_output=True, text=True, timeout=30)
        self.assert_success(result)
        result = subprocess.run(sandbox.sandbox_prefix(self.work) + [str(binary), str(self.secret)],
                                capture_output=True, text=True, timeout=10)
        self.assert_success(result)
        self.assertEqual(result.stdout, "sandbox-ok\n")
        self.assertEqual((self.work / "result").read_text(), "ok")


if __name__ == "__main__":
    unittest.main()
