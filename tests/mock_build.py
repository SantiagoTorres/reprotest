# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import argparse
import locale
import os
import pathlib
import stat
import subprocess
import tempfile
import time

if __name__ == '__main__':
    # print(os.environ)
    arg_parser = argparse.ArgumentParser(
        description='Create binaries for testing reproducibility.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    arg_parser.add_argument('variations', nargs='*',
                            help='Reproducibility properties.')
    variations = set(arg_parser.parse_args().variations)
    output = [b'']
    # This test can theoretically fail by producing the same
    # random bits in both runs, but it is extremely unlikely.
    if 'irreproducible' in variations:
        output.append(os.urandom(1024))
    if 'build_path' in variations:
        output.append(os.getcwd().encode('ascii'))
    if 'captures_environment' in variations:
        output.append(str(os.environ).encode('ascii'))
    # Like the above test, this test can theoretically fail by
    # producing the same file order, but this is unlikely, if not
    # as unlikely as in the above test.
    if 'file_ordering' in variations:
        # Ensure this temporary directory is created in the disorders
        # mount point by passing the dir argument.
        with tempfile.TemporaryDirectory(dir=str(pathlib.Path.cwd())) as temp:
            test_file_order = pathlib.Path(temp)
            for i in range(20):
                str((test_file_order/str(i)).touch())
            output.extend(p.name.encode('ascii') for p in test_file_order.iterdir())
    if 'home' in variations:
        output.append(os.path.expanduser('~').encode('ascii'))
    if 'kernel' in variations:
        output.append(subprocess.check_output(['uname', '-r']))
    if 'locales' in variations:
        output.extend(l.encode('ascii') for l in locale.getlocale())
    if 'path' in variations:
        output.extend(p.encode('ascii') for p in os.get_exec_path())
    if 'time_zone' in variations:
        output.append(str(time.timezone).encode('ascii'))
    if 'umask' in variations:
        with tempfile.TemporaryDirectory(dir=str(pathlib.Path.cwd())) as temp:
            test_permissions = pathlib.Path(temp)/'test_permissions'
            test_permissions.touch()
            output.append(stat.filemode(test_permissions.stat().st_mode).encode('ascii'))
    with open('artifact', 'wb') as artifact:
        artifact.write(b''.join(output))
