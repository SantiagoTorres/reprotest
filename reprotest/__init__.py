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
import types

import pkg_resources

from reprotest.lib import adtlog
from reprotest.lib import adt_testbed
from reprotest import _shell_ast


adtlog.verbosity = 2


# chroot is the only form of OS virtualization that's available on
# most POSIX OSes.  Linux containers (lxc) and namespaces are specific
# to Linux.  Some versions of BSD have jails (MacOS X?).  There are a
# variety of other options including Docker etc that use different
# approaches.

@contextlib.contextmanager
def virtual_server(args, temp_dir):
    '''This is a simple wrapper around adt_testbed that automates the
    clean up.'''
    # Find the location of reprotest using setuptools and then get the
    # path for the correct virt-server script.
    server_path = pkg_resources.resource_filename(__name__, 'virt/' +
                                                  args[0])
    print('VIRTUAL SERVER', [server_path] + args[1:])
    virtual_server = adt_testbed.Testbed([server_path] + args[1:], temp_dir, None)
    virtual_server.start()
    virtual_server.open()
    try:
        yield virtual_server
    finally:
        virtual_server.stop()


# time zone, locales, disorderfs, host name, user/group, shell, CPU
# number, architecture for uname (using linux64), umask, HOME, see
# also: https://tests.reproducible-builds.org/index_variations.html

Pair = collections.namedtuple('Pair', 'control experiment')

def add(mapping, key, value):
    new_mapping = mapping.copy()
    new_mapping[key] = value
    return types.MappingProxyType(new_mapping)


# TODO: relies on a pbuilder-specific command to parallelize
# @contextlib.contextmanager
# def cpu(env, tree, builder):
#     yield script, env, tree

@contextlib.contextmanager
def captures_environment(script, env, tree, builder):
    new_env = add(env.experiment, 'CAPTURE_ENVIRONMENT',
                  'i_capture_the_environment')
    yield script, Pair(env.control, new_env), tree

# TODO: this requires superuser privileges.
@contextlib.contextmanager
def domain_host(script, env, tree, builder):
    yield script, env, tree

# @contextlib.contextmanager
# def fileordering(script, env, tree, builder):
#     new_tree = os.path.dirname(os.path.dirname(tree.control)) + '/disorderfs/'
#     builder.execute(['mkdir', '-p', new_tree])
#     # disorderfs = tree2.parent/'disorderfs'
#     # disorderfs.mkdir()
#     builder.execute(['disorderfs', '--shuffle-dirents=yes',
#                      tree.experiment, new_tree])
#     try:
#         yield script, env, Pair(tree.control, new_tree)
#     finally:
#         # subprocess.check_call(['fusermount', '-u', str(disorderfs)])
#         builder.execute(['fusermount', '-u', new_tree])

@contextlib.contextmanager
def fileordering(script, env, tree, builder):
    yield script, env, tree

@contextlib.contextmanager
def home(script, env, tree, builder):
    control = add(env.control, 'HOME', '/nonexistent/first-build')
    experiment = add(env.experiment, 'HOME', '/nonexistent/second-build')
    yield script, Pair(control, experiment), tree

# TODO: uname is a POSIX standard.  The related Linux command
# (setarch) only affects uname at the moment according to the docs.
# FreeBSD changes uname with environment variables.  Wikipedia has a
# reference to a setname command on another Unix variant:
# https://en.wikipedia.org/wiki/Uname
@contextlib.contextmanager
def kernel(script, env, tree, builder):
    setarch = _shell_ast.SimpleCommand(
        '', 'linux64', _shell_ast.CmdSuffix(
            ['--uname-2.6', script.experiment[0].command]))
    new_script = (script.experiment[:-1] +
                  _shell_ast.List([_shell_ast.Term(setarch, ';')]))
    yield Pair(script.control, new_script), env, tree

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
def locales(script, env, tree, builder):
    # env1['LANG'] = 'C'
    new_env = add(add(env.experiment, 'LANG', 'fr_CH.UTF-8'),
                  'LC_ALL', 'fr_CH.UTF-8')
    # env1['LANGUAGE'] = 'en_US:en'
    # env2['LANGUAGE'] = 'fr_CH:fr'
    yield script, Pair(env.control, new_env), tree

# TODO: Linux-specific.  unshare --uts requires superuser privileges.
# How is this related to host/domainname?
# def namespace(script, env, tree, builder):
#     # command1 = ['unshare', '--uts'] + command1
#     # command2 = ['unshare', '--uts'] + command2
#     yield script, env, tree

@contextlib.contextmanager
def path(script, env, tree, builder):
    new_env = add(env.experiment, 'PATH', env.control['PATH'] +
                  '/i_capture_the_path')
    yield script, Pair(env.control, new_env), tree

# This doesn't require superuser privileges, but the chsh command
# affects all user shells, which would be bad.
@contextlib.contextmanager
def shell(script, env, tree, builder):
    yield script, env, tree

@contextlib.contextmanager
def timezone(script, env, tree, builder):
    # These time zones are theoretically in the POSIX time zone format
    # (http://pubs.opengroup.org/onlinepubs/9699919799/basedefs/V1_chap08.html#tag_08),
    # so they should be cross-platform compatible.
    control = add(env.control, 'TZ', 'GMT+12')
    experiment = add(env.experiment, 'TZ', 'GMT-14')
    yield script, Pair(control, experiment), tree

@contextlib.contextmanager
def umask(script, env, tree, builder):
    umask = _shell_ast.SimpleCommand('', 'umask', _shell_ast.CmdSuffix(['0002']))
    new_script = (_shell_ast.List([_shell_ast.Term(umask, ';')])
                  + script.experiment)
    yield Pair(script.control, new_script), env, tree

# TODO: This requires superuser privileges.
@contextlib.contextmanager
def user_group(script, env, tree, builder):
    yield script, env, tree


# The order of the variations *is* important, because the command to
# be executed in the container needs to be built from the inside out.
VARIATIONS = types.MappingProxyType(collections.OrderedDict([
    ('captures_environment', captures_environment),
    # 'cpu': cpu,
    ('domain_host', domain_host), ('fileordering', fileordering),
    ('home', home), ('kernel', kernel), ('locales', locales),
    # 'namespace': namespace,
    ('path', path), ('shell', shell),
    ('timezone', timezone), ('umask', umask),
    ('user_group', user_group)
]))


def build(script, source_root, built_artifact, builder, artifact_store, env):
    print(source_root)
    builder.execute(['ls', '-l', source_root])
    # builder.execute(['pwd'])
    print(built_artifact)
    cd = _shell_ast.SimpleCommand('', 'cd', _shell_ast.CmdSuffix([source_root]))
    new_script = (_shell_ast.List([_shell_ast.Term(cd, ';')]) + script)
    print(new_script)
    exit_code, stdout, stderr = builder.execute(['sh', '-ec', str(new_script)], xenv=[str(k) + '=' + str(v) for k, v in env.items()], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    print(exit_code, stdout, stderr)
    builder.execute(['ls', '-l', source_root])
    # subprocess.check_call(script, cwd=source_root, **kws)
    # with open(built_artifact, 'rb') as artifact:
    #     artifact_store.write(artifact.read())
    #     artifact_store.flush()
    builder.command('copyup', (built_artifact, artifact_store))


def check(build_command, artifact_name, virtual_server_args, source_root,
          variations=VARIATIONS):
    # print(virtual_server_args)
    with tempfile.TemporaryDirectory() as temp_dir, virtual_server(virtual_server_args, temp_dir) as builder:
        ast = _shell_ast.List(
            [_shell_ast.Term(build_command, ';')])
        script = Pair(ast, ast)
        env = Pair(types.MappingProxyType(os.environ.copy()),
                   types.MappingProxyType(os.environ.copy()))
        # TODO, why?: directories need explicit '/' appended for VirtSubproc
        tree = Pair(builder.scratch + '/control/', builder.scratch + '/experiment/')
        builder.command('copydown', (str(source_root) + '/', tree.control))
        builder.command('copydown', (str(source_root) + '/', tree.experiment))
        # print(pathlib.Path.cwd())
        # print(source_root)
        try:
            with contextlib.ExitStack() as stack:
                for variation in variations:
                    # print('START')
                    # print(variation)
                    script, env, tree = stack.enter_context(VARIATIONS[variation](script, env, tree, builder))
                    # print(script)
                    # print(env)
                    # print(tree)
                # I would prefer to use pathlib here but
                # .resolve(), to eliminate ../ references, doesn't
                # work on nonexistent paths.
                # print(env)
                build(script.control, tree.control,
                      os.path.normpath(tree.control + artifact_name),
                      builder,
                      os.path.normpath(temp_dir + '/control_artifact'),
                      env=env.control)
                build(script.experiment, tree.experiment,
                      os.path.normpath(tree.experiment + artifact_name),
                      builder,
                      os.path.normpath(temp_dir + '/experiment_artifact'),
                      env=env.experiment)
        except Exception:
            traceback.print_exc()
            sys.exit(2)
        sys.exit(subprocess.call(['diffoscope', temp_dir + '/control_artifact', temp_dir + '/experiment_artifact']))


COMMAND_LINE_OPTIONS = types.MappingProxyType(collections.OrderedDict([
    ('build_command', types.MappingProxyType({
        'default': None, 'nargs': '?', # 'type': str.split
        'help': 'Build command to execute.'})),
    ('artifact', types.MappingProxyType({
        'default': None, 'nargs': '?',
        'help': 'Build artifact to test for reproducibility.'})),
    ('virtual_server_args', types.MappingProxyType({
        'default': None, 'nargs': '*',
        'help': 'Arguments to pass to the virtual_server.'})),
    ('--source-root', types.MappingProxyType({
        'dest': 'source_root', 'type': pathlib.Path,
        'help': 'Root of the source tree, if not the '
        'current working directory.'})),
    ('--variations', types.MappingProxyType({
        'type': lambda s: frozenset(s.split(',')),
        'help': 'Build variations to test as a comma-separated list'
        ' (without spaces).  Default is to test all available '
        'variations.'})),
    ('--dont-vary', types.MappingProxyType({
        'dest': 'dont_vary', 'type': lambda s: frozenset(s.split(',')),
        'help': 'Build variations *not* to test as a comma-separated'
        ' list (without spaces).  Default is to test all available '
        ' variations.'})),
    ('--verbosity', types.MappingProxyType({
        'type': int, 'default': 0,
        'help': 'An integer.  Control which messages are displayed.'}))
    ]))

MULTIPLET_OPTIONS = frozenset(['build_command', 'dont_vary',
                               'variations', 'virtual_server_args'])

CONFIG_OPTIONS = []
for option in COMMAND_LINE_OPTIONS.keys():
    if 'dest' in COMMAND_LINE_OPTIONS[option]:
        CONFIG_OPTIONS.append(COMMAND_LINE_OPTIONS[option]['dest'])
    else:
        CONFIG_OPTIONS.append(option.strip('-'))
CONFIG_OPTIONS = tuple(CONFIG_OPTIONS)

def config():
    # Config file.
    config = configparser.ConfigParser()
    config.read('.reprotestrc')
    options = collections.OrderedDict()
    if 'basics' in config:
        for option in CONFIG_OPTIONS:
            if option in config['basics'] and option in MULTIPLET_OPTIONS:
                options[option] = config['basics'][option].split()
            else:
                options[option] = config['basics'][option]
    return types.MappingProxyType(options)

def command_line():
    arg_parser = argparse.ArgumentParser(
        description='Build packages and check them for reproducibility.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    for option in COMMAND_LINE_OPTIONS:
        arg_parser.add_argument(option, **COMMAND_LINE_OPTIONS[option])
    args = arg_parser.parse_args()
    # print(args)

    return types.MappingProxyType({k:v for k, v in vars(args).items() if v is not None})

        
def main():
    config_options = config()
    
    # Argparse exits with status code 2 if something goes wrong, which
    # is already the right status exit code for reprotest.
    command_line_options = command_line()

    # Command-line arguments override config file settings.
    build_command = command_line_options.get(
        'build_command',
        config_options.get('build_command'))
    artifact = command_line_options.get(
        'artifact',
        config_options.get('artifact'))
    virtual_server_args = command_line_options.get(
        'virtual_server_args',
        config_options.get('virtual_server_args'))
    # Reprotest will copy this tree and then run the build command.
    # If a source root isn't provided, assume it's the current working
    # directory.
    source_root = command_line_options.get(
        'source_root',
        config_options.get('source_root', pathlib.Path.cwd()))
    # The default is to try all variations.
    variations = frozenset(VARIATIONS.keys())
    if 'variations' in config_options:
        variations = frozenset(config_options['variations'])
    if 'dont_vary' in config_options:
        variations = variations - frozenset(config_options['dont_vary'])
    if 'variations' in command_line_options:
        variations = command_line_options['variations']
    if 'dont_vary' in command_line_options:
        variations = variations - frozenset(command_line_options['dont_vary'])
    # Restore the order
    variations = [v for v in VARIATIONS if v in variations]
    verbosity = command_line_options.get(
        'verbosity',
        config_options.get('verbosity', 0))

    if not build_command:
        print("No build command provided.")
        sys.exit(2)
    if not artifact:
        print("No build artifact to test for differences provided.")
        sys.exit(2)
    if not virtual_server_args:
        print("No virtual_server to run the build in specified.")
        sys.exit(2)
    logging.basicConfig(
        format='%(message)s', level=30-10*verbosity, stream=sys.stdout)

    # print(build_command, artifact, virtual_server_args)
    check(build_command, artifact, virtual_server_args, source_root, variations)
