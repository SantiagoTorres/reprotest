# adt_testbed.py is part of autopkgtest
# autopkgtest is a tool for testing Debian binary packages
#
# autopkgtest is Copyright (C) 2006-2015 Canonical Ltd.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
#
# See the file CREDITS for a full list of credits information (often
# installed as /usr/share/doc/autopkgtest/CREDITS).

import os
import sys
import errno
import time
import pipes
import traceback
import re
import signal
import subprocess
import tempfile
import shutil
import urllib.parse


# TODO: removing this import disables install_tmp, may want to restore
# it at some point if I'm improving support for building Debian packages in
# particular.

# from debian import debian_support

from reprotest.lib.system_interface.debian import debian_interface
from reprotest.lib import adtlog
from reprotest.lib import VirtSubproc
from reprotest.lib.util import TempPath, Path, killtree


timeouts = {'short': 100, 'copy': 300, 'install': 3000, 'test': 10000,
            'build': 100000}


class Testbed:
    def __init__(self, vserver_argv, output_dir, user,
                 setup_commands=[], add_apt_pockets=[], copy_files=[]):
        self.system_interface = debian_interface()
        self.sp = None
        self.lastsend = None
        self.scratch = None
        self.modified = False
        self._need_reset_apt = False
        self.stop_sent = False
        self.system_arch = None
        self.exec_cmd = None
        self.output_dir = output_dir
        self.shared_downtmp = None  # testbed's downtmp on the host, if supported
        self.vserver_argv = vserver_argv
        self.install_tmp_env = []
        self.user = user
        self.setup_commands = setup_commands
        self.add_apt_pockets = add_apt_pockets
        self.copy_files = copy_files
        self.initial_kernel_version = None
        # tests might install a different kernel; [(testname, reboot_marker, kver)]
        self.test_kernel_versions = []
        # used for tracking kernel version changes
        self.last_test_name = ''
        self.last_reboot_marker = ''
        self.eatmydata_prefix = []
        self.apt_pin_for_pockets = []
        self.nproc = None
        self.cpu_model = None
        self.cpu_flags = None

        try:
            self.devnull = subprocess.DEVNULL
        except AttributeError:
            self.devnull = open(os.devnull, 'rb')

        adtlog.debug('testbed init')

    def start(self):
        # are we running from a checkout?
        root_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        if os.path.exists(os.path.join(root_dir, '.git')):
            try:
                head = subprocess.check_output(['git', 'show', '--no-patch', '--oneline'],
                                               cwd=root_dir)
                head = head.decode('UTF-8').strip()
            except OSError:
                head = 'cannot determine current HEAD'
            adtlog.info('git checkout: %s' % head)
        else:
            adtlog.info('version @version@')

        # log command line invocation for the log
        adtlog.info('host %s; command line: %s' % (
            os.uname()[1], ' '.join([pipes.quote(w) for w in sys.argv])))

        self.sp = subprocess.Popen(self.vserver_argv,
                                   stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE,
                                   universal_newlines=True)
        self.expect('ok', 0)

    def stop(self):
        adtlog.debug('testbed stop')
        if self.stop_sent:
            # avoid endless loop
            return
        self.stop_sent = True

        self.close()
        if self.sp is None:
            return
        ec = self.sp.returncode
        if ec is None:
            self.sp.stdout.close()
            self.send('quit')
            self.sp.stdin.close()
            ec = self.sp.wait()
        if ec:
            self.bomb('testbed gave exit status %d after quit' % ec)
        self.sp = None

    def open(self):
        adtlog.debug('testbed open, scratch=%s' % self.scratch)
        if self.scratch is not None:
            return
        pl = self.command('open', (), 1)
        self._opened(pl)

    def post_boot_setup(self):
        '''Setup after (re)booting the test bed'''

        # provide autopkgtest-reboot command, if reboot is supported; /run is
        # usually "noexec" and /[s]bin might be readonly, so create in /tmp
        if 'reboot' in self.caps and 'root-on-testbed' in self.caps:
            adtlog.debug('testbed supports reboot, creating /tmp/autopkgtest-reboot')
            self.execute(['sh', '-ecC', '''[ ! -e /tmp/autopkgtest-reboot ] || exit 0; '''
                          '''/bin/echo -e '#!/bin/sh -e\\n'''
                          '''[ -n "$1" ] || { echo "Usage: $0 <mark>" >&2; exit 1; }\\n'''
                          '''echo "$1" > /run/autopkgtest-reboot-mark\\n'''
                          '''test_script_pid=$(cat /tmp/adt_test_script_pid)\\n'''
                          '''p=$PPID; while true; do read _ c _ pp _ < /proc/$p/stat;'''
                          '''  [ $pp -ne $test_script_pid ] || break; p=$pp; done\\n'''
                          '''kill -KILL $p\\n' > /tmp/autopkgtest-reboot;'''
                          '''chmod 755 /tmp/autopkgtest-reboot;'''
                          '''[ -L /sbin/autopkgtest-reboot ] || ln -s '''
                          '''  /tmp/autopkgtest-reboot /sbin/autopkgtest-reboot 2>/dev/null || true'''])

            self.execute(['sh', '-ecC', '''[ ! -e /tmp/autopkgtest-reboot-prepare ] || exit 0; '''
                          '''/bin/echo -e '#!/bin/sh -e\\n'''
                          '''[ -n "$1" ] || { echo "Usage: $0 <mark>" >&2; exit 1; }\\n'''
                          '''echo "$1" > /run/autopkgtest-reboot-prepare-mark\\n'''
                          '''test_script_pid=$(cat /tmp/adt_test_script_pid)\\n'''
                          '''kill -KILL $test_script_pid\\n'''
                          '''while [ -e /run/autopkgtest-reboot-prepare-mark ]; do sleep 0.5; done\\n'''
                          ''' '> /tmp/autopkgtest-reboot-prepare;'''
                          '''chmod 755 /tmp/autopkgtest-reboot-prepare;'''])

        # record running kernel version
        kver = self.check_exec(['uname', '-srv'], True).strip()
        if not self.initial_kernel_version:
            assert not self.last_test_name
            self.initial_kernel_version = kver
            adtlog.info('testbed running kernel: ' + self.initial_kernel_version)
        else:
            if kver != self.initial_kernel_version:
                self.test_kernel_versions.append((self.last_test_name, self.last_reboot_marker, kver))
                adtlog.info('testbed running kernel changed: %s (current test: %s%s)' %
                            (kver, self.last_test_name,
                             self.last_reboot_marker and (', last reboot marker: ' + self.last_reboot_marker) or ''))

        # get CPU info
        if self.nproc is None:
            cpu_info = self.check_exec(['sh', '-c', 'nproc; cat /proc/cpuinfo 2>/dev/null || true'],
                                       stdout=True).strip()
            self.nproc = cpu_info.split('\n', 1)[0]
            m = re.search('^(model.*name|cpu)\s*:\s*(.*)$', cpu_info, re.MULTILINE | re.IGNORECASE)
            if m:
                self.cpu_model = m.group(2)
            m = re.search('^(flags|features)\s*:\s*(.*)$', cpu_info, re.MULTILINE | re.IGNORECASE)
            if m:
                self.cpu_flags = m.group(2)

    def _opened(self, pl):
        self.scratch = pl[0]
        self.deps_installed = []
        self.apt_pin_for_pockets = []
        self.recommends_installed = False
        self.exec_cmd = list(map(urllib.parse.unquote, self.command('print-execute-command', (), 1)[0].split(',')))
        self.caps = self.command('capabilities', (), None)
        adtlog.debug('testbed capabilities: %s' % self.caps)
        for c in self.caps:
            if c.startswith('downtmp-host='):
                self.shared_downtmp = c.split('=', 1)[1]

        # provide a default for --user
        if self.user is None and 'root-on-testbed' in self.caps:
            self.user = ''
            for c in self.caps:
                if c.startswith('suggested-normal-user='):
                    self.user = c.split('=', 1)[1]

        # determine testbed architecture
        self.system_arch = self.check_exec(self.system_interface.get_arch_exec(), True).strip()
        adtlog.info('testbed package architecture: ' + self.system_arch)

        # do we have eatmydata?
        (code, out, err) = self.execute(['which', 'eatmydata'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if code == 0:
            adtlog.debug('testbed has eatmydata')
            self.eatmydata_prefix = [out.strip()]

        # record package versions of pristine testbed
        if self.output_dir and self.system_interface.can_query_packages():
            pkglist = TempPath(self, 'testbed-packages', autoclean=False)
            self.check_exec(self.system_interface.get_testbed_packages(pkglist))
            pkglist.copyup()

        self.post_boot_setup()

    def close(self):
        adtlog.debug('testbed close, scratch=%s' % self.scratch)
        if self.scratch is None:
            return
        self.scratch = None
        if self.sp is None:
            return
        self.command('close')
        self.shared_downtmp = None

    def bomb(self, m, _type=adtlog.TestbedFailure):
        adtlog.debug('%s %s' % (_type.__name__, m))
        # self.stop()
        raise _type(m)

    def badpkg(self, m):
        self.bomb(m, adtlog.BadPackageError)

    def send(self, string):
        try:
            adtlog.debug('sending command to testbed: ' + string)
            self.sp.stdin.write(string)
            self.sp.stdin.write('\n')
            self.sp.stdin.flush()
            self.lastsend = string
        except:
            (type, value, dummy) = sys.exc_info()
            self.bomb('cannot send to testbed: %s' % traceback.
                      format_exception_only(type, value))

    def expect(self, keyword, nresults):
        l = self.sp.stdout.readline()
        if not l:
            self.bomb('unexpected eof from the testbed')
        if not l.endswith('\n'):
            self.bomb('unterminated line from the testbed')
        l = l.rstrip('\n')
        adtlog.debug('got reply from testbed: ' + l)
        ll = l.split()
        if not ll:
            self.bomb('unexpected whitespace-only line from the testbed')
        if ll[0] != keyword:
            if self.lastsend is None:
                self.bomb("got banner `%s', expected `%s...'" %
                          (l, keyword))
            else:
                self.bomb("sent `%s', got `%s', expected `%s...'" %
                          (self.lastsend, l, keyword))
        ll = ll[1:]
        if nresults is not None and len(ll) != nresults:
            self.bomb("sent `%s', got `%s' (%d result parameters),"
                      " expected %d result parameters" %
                      (self.lastsend, l, len(ll), nresults))
        return ll

    def command(self, cmd, args=(), nresults=0, unquote=True):
        # pass args=[None,...] or =(None,...) to avoid more url quoting
        if type(cmd) is str:
            cmd = [cmd]
        if len(args) and args[0] is None:
            args = args[1:]
        else:
            args = list(map(urllib.parse.quote, args))
        al = cmd + args
        self.send(' '.join(al))
        ll = self.expect('ok', nresults)
        if unquote:
            ll = list(map(urllib.parse.unquote, ll))
        return ll

    # TODO: with stdout and stderr defaulting to None, this function
    # eats all errors/output from its call, which is not the right
    # thing.
    def execute(self, argv, xenv=[], stdout=None, stderr=None, kind='short'):
        '''Run command in testbed.

        The commands stdout/err will be piped directly to adt-run and its log
        files, unless redirection happens with the stdout/stderr arguments
        (passed to Popen).

        Return (exit code, stdout, stderr). stdout/err will be None when output
        is not redirected.
        '''
        env = list(xenv)  # copy
        if kind == 'install':
            env.append('DEBIAN_FRONTEND=noninteractive')
            env.append('APT_LISTBUGS_FRONTEND=none')
            env.append('APT_LISTCHANGES_FRONTEND=none')
        env += self.install_tmp_env

        adtlog.debug('testbed command %s, kind %s, sout %s, serr %s, env %s' %
                     (argv, kind, stdout and 'pipe' or 'raw',
                      stderr and 'pipe' or 'raw', env))

        if env:
            argv = ['env'] + env + argv

        # import pdb; pdb.set_trace()
        VirtSubproc.timeout_start(timeouts[kind])
        try:
            proc = subprocess.Popen(self.exec_cmd + argv,
                                    stdin=self.devnull,
                                    stdout=stdout, stderr=stderr)
            (out, err) = proc.communicate()
            if out is not None:
                out = out.decode()
            if err is not None:
                err = err.decode()
            VirtSubproc.timeout_stop()
        except VirtSubproc.Timeout:
            # This is a bit of a hack, but what can we do.. we can't kill/clean
            # up sudo processes, we can only hope that they clean up themselves
            # after we stop the testbed
            killtree(proc.pid)
            adtlog.debug('timed out on %s %s (kind: %s)' % (self.exec_cmd, argv, kind))
            if 'sudo' not in self.exec_cmd:
                proc.wait()
            msg = 'timed out on command "%s" (kind: %s)' % (' '.join(argv), kind)
            if kind == 'test':
                adtlog.error(msg)
                raise
            else:
                self.bomb(msg)

        adtlog.debug('testbed command exited with code %i' % proc.returncode)

        if proc.returncode in (254, 255):
            msg = 'testbed auxverb failed with exit code %i' % proc.returncode
            if out:
                msg += '\n---- stdout ----\n%s----------------\n' % out
            if err:
                msg += '\n---- stderr ----\n%s----------------\n' % err
            self.bomb(msg)

        return (proc.returncode, out, err)

    def check_exec(self, argv, stdout=False, kind='short', xenv=[]):
        '''Run argv in testbed.

        If stdout is True, capture stdout and return it. Otherwise, don't
        redirect and return None.

        argv must succeed and not print any stderr.
        '''
        (code, out, err) = self.execute(argv,
                                        xenv=xenv,
                                        stdout=(stdout and subprocess.PIPE or None),
                                        stderr=subprocess.PIPE, kind=kind)
        if err:
            self.bomb('"%s" failed with stderr "%s"' % (' '.join(argv), err),
                      adtlog.AutopkgtestError)
        if code != 0:
            self.bomb('"%s" failed with status %i' % (' '.join(argv), code),
                      adtlog.AutopkgtestError)
        return out
