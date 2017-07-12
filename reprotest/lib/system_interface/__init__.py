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

class SystemInterface:
    """
        A base class for a system interface class. It provides a common
        ancestor for adt_testbed to figure out which commands/call on each
        specific host.
    """
    pass
