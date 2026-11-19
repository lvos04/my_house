"""Bestandsgrens voor de Zigbee-procesboom die dezelfde uid gebruikt als de kernapp."""

import ctypes
import errno
import os
from pathlib import Path

# Linux Landlock UAPI voor de amd64- en arm64-images. Versie 3 beschermt ook truncate.
CREATE_RULESET, ADD_RULE, RESTRICT_SELF = 444, 445, 446
EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = (1 << n for n in range(4))
REMOVE_DIR, REMOVE_FILE, MAKE_DIR, MAKE_REG, MAKE_SYM, REFER, TRUNCATE, IOCTL_DEV = (
    1 << n for n in (4, 5, 7, 8, 12, 13, 14, 15)
)


class PathRule(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def restrict_files(program: Path, runtime: Path, data: Path, device: Path) -> None:
    """Na no_new_privs en vóór Node. Een niet-beschikbare grens weigert de optionele dienst."""
    syscall = ctypes.CDLL(None, use_errno=True).syscall

    def call(number, *args):
        result = syscall(number, *args)
        if result < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))
        return result

    abi = call(CREATE_RULESET, 0, 0, 1)
    if abi < 3:
        raise OSError(errno.ENOTSUP, "Landlock-versie 3 is vereist.")
    mask = (1 << (16 if abi >= 5 else 15)) - 1
    access = ctypes.c_uint64(mask)
    ruleset = call(CREATE_RULESET, ctypes.byref(access), ctypes.sizeof(access), 0)
    try:
        read_tree = READ_FILE | READ_DIR
        write_tree = (read_tree | WRITE_FILE | REMOVE_DIR | REMOVE_FILE | MAKE_DIR | MAKE_REG
                      | MAKE_SYM | REFER | TRUNCATE)
        rules = [
            (program, read_tree | EXECUTE), (runtime, read_tree | EXECUTE), (data, write_tree),
            (device, READ_FILE | WRITE_FILE | (IOCTL_DEV & mask)),
            (Path("/dev/null"), READ_FILE | WRITE_FILE), (Path("/dev/urandom"), READ_FILE),
            (Path("/etc/ssl/openssl.cnf"), READ_FILE),  # Node leest de publieke OpenSSL-configuratie bij elke start.
        ]
        for path, rights in rules:
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                call(ADD_RULE, ruleset, 1, ctypes.byref(PathRule(rights, fd)), 0)
            finally:
                os.close(fd)
        call(RESTRICT_SELF, ruleset, 0)
    finally:
        os.close(ruleset)
