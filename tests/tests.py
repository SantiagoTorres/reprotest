# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import subprocess

import reprotest

def test_return_code(command, code):
    try:
        reprotest.check(command, 'artifact', 'tests/')
    except SystemExit as system_exit:
        assert(system_exit.args[0] == code)

if __name__ == '__main__':
    test_return_code(['python', 'build.py'], 0)
    test_return_code(['python', 'fails.py'], 2)
    test_return_code(['python', 'build.py', 'irreproducible'], 1)
    test_return_code(['python', 'build.py', 'fileordering'], 1)
    test_return_code(['python', 'build.py', 'home'], 1)
    test_return_code(['python', 'build.py', 'kernel'], 1)
    test_return_code(['python', 'build.py', 'locales'], 1)
    test_return_code(['python', 'build.py', 'path'], 1)
    test_return_code(['python', 'build.py', 'timezone'], 1)
    test_return_code(['python', 'build.py', 'umask'], 1)

    assert(subprocess.call(['reprotest', 'python setup.py bdist', 'dist/reprotest-0.1.linux-x86_64.tar.gz']) == 1)
    assert(subprocess.call(['reprotest', 'debuild -b -uc -us', '../reprotest_0.1_all.deb']) == 1)
