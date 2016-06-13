# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import argparse
import collections
import configparser
import contextlib
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

# time zone, locales, disorderfs, host name, user/group, shell, CPU
# number, architecture for uname (using linux64), umask, HOME, see
# also: https://tests.reproducible-builds.org/index_variations.html

# chroot is the only form of OS virtualization that's available on
# most POSIX OSes.  Linux containers (lxc) and namespaces are specific
# to Linux.  Some versions of BSD has jails (MacOS X?).  There are a
# variety of other options including Docker etc that use different
# approaches.

# TODO: relies on a pbuilder-specific command to parallelize
# def cpu(command1, command2, env1, env2, tree1, tree2):
#     yield command1, command2, env1, env2, tree1, tree2

@contextlib.contextmanager
def captures_environment(command1, command2, env1, env2, tree1, tree2):
    env2['CAPTURE_ENVIRONMENT'] = 'i_capture_the_environment'
    yield command1, command2, env1, env2, tree1, tree2

# TODO: this requires superuser privileges.
@contextlib.contextmanager
def domain_host(command1, command2, env1, env2, tree1, tree2):
    yield command1, command2, env1, env2, tree1, tree2

@contextlib.contextmanager
def fileordering(command1, command2, env1, env2, tree1, tree2):
    disorderfs = tree2.parent/'disorderfs'
    disorderfs.mkdir()
    subprocess.check_call(['disorderfs', '--shuffle-dirents=yes',
                           str(tree2), str(disorderfs)])
    try:
        yield command1, command2, env1, env2, tree1, disorderfs
    finally:
        subprocess.check_call(['fusermount', '-u', str(disorderfs)])

@contextlib.contextmanager
def home(command1, command2, env1, env2, tree1, tree2):
    env1['HOME'] = '/nonexistent/first-build'
    env2['HOME'] = '/nonexistent/second-build'
    yield command1, command2, env1, env2, tree1, tree2

# TODO: uname is a POSIX standard.  The related Linux command
# (setarch) only affects uname at the moment according to the docs.
# FreeBSD changes uname with environment variables.  Wikipedia has a
# reference to a setname command: https://en.wikipedia.org/wiki/Uname
@contextlib.contextmanager
def kernel(command1, command2, env1, env2, tree1, tree2):
    command2 = ['linux64', '--uname-2.6'] + command2
    yield command1, command2, env1, env2, tree1, tree2

# TODO: if this locale doesn't exist on the system, Python's
# locales.getlocale() will return (None, None) rather than this
# locale.  I imagine it will also probably cause false positives with
# builds being reproducible when they aren't because of locale-based
# issues if this locale isn't installed.  The right solution here is
# for this locale to be encoded into the dependencies so installing it
# installs the right locale.  A weaker but still reasonable solution
# is to figure out what locales are installed (how?) and use another
# locale if this one isn't installed.

# TODO: what exact locales and how to many test is probably a mailing
# list question.
@contextlib.contextmanager
def locales(command1, command2, env1, env2, tree1, tree2):
    # env1['LANG'] = 'C'
    env2['LANG'] = 'fr_CH.UTF-8'
    # env1['LANGUAGE'] = 'en_US:en'
    # env2['LANGUAGE'] = 'fr_CH:fr'
    env2['LC_ALL'] = 'fr_CH.UTF-8'
    yield command1, command2, env1, env2, tree1, tree2

# TODO: Linux-specific.  unshare --uts requires superuser privileges.
# How is this related to host/domainname?
# def namespace(command1, command2, env1, env2, tree1, tree2):
#     # command1 = ['unshare', '--uts'] + command1
#     # command2 = ['unshare', '--uts'] + command2
#     yield command1, command2, env1, env2, tree1, tree2

@contextlib.contextmanager
def path(command1, command2, env1, env2, tree1, tree2):
    env2['PATH'] = env1['PATH'] + '/i_capture_the_path'
    yield command1, command2, env1, env2, tree1, tree2

# This doesn't require superuser privileges, but the chsh command
# affects all user shells, which would be bad.
@contextlib.contextmanager
def shell(command1, command2, env1, env2, tree1, tree2):
    yield command1, command2, env1, env2, tree1, tree2

@contextlib.contextmanager
def timezone(command1, command2, env1, env2, tree1, tree2):
    # These time zones are theoretically in the POSIX time zone format
    # (http://pubs.opengroup.org/onlinepubs/9699919799/basedefs/V1_chap08.html#tag_08),
    # so they should be cross-platform compatible.
    env1['TZ'] = 'GMT+12'
    env2['TZ'] = 'GMT-14'
    yield command1, command2, env1, env2, tree1, tree2

@contextlib.contextmanager
def umask(command1, command2, env1, env2, tree1, tree2):
    command2 = ['umask', '0002;'] + command2
    yield command1, command2, env1, env2, tree1, tree2

# TODO: This requires superuser privileges.
@contextlib.contextmanager
def user_group(command1, command2, env1, env2, tree1, tree2):
    yield command1, command2, env1, env2, tree1, tree2

VARIATIONS = collections.OrderedDict([
    ('captures_environment', captures_environment),
    # 'cpu': cpu,
    ('domain_host', domain_host), ('fileordering', fileordering),
    ('home', home), ('kernel', kernel), ('locales', locales),
    # 'namespace': namespace,
    ('path', path), ('shell', shell),
    ('timezone', timezone), ('umask', umask), ('user_group', user_group)
])

def build(command, source_root, built_artifact, artifact_store, **kws):
    # print(command)
    # print(source_root)
    # print(list(pathlib.Path(source_root).glob('*')))
    # print(kws)
    # print(subprocess.check_output(['ls'], cwd=source_root, **kws).decode('ascii'))
    # print(subprocess.check_output('python --version', cwd=source_root, **kws))
    subprocess.check_call(command, cwd=source_root, **kws)
    with open(built_artifact, 'rb') as artifact:
        artifact_store.write(artifact.read())
        artifact_store.flush()

def check(build_command, artifact_name, source_root, variations=VARIATIONS):
    with tempfile.TemporaryDirectory() as temp:
        command1 = build_command
        command2 = build_command
        env1 = os.environ.copy()
        env2 = env1.copy()
        tree1 = pathlib.Path(shutil.copytree(str(source_root), temp + '/tree1'))
        tree2 = pathlib.Path(shutil.copytree(str(source_root), temp + '/tree2'))
        # print(' '.join(command1))
        # print(pathlib.Path.cwd())
        # print(source_root)
        try:
            with contextlib.ExitStack() as stack:
                    for variation in variations:
                        # print(variation)
                        command1, command2, env1, env2, tree1, tree2 = stack.enter_context(VARIATIONS[variation](command1, command2, env1, env2, tree1, tree2))
                    # I would prefer to use pathlib here but
                    # .resolve(), to eliminate ../ references, doesn't
                    # work on nonexistent paths.
                    build(' '.join(command1), str(tree1),
                          os.path.normpath(temp + '/tree1/' + artifact_name),
                          open(os.path.normpath(temp + '/artifact1'), 'wb'),
                          env=env1, shell=True)
                    build(' '.join(command2), str(tree2),
                          os.path.normpath(temp + '/tree2/' + artifact_name),
                          open(os.path.normpath(temp + '/artifact2'), 'wb'),
                          env=env2, shell=True)
        except Exception:
            traceback.print_exc()
            sys.exit(2)
        sys.exit(subprocess.call(['diffoscope', temp + '/artifact1', temp + '/artifact2']))

def main():
    build_command = ''
    artifact = ''
    # If a source root isn't provided, assume it's the current
    # working directory.
    source_root = pathlib.Path.cwd()
    # The default is to try all variations.
    variations = frozenset(VARIATIONS.keys())

    # Config file.
    config = configparser.ConfigParser()
    config.read('.reprotestrc')
    if 'basics' in config:
        if 'variations' in config['basics']:
            variations = frozenset(config['basics'].split())
        if 'build_command' in config['basics']:
            build_command = config['build_command'].split()
        if 'artifact' in config['basics']:
            artifact = config['artifact']
        if 'source_root' in config['basics']:
            source_root = config['source_root']
        if 'verbosity' in config['basics']:
            verbosity = config['verbosity']

    # Command-line arguments override config file settings.
    arg_parser = argparse.ArgumentParser(
        description='Build packages and check them for reproducibility.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    arg_parser.add_argument(
        '-v', '--verbose', action='count', default=0, help='Verbose output.')
    arg_parser.add_argument('build_command', help='Build command to execute.')
    arg_parser.add_argument(
        'artifact', help='Build artifact to test for reproducibility.')
    # Reprotest will copy this tree and then run the build command.
    arg_parser.add_argument(
        '--source-root', dest='source_root', type=pathlib.Path,
        help='Root of the source tree, if not the '
        'current working directory.')
    arg_parser.add_argument(
        '--variations', type=lambda s: frozenset(s.split(',')),
        help='Build variations to test as a comma-separated list'
        ' (without spaces).  Default is to test all available variations.')
    arg_parser.add_argument(
        '--dont-vary', dest='dont_vary',
        type=lambda s: frozenset(s.split(',')),
        help='Build variations *not* to test as a comma-separated'
        ' list (without spaces).  Default is to test all available variations.')
    # Argparse exits with status code 2 if something goes wrong, which
    # is already the right status exit code for reprotest.

    args = arg_parser.parse_args()
    # print(args)
    if args.build_command:
        build_command = args.build_command.split()
        # print(build_command)
    if args.artifact:
        artifact = args.artifact
    if args.source_root:
        source_root = args.source_root
    if args.dont_vary and args.variations:
        print("Use only one of --variations or --dont_vary, not both.")
        sys.exit(2)
    elif args.dont_vary:
        variations = variations - args.dont_vary
    elif args.variations:
        variations = args.variations
    # Restore the order
    variations = [v for v in VARIATIONS if v in variations]

    if not build_command:
        print("No build command provided.")
        sys.exit(2)
    if not artifact:
        print("No build artifact to test for differences provided.")
        sys.exit(2)
    logging.basicConfig(
        format='%(message)s', level=30-10*args.verbose, stream=sys.stdout)
    check(build_command, artifact, source_root, variations)
