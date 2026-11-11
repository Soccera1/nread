#!/usr/bin/env python3
"""Exercise the actual curses reader in a PTY (requires Python's pyte package)."""

import errno
import fcntl
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest

import pyte


BINARY = str(Path(sys.argv.pop(1) if len(sys.argv) > 1 else "src/nread").resolve())


class Reader:
    def __init__(self, args, home, stdin=None, env=None):
        self.screen = pyte.Screen(120, 30)
        self.stream = pyte.ByteStream(self.screen)
        self.output = bytearray()
        self.status = None
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(home)
            os.environ.update(TERM="xterm", HOME=str(home), LC_ALL="C.UTF-8")
            os.environ.pop("XDG_CONFIG_HOME", None)
            os.environ.pop("XDG_STATE_HOME", None)
            if env:
                os.environ.update(env)
            fcntl.ioctl(1, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 120, 0, 0))
            if stdin is not None:
                result = subprocess.run([BINARY, *args], input=stdin)
                os._exit(result.returncode)
            os.execv(BINARY, [BINARY, *args])
        self.read()

    @property
    def text(self):
        return "\n".join(self.screen.display)

    def read(self):
        deadline = time.monotonic() + 0.25
        while time.monotonic() < deadline:
            if select.select([self.fd], [], [], 0.05)[0]:
                try:
                    data = os.read(self.fd, 65536)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    break
                if not data:
                    break
                self.output.extend(data)
                self.stream.feed(data)
        if self.status is None:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.status = os.waitstatus_to_exitcode(status)

    def send(self, keys):
        os.write(self.fd, keys)
        self.read()

    def wait_for(self, text):
        deadline = time.monotonic() + 5
        while text not in self.text and time.monotonic() < deadline:
            self.read()
        assert text in self.text, self.text

    def exit(self, keys=b"\x18"):
        self.send(keys)
        deadline = time.monotonic() + 3
        while self.status is None and time.monotonic() < deadline:
            self.read()
        assert self.status == 0, self.text

    def close(self):
        if self.status is None:
            os.kill(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, 0)
        os.close(self.fd)


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.first = self.home / "first.txt"
        self.original = b"first line\n" + b"ordinary line\n" * 39 + b"needle here\nlast line\n"
        self.first.write_bytes(self.original)
        self.second = self.home / "second.txt"
        self.second.write_text("second file contents\n")

    def reader(self, *args, stdin=None, env=None):
        reader = Reader(args, self.home, stdin, env)
        self.addCleanup(reader.close)
        return reader

    def assert_main_menu(self, reader):
        menu = "\n".join(reader.screen.display[-2:])
        self.assertIn("Open File", menu)
        for label in ("Write Out", "Replace", "Cut", "Paste", "Execute", "Undo", "Justify"):
            self.assertNotIn(label, menu)

    def test_only_reader_options(self):
        result = subprocess.run([BINARY, "--help"], capture_output=True, text=True, check=True)
        self.assertIn("Usage: nread", result.stdout)
        for option in ("--saveonexit", "--backup", "--noread", "--speller", "--autoindent", "--view"):
            self.assertNotIn(option, result.stdout)
            self.assertNotEqual(subprocess.run([BINARY, option], capture_output=True).returncode, 0)

    @unittest.skipIf(os.getenv("NREAD_TEST_REDUCED_FEATURES"), "requires syntax highlighting")
    def test_installed_system_nanorc_fallback(self):
        installed = Path("/etc/nanorc")
        overrides = ("/etc/nreadrc", "/usr/local/etc/nreadrc", "/usr/local/etc/nanorc")
        if not installed.is_file() or any(Path(path).exists() for path in overrides):
            self.skipTest("requires an installed /etc/nanorc without higher-priority configuration")
        env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / "xdg"))
        explicit = subprocess.run([BINARY, "-f", str(installed), "--listsyntaxes"],
                                  env=env, capture_output=True, check=True)
        automatic = subprocess.run([BINARY, "--listsyntaxes"],
                                   env=env, capture_output=True, check=True)
        if not explicit.stdout:
            self.skipTest("installed nanorc does not define syntaxes")
        self.assertEqual(automatic.stdout, explicit.stdout)
        self.assertFalse(automatic.stderr)

    def test_typing_edit_shortcuts_and_paste_cannot_change_text(self):
        reader = self.reader("-I", str(self.first))
        reader.wait_for("first line")
        self.assert_main_menu(reader)
        reader.send(b"typed text\r\t\x7f\x04\x0b\x15\x0f\x13\x1c\x14")
        reader.send(b"\x1b[200~pasted text\nmore text\x1b[201~")
        self.assertIn("first line", reader.text)
        self.assertNotIn("typed text", reader.text)
        self.assertNotIn("pasted text", reader.text)
        reader.exit()
        self.assertEqual(self.first.read_bytes(), self.original)
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), ["first.txt", "second.txt"])

    def test_search_and_open_files(self):
        reader = self.reader("-I", str(self.first))
        reader.wait_for("first line")
        reader.send(b"\x06needle\r")
        reader.wait_for("needle here")
        self.assertIn("needle", reader.screen.display[reader.screen.cursor.y])
        reader.send(b"\x12" + os.fsencode(self.second) + b"\r")
        reader.wait_for("second file contents")
        reader.send(b"\x18")
        reader.wait_for("needle here")
        reader.exit()
        self.assertEqual(self.first.read_bytes(), self.original)

    def test_empty_startup_stays_read_only_and_can_open(self):
        reader = self.reader("-I")
        reader.wait_for("Welcome to nread")
        reader.send(b"cannot insert")
        self.assertNotIn("cannot insert", reader.text)
        reader.send(b"\x12" + os.fsencode(self.first) + b"\r")
        reader.wait_for("first line")
        reader.send(b"\x18")
        reader.exit()

    def test_missing_file_stays_read_only(self):
        missing = self.home / "missing.txt"
        reader = self.reader("-I", str(missing))
        reader.send(b"cannot create\x0f")
        reader.exit()
        self.assertFalse(missing.exists())

    def test_configuration_cannot_enable_editing(self):
        config = self.home / "reader.rc"
        config.write_text('set saveonexit\nset locking\nset backup\nset noread\n'
                          'set autoindent\nset backupdir "/nonexistent/nread-backups"\n'
                          'bind ^O writeout main\nbind ^K cut main\n'
                          'bind ^] enter main\nbind ^R replace all\n')
        reader = self.reader("-f", str(config), str(self.first))
        reader.wait_for("first line")
        self.assert_main_menu(reader)
        reader.send(b"text\x0f\x0b\x1d\x12")
        reader.exit()
        self.assertEqual(self.first.read_bytes(), self.original)
        self.assertFalse(list(self.home.glob("*.swp")))
        self.assertFalse(list(self.home.glob("*.save")))
        self.assertFalse(list(self.home.glob("*~")))

    def test_piped_input(self):
        reader = self.reader("-I", "-", stdin=b"piped contents\n")
        reader.wait_for("piped contents")
        reader.send(b"cannot insert")
        reader.exit()

    def test_multiple_command_line_files(self):
        reader = self.reader("-I", str(self.first), str(self.second))
        reader.wait_for("first line")
        reader.send(b"\x1b.")
        reader.wait_for("second file contents")
        reader.send(b"\x18")
        reader.wait_for("first line")
        reader.exit()

    def test_help_describes_only_reader_commands(self):
        reader = self.reader("-I", str(self.first))
        reader.send(b"\x07")
        reader.wait_for("nread Help")
        pages = reader.text
        for _ in range(10):
            reader.send(b"\x16")
            pages += reader.text
        for phrase in ("Write Out", "Replace", "Undo", "Execute", "Spell", "Justify",
                       "Hard wrapping", "Auto indent", "Cut from cursor"):
            self.assertNotIn(phrase, pages)
        reader.send(b"\x18")
        reader.exit()

    def test_modern_bindings(self):
        reader = self.reader("-I", "--modernbindings", str(self.first))
        reader.wait_for("first line")
        self.assert_main_menu(reader)
        reader.send(b"text\x13\x17\x12\x18\x16\x1a")
        reader.send(b"\x06needle\r")
        reader.wait_for("needle here")
        reader.send(b"\x0f" + os.fsencode(self.second) + b"\r")
        reader.wait_for("second file contents")
        reader.send(b"\x11")
        reader.wait_for("needle here")
        reader.exit(b"\x11")
        self.assertEqual(self.first.read_bytes(), self.original)

    def test_nanorc_fallback_locations(self):
        for location in (".nanorc", ".config/nano/nanorc", "xdg/nano/nanorc"):
            with self.subTest(location=location):
                config = self.home / location
                config.parent.mkdir(parents=True, exist_ok=True)
                config.write_text("set nohelp\n")
                reader = self.reader(str(self.first), env={"XDG_CONFIG_HOME": str(self.home / "xdg")})
                reader.wait_for("first line")
                self.assertNotIn("Open File", reader.text)
                reader.exit()
                config.unlink()

    def test_nreadrc_precedes_nanorc_in_all_locations(self):
        (self.home / ".nanorc").write_text("set nohelp\n")
        for location in (".nreadrc", ".config/nread/nreadrc", "xdg/nread/nreadrc"):
            with self.subTest(location=location):
                config = self.home / location
                config.parent.mkdir(parents=True, exist_ok=True)
                config.write_text("# An empty nreadrc still overrides nanorc.\n")
                reader = self.reader(str(self.first), env={"XDG_CONFIG_HOME": str(self.home / "xdg")})
                reader.wait_for("first line")
                self.assert_main_menu(reader)
                reader.exit()
                config.unlink()

    def test_explicit_rc_and_ignore_rc_override_default_lookup(self):
        (self.home / ".nreadrc").write_text("set nohelp\n")
        explicit = self.home / "explicit.rc"
        explicit.write_text("")
        for args in (("-I",), ("-f", str(explicit))):
            with self.subTest(args=args):
                reader = self.reader(*args, str(self.first))
                reader.wait_for("first line")
                self.assert_main_menu(reader)
                reader.exit()

    def test_disabled_features_are_ignored_without_replacing_reader_keys(self):
        config = self.home / ".nanorc"
        config.write_text('set speller\nset fill invalid\nset quotestr "["\n'
                          'unset brackets\nset breaklonglines\nset backupdir\n'
                          'set autoindent\nset saveonexit\n'
                          'bind ^X speller main\nbind ^X justify main\n'
                          'bind ^R linter main\nbind ^R writeout main\n'
                          'bind ^F whereis main\n'
                          'bind ^F formatter main\n'
                          'linter\nformatter\ncomment\ntabgives\n'
                          'extendsyntax absent formatter "bad command"\n'
                          'set constantshow\n')
        reader = self.reader(str(self.first))
        reader.wait_for("first line")
        self.assertNotIn("Mistakes", reader.text)
        self.assertNotIn("Unknown", reader.text)
        self.assertNotIn("requires", reader.text)
        reader.send(b"\x06needle\r")
        reader.wait_for("needle here")
        reader.send(b"\x12" + os.fsencode(self.second) + b"\r")
        reader.wait_for("second file contents")
        reader.send(b"\x18")
        reader.exit()

    def test_unknown_options_still_report_errors(self):
        (self.home / ".nreadrc").write_text("set misspelledoption\n")
        reader = self.reader(str(self.first))
        reader.wait_for("Mistakes in")
        reader.exit()
        self.assertIn(b"Unknown option: misspelledoption", reader.output)

    @unittest.skipUnless(os.getenv("NREAD_TEST_SYSCONFDIR"), "requires an isolated system config directory")
    def test_system_config_fallback_and_user_overrides(self):
        directory = Path(os.environ["NREAD_TEST_SYSCONFDIR"])
        directory.mkdir(parents=True, exist_ok=True)
        nano = directory / "nanorc"
        nread = directory / "nreadrc"
        self.addCleanup(nano.unlink, missing_ok=True)
        self.addCleanup(nread.unlink, missing_ok=True)
        nano.write_text("set nohelp\n")
        reader = self.reader(str(self.first))
        reader.wait_for("first line")
        self.assertNotIn("Open File", reader.text)
        reader.exit()

        nread.write_text("# Prefer this file to the system nanorc.\n")
        reader = self.reader(str(self.first))
        reader.wait_for("first line")
        self.assert_main_menu(reader)
        reader.exit()

        (self.home / ".nanorc").write_text("set nohelp\n")
        reader = self.reader(str(self.first))
        reader.wait_for("first line")
        self.assertNotIn("Open File", reader.text)
        reader.exit()

    @unittest.skipUnless(os.getenv("NREAD_TEST_REDUCED_FEATURES"), "requires the reduced-feature test build")
    def test_optional_disabled_features_are_ignored(self):
        (self.home / ".nanorc").write_text(
            'set mouse\nset linenumbers\nset historylog\nset positionlog\n'
            'set operatingdir\nset titlecolor\nset functioncolor\n'
            'bind ^X mouse main\nbind ^X nosyntax main\n'
            'bind ^R browser main\nbind ^R linenumbers main\n'
            'include "/nonexistent/nread-syntax"\nsyntax\ncolor invalid\n'
            'extendsyntax absent color invalid\n')
        reader = self.reader(str(self.first))
        reader.wait_for("first line")
        self.assertNotIn("Mistakes", reader.text)
        self.assert_main_menu(reader)
        reader.exit()
        self.assertNotIn(b"Unknown", reader.output)
        self.assertNotIn(b"Error in", reader.output)


if __name__ == "__main__":
    unittest.main()
