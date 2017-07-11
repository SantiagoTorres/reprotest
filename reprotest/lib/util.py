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
#
# Refactoring was done on 2017 by Santiago Torres-Arias
# <santiago@archlinux.org> to simplify cross-platform compatbility for
# reprotest
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


timeouts = {'short': 100, 'copy': 300, 'install': 3000, 'test': 10000,
            'build': 100000}

class Path:
    '''Represent a file/dir with a host and a testbed path'''

    def __init__(self, testbed, host, tb, is_dir=None):
        '''Create a Path object.

        The object itself is just a pair of file names, nothing more. They do
        not need to exist until you call copyup() or copydown() on them.

        testbed: the Testbed object which this refers to
        host: path of the file on the host
        tb: path of the file in testbed
        is_dir: whether path is a directory; None for "unspecified" if you only
                need copydown()
        '''
        self.testbed = testbed
        self.host = host
        self.tb = tb
        self.is_dir = is_dir

    def copydown(self, check_existing=False):
        '''Copy file from the host to the testbed

        If check_existing is True, don't copy if the testbed path already
        exists.
        '''
        if check_existing and self.testbed.execute(['test', '-e', self.tb])[0] == 0:
            adtlog.debug('copydown: tb path %s already exists' % self.tb)
            return

        # create directory on testbed
        self.testbed.check_exec(['mkdir', '-p', os.path.dirname(self.tb)])

        if os.path.isdir(self.host):
            # directories need explicit '/' appended for VirtSubproc
            self.testbed.command('copydown', (self.host + '/', self.tb + '/'))
        else:
            self.testbed.command('copydown', (self.host, self.tb))

        # we usually want our files be readable for the non-root user
        if self.testbed.user:
            rc = self.testbed.execute(['chown', '-R', self.testbed.user, '--', self.tb],
                                      stderr=subprocess.PIPE)[0]
            if rc != 0:
                # chowning doesn't work on all shared downtmps, try to chmod
                # instead
                self.testbed.check_exec(['chmod', '-R', 'go+rwX', '--', self.tb])

    def copyup(self, check_existing=False):
        '''Copy file from the testbed to the host

        If check_existing is True, don't copy if the host path already
        exists.
        '''
        if check_existing and os.path.exists(self.host):
            adtlog.debug('copyup: host path %s already exists' % self.host)
            return

        os.makedirs(os.path.dirname(self.host), exist_ok=True, mode=0o2755)
        assert self.is_dir is not None
        if self.is_dir:
            self.testbed.command('copyup', (self.tb + '/', self.host + '/'))
        else:
            self.testbed.command('copyup', (self.tb, self.host))


class TempPath(Path):
    '''Represent a file in the hosts'/testbed's temporary directories

    These are only guaranteed to exit within one testbed run.
    '''
    def __init__(self, testbed, name, is_dir=False, autoclean=True):
        '''Create a temporary Path object.

        The object itself is just a pair of file names, nothing more. They do
        not need to exist until you call copyup() or copydown() on them.

        testbed: the Testbed object which this refers to
        name: name of the temporary file (without path); host and tb
              will then be derived from that
        is_dir: whether path is a directory; None for "unspecified" if you only
                need copydown()
        autoclean: If True (default), remove file when test finishes. Should
                be set to False for files which you want to keep in the
                --output-dir which are useful for reporting results, like test
                stdout/err, log files, and binaries.
        '''
        # if the testbed supports a shared downtmp, use that to avoid
        # unnecessary copying, unless we want to permanently keep the file
        if testbed.shared_downtmp and (not testbed.output_dir or autoclean):
            host = testbed.shared_downtmp
        else:
            host = testbed.output_dir
        self.autoclean = autoclean
        Path.__init__(self, testbed, os.path.join(host, name),
                      os.path.join(testbed.scratch, name),
                      is_dir)

    def __del__(self):
        if self.autoclean:
            if os.path.exists(self.host):
                try:
                    os.unlink(self.host)
                except OSError as e:
                    if e.errno == errno.EPERM:
                        self.testbed.check_exec(['rm', '-rf', self.tb])
                    else:
                        raise

#
# Helper functions
#


def child_ps(pid):
    '''Get all child processes of pid'''

    try:
        out = subprocess.check_output(['ps', '-o', 'pid=', '--ppid', str(pid)])
        return [int(p) for p in out.split()]
    except subprocess.CalledProcessError:
        return []


def killtree(pid):
    '''Recursively kill pid and all of its children'''

    for child in child_ps(pid):
        killtree(child)
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
