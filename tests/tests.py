# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import os
import subprocess
import sys

import pytest
import reprotest

REPROTEST = [sys.executable, "-m", "reprotest"]

def check_return_code(command, virtual_server, code):
    try:
        retcode = reprotest.check(command, 'artifact', virtual_server, 'tests')
        assert(code == retcode)
    except SystemExit as system_exit:
        assert(system_exit.args[0] == code)

REPROTEST_TEST_SERVERS = os.getenv("REPROTEST_TEST_SERVERS", "null").split(",")
@pytest.fixture(scope='module', params=REPROTEST_TEST_SERVERS)
def virtual_server(request):
    if request.param == 'null':
        return [request.param]
    elif request.param == 'schroot':
        return [request.param, 'stable-amd64']
    elif request.param == 'qemu':
        return [request.param, os.path.expanduser('~/linux/reproducible_builds/adt-sid.img')]
    else:
        raise ValueError(request.param)

def test_simple_builds(virtual_server):
    # mock_build is not expected to reproduce when disorderfs is active, though
    # we should probably change "1" to int(is_disorderfs_active)
    check_return_code('python3 mock_build.py', virtual_server, 1)
    check_return_code('python3 mock_failure.py', virtual_server, 2)
    check_return_code('python3 mock_build.py irreproducible', virtual_server, 1)

@pytest.mark.parametrize('variation', ['fileordering', 'home', 'kernel', 'locales', 'path', 'timezone', 'umask'])
def test_variations(virtual_server, variation):
    check_return_code('python3 mock_build.py ' + variation, virtual_server, 1)

def test_self_build(virtual_server):
    assert(1 == subprocess.call(REPROTEST + ['python3 setup.py bdist', 'dist/*.tar.gz'] + virtual_server))
    # at time of writing (2016-09-23) these are not expected to reproduce;
    # strip-nondeterminism normalises them for Debian
    assert(1 == subprocess.call(REPROTEST + ['python3 setup.py sdist 2>/dev/null', 'dist/*.tar.gz'] + virtual_server))
    assert(1 == subprocess.call(REPROTEST + ['python3 setup.py bdist_wheel', 'dist/*.whl'] + virtual_server))

# TODO: don't call it if we don't have debian/, e.g. for other distros
def test_debian_build(virtual_server):
    # This is a bit dirty though it works - when building the debian package,
    # debian/rules will call this, which will call debian/rules, so ../*.deb
    # gets written twice and the second one is the "real" one, but since it
    # should all be reproducible, this should be OK.
    assert(0 == subprocess.call(
        REPROTEST + ['debuild -b -uc -us', '../*.deb'] + virtual_server,
        # "nocheck" to stop tests recursing into themselves
        env=dict(list(os.environ.items()) + [("DEB_BUILD_OPTIONS", "nocheck")])))
