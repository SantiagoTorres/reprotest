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

from . import system_interface

# TODO: removing this import disables install_tmp, may want to restore
# it at some point if I'm improving support for building Debian packages in
# particular.

class arch_interface(system_interface):

    def get_arch_exec(self):
        return ['uname', '-m']

    def get_testbed_packages(self, target_file):
        return ['sh', '-ec', "pacman -Q > %s" % target_file.tb]

    def can_query_packages(self):
        try:
            return subprocess.check_call(['which', 'pacman'], stdout=subprocess.DEVNULL) == 0
        except:
            return 0

def get_interface():
    return arch_interface
