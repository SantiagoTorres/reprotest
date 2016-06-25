# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import subprocess

import reprotest

def check_return_code(command, code):
    try:
        reprotest.check(command, 'artifact', ['null'], 'tests/')
    except SystemExit as system_exit:
        assert(system_exit.args[0] == code)

def test_check():
    check_return_code(['python', 'mock_build.py'], 0)
    # check_return_code(['python', 'mock_failure.py'], 2)
    check_return_code(['python', 'mock_build.py', 'irreproducible'], 1)
    check_return_code(['python', 'mock_build.py', 'fileordering'], 1)
    check_return_code(['python', 'mock_build.py', 'home'], 1)
    check_return_code(['python', 'mock_build.py', 'kernel'], 1)
    check_return_code(['python', 'mock_build.py', 'locales'], 1)
    check_return_code(['python', 'mock_build.py', 'path'], 1)
    check_return_code(['python', 'mock_build.py', 'timezone'], 1)
    # check_return_code(['python', 'mock_build.py', 'umask'], 1)

def test_self_build():
    assert(subprocess.call(['reprotest', 'python setup.py bdist', 'dist/reprotest-0.1.linux-x86_64.tar.gz', 'null']) == 1)
    assert(subprocess.call(['reprotest', 'debuild -b -uc -us', '../reprotest_0.1_all.deb', 'null']) == 1)
