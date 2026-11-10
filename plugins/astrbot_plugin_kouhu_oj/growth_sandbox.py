"""Fail-closed Linux sandbox for containers without usable user namespaces.

The caller supplies resource limits, timeouts and bounded stdio. This helper
restricts filesystem access with Landlock and syscalls with libseccomp before
exec; both restrictions are inherited by compiler subprocesses. It creates no
namespaces and requires no extra capabilities or relaxed Docker settings.

Landlock cannot restrict metadata-changing syscalls by path, so seccomp denies
those globally. Before compiling, the trusted caller must create the output
binary with mode 0700 (or chmod it after compilation). Never share the writable
workdir with host secrets or other untrusted executions. Anonymous stdio pipes
remain usable; regular-file stdio should be caller-owned bounded capture files.
"""
from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import platform
import resource
import sys


class SandboxUnavailable(RuntimeError):
    """A mandatory isolation mechanism could not be installed."""


def sandbox_prefix(workdir: Path) -> list[str]:
    """Return a shell-free prefix; append argv using real workdir paths."""
    return [sys.executable, str(Path(__file__).resolve()), "--run",
            str(workdir.resolve(strict=True)), "--"]


# The validator may use either spelling.
command_prefix = sandbox_prefix


class _Ruleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathRule(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int)]


def _checked(result: int, operation: str) -> int:
    if result < 0:
        code = ctypes.get_errno()
        raise SandboxUnavailable(f"{operation}: {os.strerror(code)}")
    return result


def _landlock(libc: ctypes.CDLL, workdir: Path) -> None:
    # These syscall numbers are shared by the supported Linux architectures.
    # Unknown architectures fail closed rather than risking a different syscall.
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "aarch64"):
        raise SandboxUnavailable("unsupported Linux syscall architecture")
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    abi = _checked(syscall(444, 0, 0, 1), "Landlock ABI probe")
    # ABI 2 adds REFER; ABI 3 adds TRUNCATE. Without the latter, an existing
    # forbidden file could be truncated without WRITE_FILE permission.
    if abi < 3:
        raise SandboxUnavailable("Landlock ABI 3 or newer is required")
    handled = (1 << 15) - 1  # EXECUTE through TRUNCATE, including all MAKE_*.
    if abi >= 5:
        handled |= 1 << 15  # IOCTL_DEV: deny device ioctls as well.
    ruleset = _Ruleset(handled)
    ruleset_fd = _checked(syscall(444, ctypes.byref(ruleset),
                                  ctypes.sizeof(ruleset), 0), "Landlock ruleset")
    read_exec = (1 << 0) | (1 << 2) | (1 << 3)

    def allow(path: str, access: int, *, optional: bool = False) -> None:
        try:
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except FileNotFoundError:
            if optional:
                return
            raise
        try:
            # READ_DIR is valid only for directories; /dev/null and ld.so.cache
            # are deliberately limited to file access rights.
            rule = _PathRule(access, fd)
            _checked(syscall(445, ruleset_fd, 1, ctypes.byref(rule), 0),
                     f"Landlock allow {path}")
        finally:
            os.close(fd)

    try:
        for path in ("/usr", "/lib", "/lib64", "/bin"):
            allow(path, read_exec, optional=True)
        allow("/etc/ld.so.cache", 1 << 2, optional=True)
        allow("/etc/alternatives", read_exec, optional=True)
        allow("/dev/null", (1 << 1) | (1 << 2))
        allow(str(workdir), handled)
        _checked(syscall(446, ruleset_fd, 0), "Landlock restrict_self")
    finally:
        os.close(ruleset_fd)


def _seccomp() -> None:
    try:
        lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    except OSError as exc:
        raise SandboxUnavailable("libseccomp.so.2 unavailable") from exc
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                    ctypes.c_int, ctypes.c_uint]
    lib.seccomp_rule_add.restype = ctypes.c_int
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_load.restype = ctypes.c_int
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.restype = None
    context = lib.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise SandboxUnavailable("seccomp_init failed")
    denied = (
        "socket", "socketpair", "socketcall", "connect", "bind", "listen",
        "accept", "accept4", "sendto", "sendmsg", "sendmmsg", "recvfrom",
        "recvmsg", "recvmmsg", "shutdown", "getsockname", "getpeername",
        "setsockopt", "getsockopt", "ptrace", "process_vm_readv",
        "process_vm_writev", "mount", "umount", "umount2", "pivot_root",
        "chroot", "unshare", "setns", "fsopen", "fsconfig", "fsmount",
        "move_mount", "open_tree", "mount_setattr", "bpf", "perf_event_open",
        "keyctl", "add_key", "request_key", "kill", "tkill", "tgkill",
        "rt_sigqueueinfo", "rt_tgsigqueueinfo",
        "pidfd_open", "pidfd_getfd", "pidfd_send_signal", "io_uring_setup",
        "io_uring_enter", "io_uring_register", "open_by_handle_at",
        "name_to_handle_at", "reboot", "kexec_load", "kexec_file_load",
        # Landlock does not mediate ownership, mode or timestamp changes.
        "chmod", "fchmod", "fchmodat", "fchmodat2", "chown", "fchown",
        "lchown", "fchownat", "utime", "utimes", "futimesat", "utimensat",
        "setxattr", "lsetxattr", "fsetxattr", "removexattr", "lremovexattr",
        "fremovexattr", "quotactl", "quotactl_fd", "acct", "swapon", "swapoff",
        "mknod", "mknodat",
        # Host IPC objects are outside Landlock's filesystem restrictions.
        "shmget", "shmat", "shmctl", "msgget", "msgsnd", "msgrcv", "msgctl",
        "semget", "semop", "semtimedop", "semctl",
    )
    try:
        for name in denied:
            number = lib.seccomp_syscall_resolve_name(name.encode("ascii"))
            # libseccomp returns negative pseudo-numbers for calls absent on the
            # native architecture. No such native syscall needs filtering.
            if number < 0:
                continue
            status = lib.seccomp_rule_add(context, 0x00050000 | errno.EPERM,
                                          number, 0)
            if status != 0:
                raise SandboxUnavailable(f"seccomp rule {name}: {status}")
        status = lib.seccomp_load(context)
        if status != 0:
            raise SandboxUnavailable(f"seccomp_load: {status}")
    finally:
        lib.seccomp_release(context)


def _run(workdir: Path, argv: list[str]) -> None:
    workdir = workdir.resolve(strict=True)
    if not workdir.is_dir() or workdir == Path("/"):
        raise SandboxUnavailable("workdir must be a dedicated directory")
    # No inherited capabilities are needed; NNP also makes later setuid/file
    # capability execs unable to grant privileges.
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                           ctypes.c_ulong, ctypes.c_ulong]
    libc.prctl.restype = ctypes.c_int
    _checked(libc.prctl(38, 1, 0, 0, 0), "PR_SET_NO_NEW_PRIVS")
    os.chdir(workdir)
    # Landlock cannot revoke access through already-open descriptors. Preserve
    # only caller-managed stdin/stdout/stderr and close everything else.
    maximum = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    if maximum == resource.RLIM_INFINITY:
        maximum = 1 << 20
    os.closerange(3, maximum)
    os.environ.clear()
    os.environ.update(PATH="/usr/bin:/bin", LANG="C", TMPDIR=str(workdir),
                      HOME=str(workdir))
    _landlock(libc, workdir)
    _seccomp()
    os.execvpe(argv[0], argv, dict(os.environ))


def _main(argv: list[str]) -> int:
    if len(argv) < 5 or argv[1] != "--run" or argv[3] != "--":
        print("growth sandbox: expected --run WORKDIR -- COMMAND [ARG ...]",
              file=sys.stderr)
        return 125
    try:
        _run(Path(argv[2]), argv[4:])
    except (OSError, ValueError, SandboxUnavailable) as exc:
        print(f"growth sandbox unavailable: {exc}", file=sys.stderr)
        return 125
    return 125  # execvpe never returns on success.


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
