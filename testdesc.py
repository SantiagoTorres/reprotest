# testdesc is part of autopkgtest
# autopkgtest is a tool for testing Debian binary packages
#
# autopkgtest is Copyright (C) 2006-2014 Canonical Ltd.
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

import adtlog
import string
import re
import errno
import os.path

import debian.deb822
import debian.debian_support


#
# Abstract test representation
#

known_restrictions = ['rw-build-tree', 'breaks-testbed', 'needs-root',
                      'build-needed', 'allow-stderr', 'isolation-container',
                      'isolation-machine', 'needs-recommends']


class Unsupported(Exception):
    '''Test cannot be run in the testbed'''

    def __init__(self, testname, message):
        Exception(testname, message)

    def report(self):
        adtlog.report(self.args[0], 'SKIP %s' % self.args[1])


class InvalidControl(Exception):
    '''Test has invalid control data'''

    def __init__(self, testname, message):
        Exception(testname, message)

    def report(self):
        adtlog.report(self.args[0], 'BROKEN %s' % self.args[1])


class Test:
    '''Test description.

    This is only a representation of the metadata, it does not have any
    actions.
    '''
    def __init__(self, name, path, command, restrictions, features, depends):
        '''Create new test description

        A test must have either "path" or "command", the respective other value
        must be None.

        @name: Test name
        @path: path to the test's executable, relative to source tree
        @command: shell command for the test code
        @restrictions, @features: string lists, as in README.package-tests
        @depends: string list of test dependencies (packages)
        '''
        if '/' in name:
            raise Unsupported(name, 'test name may not contain / character')
        for r in restrictions:
            if r not in known_restrictions:
                raise Unsupported(name, 'unknown restriction %s' % r)

        if not ((path is None) ^ (command is None)):
            raise ValueError('Test must have either path or command')

        self.name = name
        self.path = path
        self.command = command
        self.features = features
        self.depends = depends
        self.restrictions = restrictions
        # None while test hasn't run yet; True: pass, False: fail
        self.result = None
        adtlog.debug('Test defined: name %s path %s restrictions %s '
                     ' features %s depends %s' % (name, path, restrictions,
                                                  features, depends))

    def passed(self):
        '''Mark test as passed'''

        self.result = True
        adtlog.report(self.name, 'PASS')

    def failed(self, reason):
        '''Mark test as failed'''

        self.result = False
        adtlog.report(self.name, 'FAIL ' + reason)

    def check_testbed_compat(self, caps):
        '''Check for restrictions incompatible with test bed capabilities.

        Raise Unsupported exception if there are any.
        '''
        if 'isolation-container' in self.restrictions and \
           'isolation-container' not in caps and \
           'isolation-machine' not in caps:
            raise Unsupported(self.name,
                              'Test requires container-level isolation but '
                              'testbed does not provide that')

        if 'isolation-machine' in self.restrictions and \
           'isolation-machine' not in caps:
            raise Unsupported(self.name,
                              'Test requires machine-level isolation but '
                              'testbed does not provide that')

        if 'breaks-testbed' in self.restrictions and \
           'revert-full-system' not in caps:
            raise Unsupported(self.name,
                              'Test breaks testbed but testbed does not '
                              'provide revert-full-system')

        if 'needs-root' in self.restrictions and \
           'root-on-testbed' not in caps:
            raise Unsupported(self.name,
                              'Test needs root on testbed which is not '
                              'available')

#
# Parsing for Debian source packages
#


def parse_rfc822(path):
    '''Parse Debian-style RFC822 file

    Yield dictionaries with the keys/values.
    '''
    try:
        f = open(path, encoding='UTF-8')
    except (IOError, OSError) as oe:
        if oe.errno != errno.ENOENT:
            raise
        return

    # filter out comments, python-debian doesn't do that
    # (http://bugs.debian.org/743174)
    lines = []
    for l in f:
        # completely ignore ^# as that breaks continuation lines
        if l.startswith('#'):
            continue
        # filter out comments which don't start on first column
        l = l.split('#', 1)[0]
        lines.append(l)
    f.close()

    for p in debian.deb822.Deb822.iter_paragraphs(lines):
        r = {}
        for field, value in p.items():
            # un-escape continuation lines
            v = ''.join(value.split('\n')).replace('  ', ' ')
            field = string.capwords(field)
            r[field] = v
        yield r


def _debian_packages_from_source(srcdir):
    packages = []

    for st in parse_rfc822(os.path.join(srcdir, 'debian/control')):
        if 'Package' not in st:
            # source stanza
            continue
        if 'Xc-package-type' in st:
            # filter out udebs
            continue
        arch = st['Architecture']
        if arch in ('all', 'any'):
            packages.append(st['Package'])
        else:
            packages.append('%s [%s]' % (st['Package'], arch))

    return packages


def _debian_build_deps_from_source(srcdir):
    deps = []
    for st in parse_rfc822(os.path.join(srcdir, 'debian/control')):
        if 'Build-depends' in st:
            for d in st['Build-depends'].split(','):
                dp = d.strip()
                if dp:
                    deps.append(dp)
        if 'Build-depends-indep' in st:
            for d in st['Build-depends-indep'].split(','):
                dp = d.strip()
                if dp:
                    deps.append(dp)
    # @builddeps@ should always imply build-essential
    deps.append('build-essential')
    return deps


dep_re = re.compile(
    r'(?P<package>[a-z0-9+-.]+)(?::native)?\s*'
    r'(\((?P<relation><<|<=|>=|=|>>)\s*(?P<version>[^\)]*)\))?'
    r'(\s*\[[[a-z0-9+-. ]+\])?$')


def _debian_check_dep(testname, dep):
    '''Check a single Debian dependency'''

    dep = dep.strip()
    m = dep_re.match(dep)
    if not m:
        raise InvalidControl(testname, "Test Depends field contains an "
                             "invalid dependency `%s'" % dep)
    if m.group("version"):
        try:
            debian.debian_support.NativeVersion(m.group('version'))
        except ValueError:
            raise InvalidControl(testname, "Test Depends field contains "
                                 "dependency `%s' with an "
                                 "invalid version" % dep)
        except AttributeError:
            # too old python-debian, skip the check
            pass


def _parse_debian_depends(testname, dep_str, srcdir):
    '''Parse Depends: line in a Debian package

    Split dependencies (comma separated), validate their syntax, and expand @
    and @builddeps@. Return a list of dependencies.

    This may raise an InvalidControl exception if there are invalid
    dependencies.
    '''
    deps = []
    for alt_group_str in dep_str.split(','):
        alt_group_str = alt_group_str.strip()
        if not alt_group_str:
            # happens for empty depends or trailing commas
            continue
        adtlog.debug('processing dependency %s' % alt_group_str)
        if alt_group_str == '@':
            for d in _debian_packages_from_source(srcdir):
                adtlog.debug('synthesised dependency %s' % d)
                deps.append(d)
        elif alt_group_str == '@builddeps@':
            for d in _debian_build_deps_from_source(srcdir):
                adtlog.debug('synthesised dependency %s' % d)
                deps.append(d)
        else:
            for dep in alt_group_str.split('|'):
                _debian_check_dep(testname, dep)
            deps.append(alt_group_str)

    return deps


def parse_debian_source(srcdir, testbed_caps):
    '''Parse test descriptions from a Debian DEP-8 source dir

    Return (list of Test objects, some_skipped). If this encounters any invalid
    restrictions, fields, or test restrictions which cannot be met by the given
    testbed capabilities, the test will be skipped (and reported so), and not
    be included in the result.

    This may raise an InvalidControl exception.
    '''
    some_skipped = False
    tests = []
    for record in parse_rfc822(os.path.join(srcdir, 'debian', 'tests',
                                            'control')):
        try:
            try:
                test_names = record['Tests'].split()
            except KeyError:
                raise InvalidControl('*', 'missing "Tests" field')

            test_dir = record.get('Tests-directory', 'debian/tests')
            depends = _parse_debian_depends(test_names[0],
                                            record.get('Depends', '@'),
                                            srcdir)

            for n in test_names:
                test = Test(n, os.path.join(test_dir, n), None,
                            record.get('Restrictions', '').split(),
                            record.get('Features', '').split(),
                            depends)
                test.check_testbed_compat(testbed_caps)
                tests.append(test)
        except Unsupported as u:
            u.report()
            some_skipped = True

    return (tests, some_skipped)
