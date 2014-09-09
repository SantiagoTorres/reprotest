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

import string
import re
import errno
import os.path
import json
import subprocess
import tempfile
import atexit
import shutil

import debian.deb822
import debian.debian_support
import debian.debfile

import adtlog

#
# Abstract test representation
#

known_restrictions = ['rw-build-tree', 'breaks-testbed', 'needs-root',
                      'build-needed', 'allow-stderr', 'isolation-container',
                      'isolation-machine', 'needs-recommends']


class Unsupported(Exception):
    '''Test cannot be run in the testbed'''

    def __init__(self, testname, message):
        self.testname = testname
        self.message = message

    def __str__(self):
        return 'Unsupported test %s: %s' % (self.testname, self.message)

    def report(self):
        adtlog.report(self.testname, 'SKIP %s' % self.message)


class InvalidControl(Exception):
    '''Test has invalid control data'''

    def __init__(self, testname, message):
        self.testname = testname
        self.message = message

    def __str__(self):
        return 'InvalidControl test %s: %s' % (self.testname, self.message)

    def report(self):
        adtlog.report(self.testname, 'BROKEN %s' % self.message)


class Test:
    '''Test description.

    This is only a representation of the metadata, it does not have any
    actions.
    '''
    def __init__(self, name, path, command, restrictions, features, depends,
                 clicks):
        '''Create new test description

        A test must have either "path" or "command", the respective other value
        must be None.

        @name: Test name
        @path: path to the test's executable, relative to source tree
        @command: shell command for the test code
        @restrictions, @features: string lists, as in README.package-tests
        @depends: string list of test dependencies (packages)
        @clicks: path list of click packages to install for this test
        '''
        if '/' in name:
            raise Unsupported(name, 'test name may not contain / character')
        for r in restrictions:
            if r not in known_restrictions:
                raise Unsupported(name, 'unknown restriction %s' % r)

        if not ((path is None) ^ (command is None)):
            raise InvalidControl(name, 'Test must have either path or command')

        self.name = name
        self.path = path
        self.command = command
        self.restrictions = restrictions
        self.features = features
        self.depends = depends
        self.clicks = clicks
        # None while test hasn't run yet; True: pass, False: fail
        self.result = None
        adtlog.debug('Test defined: name %s path %s command "%s" '
                     'restrictions %s features %s depends %s clicks %s' %
                     (name, path, command, restrictions, features, depends,
                      clicks))

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
                deps.append(d + ' (>= 0~)')
        elif alt_group_str == '@builddeps@':
            for d in _debian_build_deps_from_source(srcdir):
                adtlog.debug('synthesised dependency %s' % d)
                deps.append(d)
        else:
            for dep in alt_group_str.split('|'):
                _debian_check_dep(testname, dep)
            deps.append(alt_group_str)

    return deps


def _auto_debian_control_ruby(srcdir, tests):
    '''Add automatic gem2deb test for Ruby packages'''

    if (any(map(lambda f: os.path.exists(os.path.join(srcdir, f)),
                ['debian/ruby-tests.rake', 'debian/ruby-tests.rb',
                 'debian/ruby-test-files.yaml']))):

        adtlog.info('Ruby package detected')

        command = 'gem2deb-test-runner --autopkgtest 2>&1'
        depends = _parse_debian_depends(command, '@, @builddeps@', srcdir)
        depends = [d for d in depends if not d.startswith('gem2deb')]
        depends.append('gem2deb-test-runner')
        tests.append(Test('auto-gem2deb', None, command, [], [],
                          depends, []))


def _auto_debian_control_perl(srcdir, tests):
    '''Add automatic test for Perl packages'''

    if (os.path.exists(os.path.join(srcdir, 't')) and
            (os.path.exists(os.path.join(srcdir, 'Makefile.PL')) or
             os.path.exists(os.path.join(srcdir, 'Build.PL')))):

        adtlog.info('Perl package detected')

        command = '/usr/share/pkg-perl-autopkgtest/runner build-deps'
        depends = _parse_debian_depends(command,
                                        '@, @builddeps@, pkg-perl-autopkgtest',
                                        srcdir)
        tests.append(Test('auto-build-deps', None, command, [], [],
                          depends, []))

        command = '/usr/share/pkg-perl-autopkgtest/runner runtime-deps'
        depends = _parse_debian_depends(command,
                                        '@, pkg-perl-autopkgtest',
                                        srcdir)
        tests.append(Test('auto-runtime-deps', None, command, [], [],
                          depends, []))


def _auto_debian_control(srcdir):
    '''Infer tests if there is no Debian test control file'''

    tests = []
    some_skipped = False

    _auto_debian_control_ruby(srcdir, tests)
    _auto_debian_control_perl(srcdir, tests)

    return (tests, some_skipped)


def parse_debian_source(srcdir, testbed_caps, control_path=None):
    '''Parse test descriptions from a Debian DEP-8 source dir

    You can specify an alternative path for the control file (default:
    debian/tests/control).

    Return (list of Test objects, some_skipped). If this encounters any invalid
    restrictions, fields, or test restrictions which cannot be met by the given
    testbed capabilities, the test will be skipped (and reported so), and not
    be included in the result.

    This may raise an InvalidControl exception.
    '''
    some_skipped = False
    command_counter = 0
    tests = []
    if control_path:
        control_path = os.path.join(srcdir, control_path)
    else:
        control_path = os.path.join(srcdir, 'debian', 'tests', 'control')

    if not os.path.exists(control_path):
        return _auto_debian_control(srcdir)

    for record in parse_rfc822(control_path):
        command = None
        try:
            restrictions = record.get('Restrictions', '').replace(
                ',', ' ').split()
            features = record.get('Features', '').replace(',', ' ').split()

            if 'Tests' in record:
                test_names = record['Tests'].replace(',', ' ').split()
                depends = _parse_debian_depends(test_names[0],
                                                record.get('Depends', '@'),
                                                srcdir)
                if 'Test-command' in record:
                    raise InvalidControl('*', 'Only one of "Tests" or '
                                         '"Test-Command" may be given')
                test_dir = record.get('Tests-directory', 'debian/tests')

                for n in test_names:
                    test = Test(n, os.path.join(test_dir, n), None,
                                restrictions, features, depends, [])
                    test.check_testbed_compat(testbed_caps)
                    tests.append(test)
            elif 'Test-command' in record:
                command = record['Test-command']
                depends = _parse_debian_depends(command,
                                                record.get('Depends', '@'),
                                                srcdir)
                command_counter += 1
                test = Test('command%i' % command_counter, None, command,
                            restrictions, features, depends, [])
                test.check_testbed_compat(testbed_caps)
                tests.append(test)
            else:
                raise InvalidControl('*', 'missing "Tests" or "Test-Command"'
                                     ' field')
        except Unsupported as u:
            u.report()
            some_skipped = True

    return (tests, some_skipped)


#
# Parsing for click packages
#

def parse_click_manifest(manifest, testbed_caps, clickdeps, srcdir=None):
    '''Parse test descriptions from a click manifest.

    @manifest: String with the click manifest
    @testbed_caps: List of testbed capabilities
    @clickdeps: paths of click packages that these tests need

    Return (source_dir, list of Test objects, some_skipped). If this encounters
    any invalid restrictions, fields, or test restrictions which cannot be met
    by the given testbed capabilities, the test will be skipped (and reported
    so), and not be included in the result.

    If srcdir is given, use that as source for the click package, and return
    that as first return value. Otherwise, locate and download the source from
    the click's manifest into a temporary directory and use that.

    This may raise an InvalidControl exception.
    '''
    try:
        manifest_j = json.loads(manifest)
        test_j = manifest_j.get('x-test', {})
    except ValueError as e:
        raise InvalidControl(
            '*', 'click manifest is not valid JSON: %s' % str(e))
    if not isinstance(test_j, dict):
        raise InvalidControl(
            '*', 'click manifest x-test key must be a dictionary')

    some_skipped = False
    tests = []

    # It's a dictionary and thus does not have a predictable ordering; sort it
    # to get a predictable list
    for name in sorted(test_j):
        desc = test_j[name]
        adtlog.debug('parsing click manifest test %s: %s' % (name, desc))

        # simple string is the same as { "path": <desc> } without any
        # restrictions, or the special "autopilot" case
        if isinstance(desc, str):
            if name == 'autopilot' and re.match('^[a-z_][a-z0-9_]+$', desc):
                desc = {'autopilot_module': desc}
            else:
                desc = {'path': desc}

        if not isinstance(desc, dict):
            raise InvalidControl(name, 'click manifest x-test dictionary '
                                 'entries must be strings or dicts')

        # autopilot special case: dict with extra depends
        if 'autopilot_module' in desc:
            desc['command'] = 'PYTHONPATH=tests/autopilot:$PYTHONPATH ' \
                'python3 -m autopilot.run run -v -f subunit -o ' \
                '$ADT_ARTIFACTS/%s.subunit ' % name + os.environ.get(
                    'ADT_AUTOPILOT_MODULE', desc['autopilot_module'])
            desc.setdefault('depends', []).insert(
                0, 'ubuntu-ui-toolkit-autopilot')
            desc['depends'].insert(0, 'autopilot-touch')
            if 'allow-stderr' not in desc.setdefault('restrictions', []):
                desc['restrictions'].append('allow-stderr')

        try:
            test = Test(name, desc.get('path'), desc.get('command'),
                        desc.get('restrictions', []), desc.get('features', []),
                        desc.get('depends', []), clickdeps)
            test.check_testbed_compat(testbed_caps)
            tests.append(test)
        except Unsupported as u:
            u.report()
            some_skipped = True

    if srcdir is None:
        # do we have an x-source/vcs-bzr link?
        if 'x-source' in manifest_j:
            try:
                repo = manifest_j['x-source']['vcs-bzr']
                adtlog.info('checking out click source from %s' % repo)
                d = tempfile.mkdtemp(prefix='adt.clicksrc.')
                atexit.register(shutil.rmtree, d, ignore_errors=True)
                try:
                    subprocess.check_call(['bzr', 'checkout', '--lightweight',
                                           repo, d])
                    srcdir = d
                except subprocess.CalledProcessError as e:
                    adtlog.error('Failed to check out click source from %s: %s'
                                 % (repo, str(e)))
            except KeyError:
                adtlog.error('Click source download from x-source only '
                             'supports "vcs-bzr" repositories')
        else:
            adtlog.error('cannot download click source: manifest does not '
                         'have "x-source"')

    return (srcdir, tests, some_skipped)


def parse_click(clickpath, testbed_caps, srcdir=None):
    '''Parse test descriptions from a click package.

    Return (source_dir, list of Test objects, some_skipped). If this encounters
    any invalid restrictions, fields, or test restrictions which cannot be met
    by the given testbed capabilities, the test will be skipped (and reported
    so), and not be included in the result.

    If srcdir is given, use that as source for the click package, and return
    that as first return value. Otherwise, locate and download the source from
    the click's manifest into a temporary directory and use that (not yet
    implemented).

    This may raise an InvalidControl exception.
    '''
    pkg = debian.debfile.DebFile(clickpath)
    try:
        manifest = pkg.control.get_content('manifest').decode('UTF-8')
    finally:
        pkg.close()

    return parse_click_manifest(manifest, testbed_caps, [clickpath], srcdir)
