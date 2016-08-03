# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import os
import subprocess

import pkg_resources
import pytest

import reprotest

VERSION = pkg_resources.require('reprotest')[0].version

def check_return_code(command, virtual_server, code):
    try:
        reprotest.check(command, 'artifact', virtual_server, 'tests')
    except SystemExit as system_exit:
        assert(system_exit.args[0] == code)

@pytest.fixture(scope='module', params=['null' , 'qemu', 'schroot'])
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

@pytest.mark.parametrize('variation', reprotest.VARIATIONS)
def test_variations(virtual_server, variation):
    check_return_code('python3 mock_build.py ' + variation, virtual_server, 1)

def test_self_build(virtual_server):
    assert(subprocess.call(['reprotest', 'python3 setup.py bdist', 'dist/reprotest-' + VERSION + '.linux-x86_64.tar.gz'] + virtual_server) == 1)
    # setup.py complains there's no README.rst, README, or README.txt.
    # Why that's hard-coded, I have no idea.  This command eats the
    # error so the build doesn't crash.
    assert(subprocess.call(['reprotest', 'python3 setup.py sdist 2>/dev/null', 'dist/reprotest-' + VERSION + '.tar.gz'] + virtual_server) == 1)
    assert(subprocess.call(['reprotest', 'python3 setup.py bdist_wheel', 'dist/reprotest-' + VERSION + '-py3-none-any.whl'] + virtual_server) == 1)
    assert(subprocess.call(['reprotest', 'debuild -b -uc -us', '../reprotest_' + VERSION + '_all.deb'] + virtual_server) == 1)
