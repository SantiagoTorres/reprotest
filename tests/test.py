# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import os

import reprotest

def test_return_code(command, code):
    try:
        reprotest.check(command, 'tests/artifact')
    except SystemExit as system_exit:
        assert(system_exit.args[0] == code)

if __name__ == '__main__':
    try:
        test_return_code(['python', 'tests/build.py'], 0)
        test_return_code(['python', 'tests/fails.py'], 2)
        test_return_code(['python', 'tests/build.py', 'irreproducible'], 1)
        test_return_code(['python', 'tests/build.py', 'locales'], 1)
        test_return_code(['python', 'tests/build.py', 'timezone'], 1)
    finally:
        # Clean up random binary file created as part of the test.
        if os.path.isfile('tests/artifact'):
            os.remove('tests/artifact')
