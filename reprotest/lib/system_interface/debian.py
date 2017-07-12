# adt_testbed.py is part of autopkgtest
# autopkgtest is a tool for testing Debian binary packages. The
# system_interface module is an addition for reprotest to make
# this module distro-agnostic
#
# autopkgtest is Copyright (C) 2006-2015 Canonical Ltd.
# the system_interface module is Copyright (C) 2017 Santiago Torres-Arias
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
import subprocess

from . import SystemInterface


class DebianInterface(SystemInterface):
    """
        SystemInterface implementation for Debian hosts. Contains commands that
        are specific to the Debian toolchain.
    """

    def get_arch(self):
        return ['dpkg', '--print-architecture']

    def get_installed_packages(self, target_file):
        return ['sh', '-ec',
                "dpkg-query --show -f '${Package}\\t${Version}\\n' > %s" % target_file.tb]

    def can_query_packages(self):
        try:
            return subprocess.check_call(['which', 'dpkg-query'], stdout=subprocess.DEVNULL) == 0
        except subprocess.CalledProcessError:
            return 0
