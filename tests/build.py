# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import argparse
import locale
import os
import pathlib
import subprocess
import tarfile
import tempfile
import time

if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser(
        description='Create binaries for testing reproducibility.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    arg_parser.add_argument('commands', nargs='*',
                            help='Reproducibility properties.')
    args = set(arg_parser.parse_args().commands)
    output = [b'']
    if 'irreproducible' in args:
        # This test can theoretically fail by producing the same
        # random bits in both runs, but it is extremely unlikely.
        output.append(os.urandom(1024))
    if 'home' in args:
        output.append(os.path.expanduser('~').encode('ascii'))
    if 'kernel' in args:
        output.append(subprocess.check_output(['uname', '-r']))
    if 'locales' in args:
        # print(locale.getlocale())
        # print([l.encode('ascii') for l in locale.getlocale()])
        output.extend(l.encode('ascii') for l in locale.getlocale())
    if 'path' in args:
        output.extend(p.encode('ascii') for p in os.get_exec_path())
    if 'timezone' in args:
        output.append(str(time.timezone).encode('ascii'))
    if 'umask' in args:
        test_permissions = pathlib.Path.cwd() / 'test_permissions'
        test_permissions.touch()
        with tempfile.TemporaryFile() as temp:
            archive = tarfile.open(name='temp', mode='w', fileobj=temp)
            archive.add(str(test_permissions))
            temp.seek(0)
            output.append(temp.read())
    with open('artifact', 'wb') as artifact:
        artifact.write(b''.join(output))
