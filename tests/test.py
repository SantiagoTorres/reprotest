# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import os

import reprotest

def test_return_code(command, artifact, code):
    try:
        reprotest.check(command, artifact)
    except SystemExit as system_exit:
        assert(system_exit.args[0] == code)
    

if __name__ == '__main__':
    test_return_code(['python', 'tests/dummy_build.py'],
                     'tests/dummy_artifact.txt', 0)
    test_return_code(['python', 'tests/fails.py'], '', 2)
    test_return_code(['python', 'tests/irreproducible.py'],
                     'tests/irreproducible_artifact', 1)
    os.remove('tests/irreproducible_artifact')
