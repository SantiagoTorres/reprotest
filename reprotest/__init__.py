# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import argparse
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

# time zone, locales, disorderfs, host name, user/group, shell, CPU
# number, architecture for uname (using linux64), umask, HOME, see
# also: https://tests.reproducible-builds.org/index_variations.html

def cpu(command, env1, env2, tree1, tree2):
    return command, env1, env2, tree1, tree2

def domain(command, env1, env2, tree1, tree2):
    return command, env1, env2, tree1, tree2

def captures_environment(command, env1, env2, tree1, tree2):
    env2['CAPTURE_ENVIRONMENT'] = 'i_capture_the_environment'
    return command, env1, env2, tree1, tree2

def filesystem(command, env1, env2, tree1, tree2):
    return command, env1, env2, tree1, tree2

def group(command, env1, env2, tree1, tree2):
    return command, env1, env2, tree1, tree2

def home(command, env1, env2, tree1, tree2):
    env1['HOME'] = '/nonexistent/first-build'
    env2['HOME'] = '/nonexistent/second-build'
    return command, env1, env2, tree1, tree2

def host(command, env1, env2, tree1, tree2):
    return command, env1, env2, tree1, tree2

def kernel(command, env1, env2, tree1, tree2):
    return command, env1, env2, tree1, tree2

# TODO: if this locale doesn't exist on the system, Python's
# locales.getlocale() will return (None, None) rather than this
# locale.  I imagine it will also probably cause false positives with
# builds being reproducible when they aren't because of locale-based
# issues if this locale isn't installed.  The right solution here is
# for this locale to be encoded into the dependencies so installing it
# installs the right locale.  A weaker but still reasonable solution
# is to figure out what locales are installed (how?) and use another
# locale if this one isn't installed.

def locales(command, env1, env2, tree1, tree2):
    # env1['LANG'] = 'C'
    env2['LANG'] = 'fr_CH.UTF-8'
    # env1['LANGUAGE'] = 'en_US:en'
    # env2['LANGUAGE'] = 'fr_CH:fr'
    env2['LC_ALL'] = 'fr_CH.UTF-8'
    return command, env1, env2, tree1, tree2

def namespace(command, env1, env2, tree1, tree2):
    return command, env1, env2, tree1, tree2

def path(command, env1, env2, tree1, tree2):
    env2['PATH'] = env1['PATH'] + 'i_capture_the_path'
    return command, env1, env2, tree1, tree2

def shell(command, env1, env2, tree1, tree2):
    return command, env1, env2, tree1, tree2

def time(command, env1, env2, tree1, tree2):
    return command, env1, env2, tree1, tree2

def timezone(command, env1, env2, tree1, tree2):
    # These time zones are theoretically in the POSIX time zone format
    # (http://pubs.opengroup.org/onlinepubs/9699919799/basedefs/V1_chap08.html#tag_08),
    # so they should be cross-platform compatible.
    env1['TZ'] = 'GMT+12'
    env2['TZ'] = 'GMT-14'
    return command, env1, env2, tree1, tree2

def umask(command, env1, env2, tree1, tree2):
    return command, env1, env2, tree1, tree2

def user(command, env1, env2, tree1, tree2):
    return command, env1, env2, tree1, tree2

VARIATIONS = {'captures_environment': captures_environment, 'cpu':
              cpu, 'domain': domain, 'filesystem': filesystem, 'group': group,
              'home': home, 'host': host, 'kernel': kernel, 'locales': locales,
              'namespace': namespace, 'path': path, 'shell': shell, 'time': time,
              'timezone': timezone, 'umask': umask, 'user': user}

def build(command, source_root, built_artifact, artifact_store, **kws):
    return_code = subprocess.call(command, cwd=source_root, **kws)
    if return_code != 0:
        sys.exit(2)
    else:
        with open(built_artifact, 'rb') as artifact:
            artifact_store.write(artifact.read())
            artifact_store.flush()

def check(build_command, source_root, artifact_name, variations=VARIATIONS):
    with tempfile.TemporaryDirectory() as temp:
        env1 = os.environ.copy()
        env2 = env1.copy()
        tree1 = shutil.copytree(str(source_root), temp + '/tree1')
        tree2 = shutil.copytree(str(source_root), temp + '/tree2')
        for variation in variations:
            build_command, env1, env2, tree1, tree2 = VARIATIONS[variation](build_command, env1, env2, tree1, tree2)
        build(build_command, tree1, temp + '/tree1/' + artifact_name,
              open(temp + '/artifact1', 'wb'), env=env1)
        build(build_command, tree2, temp + '/tree2/' + artifact_name,
              open(temp + '/artifact2', 'wb'), env=env2)
        sys.exit(subprocess.call(['diffoscope', temp + '/artifact1', temp + '/artifact2']))

def main():
    arg_parser = argparse.ArgumentParser(
        description='Build packages and check them for reproducibility.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    arg_parser.add_argument(
        '-v', '--verbose', action='count', default=0, help='Verbose output.')
    arg_parser.add_argument('build_command', help='Build command to execute.')
    arg_parser.add_argument(
        'artifact', help='Build artifact to test for reproducibility.')
    # Reprotest will copy this tree and then run the build command.
    arg_parser.add_argument('--source_root', type=pathlib.Path,
                           help='Root of the source tree, if not the'
                           'current working directory.')
    arg_parser.add_argument(
        '--variations', help='Build variations to test as a comma-separated list'
        '(without spaces).  Default is to test all available variations.')
    arg_parser.add_argument(
        '--dont_vary', help='Build variations *not* to test as a comma-separated'
        'list (without spaces).  Default is to test all available variations.')
    # Argparse exits with status code 2 if something goes wrong, which
    # is already the right status exit code for reprotest.
    args = arg_parser.parse_args()
    logging.basicConfig(
        format='%(message)s', level=30-10*args.verbose, stream=sys.stdout)
    variations = VARIATIONS
    if args.dont_vary and args.variations:
        print("Use only one of --variations or --dont_vary, not both.")
        sys.exit(2)
    elif args.dont_vary:
        variations = variations - args.dont_vary
    elif args.variations:
        variations = args.variations
    check(args.build_command.split(),
          # If a source root isn't provided, assume it's the current
          # working directory.
          args.source_root if args.source_root else pathlib.Path.cwd(),
          args.artifact,
          variations)
