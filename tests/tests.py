# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import os
import subprocess
import sys

import pytest
import reprotest

REPROTEST = [sys.executable, "-m", "reprotest", "--no-diffoscope"]
REPROTEST_TEST_SERVERS = os.getenv("REPROTEST_TEST_SERVERS", "null").split(",")
REPROTEST_TEST_DONTVARY = os.getenv("REPROTEST_TEST_DONTVARY", "").split(",")

if REPROTEST_TEST_DONTVARY:
    REPROTEST += ["--dont-vary", ",".join(REPROTEST_TEST_DONTVARY)]

TEST_VARIATIONS = frozenset(reprotest.VARIATIONS.keys()) - frozenset(REPROTEST_TEST_DONTVARY)

def check_return_code(command, virtual_server, code):
    try:
        retcode = reprotest.check(command, 'artifact', virtual_server, 'tests', variations=TEST_VARIATIONS)
    except SystemExit as system_exit:
        retcode = system_exit.args[0]
    finally:
        if isinstance(code, int):
            assert(retcode == code)
        else:
            assert(retcode in code)

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
    check_return_code('python3 mock_build.py', virtual_server, 0)
    check_return_code('python3 mock_failure.py', virtual_server, 2)
    check_return_code('python3 mock_build.py irreproducible', virtual_server, 1)

# TODO: test all variations that we support
@pytest.mark.parametrize('captures', list(reprotest.VARIATIONS.keys()))
def test_variations(virtual_server, captures):
    expected = 1 if captures in TEST_VARIATIONS else 0
    check_return_code('python3 mock_build.py ' + captures, virtual_server, expected)

def test_self_build(virtual_server):
    # at time of writing (2016-09-23) these are not expected to reproduce;
    # if these start failing then you should change 1 == to 0 == but please
    # figure out which version of setuptools made things reproduce and add a
    # versioned dependency on that one
    assert(1 == subprocess.call(REPROTEST + ['python3 setup.py bdist', 'dist/*.tar.gz'] + virtual_server))
    assert(1 == subprocess.call(REPROTEST + ['python3 setup.py sdist; sleep 2', 'dist/*.tar.gz'] + virtual_server))
    assert(1 == subprocess.call(REPROTEST + ['python3 setup.py bdist_wheel', 'dist/*.whl'] + virtual_server))

# TODO: don't call it if we don't have debian/, e.g. for other distros
def test_debian_build(virtual_server):
    # This is a bit dirty though it works - when building the debian package,
    # debian/rules will call this, which will call debian/rules, so ../*.deb
    # gets written twice and the second one is the "real" one, but since it
    # should all be reproducible, this should be OK.
    assert(0 == subprocess.call(
        REPROTEST + ['debuild -b -nc -uc -us', '../*.deb'] + virtual_server,
        # "nocheck" to stop tests recursing into themselves
        env=dict(list(os.environ.items()) + [("DEB_BUILD_OPTIONS", "nocheck")])))
