# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

# time zone, locales, disorderfs, host name, user/group, shell, CPU
# number, architecture for uname (using linux64), umask, HOME, see
# also: https://tests.reproducible-builds.org/index_variations.html

# TODO: if this locale doesn't exist on the system, Python's
# locales.getlocale() will return (None, None) rather than this
# locale.  I imagine it will also probably cause false positives with
# builds being reproducible when they aren't because of locale-based
# issues if this locale isn't installed.  The right solution here is
# for this locale to be encoded into the dependencies so installing it
# installs the right locale.  A weaker but still reasonable solution
# is to figure out what locales are installed (how?) and use another
# locale if this one isn't installed.

# These time zones are theoretically in the POSIX time zone format
# (http://pubs.opengroup.org/onlinepubs/9699919799/basedefs/V1_chap08.html#tag_08),
# so they should be cross-platform compatible.

ENVIRONMENT_VARIABLES1 = {'TZ': 'GMT+12'}

ENVIRONMENT_VARIABLES2 = {'TZ': 'GMT-14', 'LANG': 'fr_CH.UTF-8', 'LC_ALL': 'fr_CH.UTF-8'}

def build(command, source_root, built_artifact, artifact_store, **kws):
    return_code = subprocess.call(command, cwd=source_root, **kws)
    if return_code != 0:
        sys.exit(2)
    else:
        with open(built_artifact, 'rb') as artifact:
            artifact_store.write(artifact.read())
            artifact_store.flush()

def check(build_command, source_root, artifact_name):
    with tempfile.TemporaryDirectory() as temp:
        shutil.copytree(str(source_root), temp + '/tree1')
        shutil.copytree(str(source_root), temp + '/tree2')
        env = os.environ.copy()
        # print(env)
        env.update(ENVIRONMENT_VARIABLES1)
        # print(env)
        build(build_command, temp + '/tree1', temp + '/tree1/' + artifact_name,
              open(temp + '/artifact1', 'wb'), env=env)
        env.update(ENVIRONMENT_VARIABLES2)
        # print(env)
        build(build_command, temp + '/tree2', temp + '/tree2/' + artifact_name,
              open(temp + '/artifact2', 'wb'), env=env)
        sys.exit(subprocess.call(['diffoscope', temp + '/artifact1', temp + '/artifact2']))

def main():
    arg_parser = argparse.ArgumentParser(
        description='Build packages and check them for reproducibility.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    arg_parser.add_argument('build_command', help='Build command to execute.')
    arg_parser.add_argument(
        'artifact', help='Build artifact to test for reproducibility.')
    # Reprotest will copy this tree and then run the build command.
    arg_parser.add_argument('--source_root', type=pathlib.Path,
                           help='Root of the source tree, if not the'
                           'current working directory.')
    arg_parser.add_arguments(
        '--variations', help='Build variations to test as a comma-separated list'
        '(without spaces).  Default is to test all available variations.')
    # Argparse exits with status code 2 if something goes wrong, which
    # is already the right status exit code for reprotest.
    args = arg_parser.parse_args()
    check(args.build_command.split(),
          # If a source root isn't provided, assume it's the current
          # working directory.
          args.source_root if args.source_root else pathlib.Path.cwd(),
          args.artifact)
