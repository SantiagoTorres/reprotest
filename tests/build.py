# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import argparse
import locale
import os
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
        output.append(os.urandom(1024))
    if 'home' in args:
        output.append(os.path.expanduser('~').encode('ascii'))
    if 'locales' in args:
        # print(locale.getlocale())
        # print([l.encode('ascii') for l in locale.getlocale()])
        output.extend(l.encode('ascii') for l in locale.getlocale())
    if 'path' in args:
        output.extend(p.encode('ascii') for p in os.get_exec_path())
    if 'timezone' in args:
        output.append(time.ctime().encode('ascii'))
    with open('artifact', 'wb') as artifact:
        artifact.write(b''.join(output))
