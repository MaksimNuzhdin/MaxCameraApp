"""seccomp-BPF network lockdown, installed after a process has opened its sockets.

x86-64 only. The filter is applied to every thread (TSYNC) and cannot be removed.
After `lock_network()`:
  * no new IP sockets can be created (only AF_UNIX, and only if allowed),
  * existing UDP sockets cannot be re-pointed: connect/bind/sendmsg are refused
    and sendto is refused whenever it carries a destination address,
  * no program can be executed and no other process can be inspected.
Refused calls fail with EPERM.
"""
import ctypes
import errno
import platform
import struct

AUDIT_ARCH_X86_64 = 0xC000003E
_NR = dict(socket=41, connect=42, sendto=44, sendmsg=46, bind=49, listen=50, ptrace=101, mount=165,
           umount2=166, pivot_root=155, chroot=161, setns=308, unshare=272, execve=59, execveat=322,
           sendmmsg=307, process_vm_readv=310, process_vm_writev=311, bpf=321, perf_event_open=298,
           io_uring_setup=425, io_uring_enter=426, io_uring_register=427, keyctl=250, add_key=248,
           request_key=249, init_module=175, finit_module=313, kexec_load=246, userfaultfd=323,
           memfd_create=319)
_AF_UNIX = 1

_LD, _JEQ, _RET = 0x20, 0x15, 0x06
_DATA_NR, _DATA_ARCH, _ARG0, _ARG4_LO, _ARG4_HI = 0, 4, 16, 48, 52
_ALLOW = 0x7fff0000
_ERRNO = 0x00050000 | errno.EPERM
_KILL = 0x80000000


def _stmt(code, k):
    return struct.pack("HBBI", code, 0, 0, k)


def _jump(code, k, jt, jf):
    return struct.pack("HBBI", code, jt, jf, k)


def _program(allow_unix_connect, allow_listen):
    denied = ["ptrace", "mount", "umount2", "pivot_root", "chroot", "setns", "unshare", "execve",
              "execveat", "sendmsg", "sendmmsg", "process_vm_readv", "process_vm_writev", "bpf",
              "perf_event_open", "io_uring_setup", "io_uring_enter", "io_uring_register", "keyctl",
              "add_key", "request_key", "init_module", "finit_module", "kexec_load", "userfaultfd",
              "memfd_create", "bind"]
    if not allow_unix_connect:
        denied.append("connect")
    if not allow_listen:
        denied.append("listen")
    rules = [_stmt(_LD, _DATA_ARCH), _jump(_JEQ, AUDIT_ARCH_X86_64, 1, 0), _stmt(_RET, _KILL),
             _stmt(_LD, _DATA_NR),
             # x32 ABI numbers alias the native table, so they are refused outright.
             struct.pack("HBBI", 0x35, 0, 1, 0x40000000), _stmt(_RET, _ERRNO)]
    for name in denied:
        rules += [_jump(_JEQ, _NR[name], 0, 1), _stmt(_RET, _ERRNO)]
    # socket(): AF_UNIX only.
    rules += [_jump(_JEQ, _NR["socket"], 0, 3), _stmt(_LD, _ARG0), _jump(_JEQ, _AF_UNIX, 1, 0), _stmt(_RET, _ERRNO)]
    rules += [_stmt(_LD, _DATA_NR)]
    # sendto(): destination pointer (arg 4) must be NULL.
    rules += [_jump(_JEQ, _NR["sendto"], 0, 5), _stmt(_LD, _ARG4_LO), _jump(_JEQ, 0, 0, 2),
              _stmt(_LD, _ARG4_HI), _jump(_JEQ, 0, 1, 0), _stmt(_RET, _ERRNO)]
    rules += [_stmt(_RET, _ALLOW)]
    return rules


def lock_network(allow_unix_connect=False, allow_listen=False):
    if platform.machine() != "x86_64":
        raise RuntimeError("the network lockdown filter is defined for x86-64 only")
    libc = ctypes.CDLL(None, use_errno=True)
    PR_SET_NO_NEW_PRIVS, SYS_seccomp, SET_MODE_FILTER, FLAG_TSYNC = 38, 317, 1, 1
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0):
        raise OSError(ctypes.get_errno(), "prctl(NO_NEW_PRIVS) failed")
    rules = _program(allow_unix_connect, allow_listen)
    blob = ctypes.create_string_buffer(b"".join(rules))

    class Prog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]

    prog = Prog(len(rules), ctypes.addressof(blob))
    if libc.syscall(SYS_seccomp, SET_MODE_FILTER, FLAG_TSYNC, ctypes.byref(prog)):
        raise OSError(ctypes.get_errno(), "seccomp filter installation failed")
