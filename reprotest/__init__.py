# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import argparse
import collections
import configparser
import logging
import os
import pathlib
import subprocess
import sys
import tempfile
import traceback
import types

import pkg_resources

from reprotest.lib import adtlog
from reprotest.lib import adt_testbed
from reprotest import _contextlib
from reprotest import _shell_ast


adtlog.verbosity = 1


# chroot is the only form of OS virtualization that's available on
# most POSIX OSes.  Linux containers (lxc) and namespaces are specific
# to Linux.  Some versions of BSD have jails (MacOS X?).  There are a
# variety of other options including Docker etc that use different
# approaches.

@_contextlib.contextmanager
def start_testbed(args, temp_dir):
    '''This is a simple wrapper around adt_testbed that automates the
    initialization and cleanup.'''
    # Find the location of reprotest using setuptools and then get the
    # path for the correct virt-server script.
    server_path = pkg_resources.resource_filename(__name__, 'virt/' +
                                                  args[0])
    print('VIRTUAL SERVER', [server_path] + args[1:])
    testbed = adt_testbed.Testbed([server_path] + args[1:], temp_dir, None)
    testbed.start()
    testbed.open()
    try:
        yield testbed
    finally:
        testbed.stop()


Pair = collections.namedtuple('Pair', 'control experiment')
Pair.__doc__ = ('Holds one object for each run of the build process.'
                + Pair.__doc__)

def add(mapping, key, value):
    '''Helper function for adding a key-value pair to an immutable mapping.'''
    new_mapping = mapping.copy()
    new_mapping[key] = value
    return types.MappingProxyType(new_mapping)

class Script(collections.namedtuple('_Script', 'build_command setup cleanup')):
    '''Holds the shell ASTs used to construct the final build script.

    Args:
        build_command (_shell_ast.Command): The build command itself, including
            all commands that accept other commands as arguments.  Examples:
            setarch.
        setup (_shell_ast.AndList): These are shell commands that change the
            shell environment and need to be run as part of the same script as
            the main build command but don't take other commands as arguments.  
            These execute conditionally because if one command fails,
            the whole script should fail.  Examples: cd, umask.
        cleanup (_shell_ast.List): All commands that have to be run to return
            the testbed to its initial state, before the testbed does its own
            cleanup.  These are executed only if the build command fails,
            because otherwise the cleanup has to occur after the build artifact
            is copied out.  These execution unconditionally, one after another,
            because all cleanup commands should be attempted irrespective of
            whether others succeed.  Examples: fileordering.
    '''

    def __new__(cls, build_command, setup=_shell_ast.AndList(),
                cleanup=_shell_ast.List()):
        return super().__new__(cls, build_command, setup, cleanup)

    def append_command(self, command):
        '''Passes the current build command as the last argument to a given
        _shell_ast.SimpleCommand.

        '''
        new_suffix = (command.cmd_suffix +
                      _shell_ast.CmdSuffix([self.build_command]))
        new_command = _shell_ast.SimpleCommand(command.cmd_prefix,
                                               command.cmd_name,
                                               new_suffix)
        return self._replace(build_command=new_command)

    def append_setup(self, command):
        '''Adds a command to the setup phase.

        '''
        new_setup = self.setup + _shell_ast.AndList([command])
        return self._replace(setup=new_setup)

    def append_cleanup(self, command):
        '''Adds a command to the cleanup phase.

        '''
        new_cleanup = (self.cleanup +
                       _shell_ast.List([_shell_ast.Term(command, ';')]))
        return self._replace(cleanup=new_cleanup)

    def __str__(self):
        '''Generates the shell code for the script.

        The build command is only executed if all the setup commands
        finish without errors.  The setup and build commands are
        executed in a subshell so that changes they make to the shell
        don't affect the cleanup commands.  (This avoids the problem
        with the disorderfs mount being kept open as a current working
        directory when the cleanup tries to unmount it.)  The cleanup
        is executed only if any of the setup commands or the build
        command fails.

        '''
        subshell = _shell_ast.Subshell(self.setup +
                                       _shell_ast.AndList([self.build_command]))
        return (str(subshell) +
                (' ||\n' + str(self.cleanup) if self.cleanup else ''))


# time zone, locales, disorderfs, host name, user/group, shell, CPU
# number, architecture for uname (using linux64), umask, HOME, see
# also: https://tests.reproducible-builds.org/index_variations.html

# TODO: relies on a pbuilder-specific command to parallelize
# @_contextlib.contextmanager
# def cpu(env, tree, testbed):
#     yield script, env, tree

@_contextlib.contextmanager
def captures_environment(script, env, tree, testbed):
    new_env = add(env.experiment, 'CAPTURE_ENVIRONMENT',
                  'i_capture_the_environment')
    yield script, Pair(env.control, new_env), tree

# TODO: this requires superuser privileges.
@_contextlib.contextmanager
def domain_host(script, env, tree, testbed):
    yield script, env, tree

@_contextlib.contextmanager
def fileordering(script, env, tree, testbed):
    new_tree = os.path.dirname(os.path.dirname(tree.control)) + '/disorderfs/'
    # testbed.check_exec(['id'])
    testbed.check_exec(['mkdir', '-p', new_tree])
    # TODO: this is a temporary hack, there will eventually be
    # multiple variations that depend on whether the testbed has root
    # privileges.
    if 'root-on-testbed' in testbed.capabilities:
        disorderfs = ['disorderfs', '--shuffle-dirents=yes',
                      '--multi-user=yes', tree.experiment, new_tree]
    else:
        disorderfs = ['disorderfs', '--shuffle-dirents=yes',
                      tree.experiment, new_tree]
    testbed.check_exec(disorderfs)
    unmount = _shell_ast.SimpleCommand.make('fusermount', '-u', new_tree)
    # If there's an error in the build process, the virt/ program will
    # try to delete the temporary directory containing disorderfs
    # before it's unmounted unless it's unmounted in the script
    # itself.
    new_script = script.experiment.append_cleanup(unmount)
    try:
        yield Pair(script.control, new_script), env, Pair(tree.control, new_tree)
    finally:
        testbed.check_exec(str(unmount).split())

# @_contextlib.contextmanager
# def fileordering(script, env, tree, testbed):
#     yield script, env, tree

@_contextlib.contextmanager
def home(script, env, tree, testbed):
    control = add(env.control, 'HOME', '/nonexistent/first-build')
    experiment = add(env.experiment, 'HOME', '/nonexistent/second-build')
    yield script, Pair(control, experiment), tree

# TODO: uname is a POSIX standard.  The related Linux command
# (setarch) only affects uname at the moment according to the docs.
# FreeBSD changes uname with environment variables.  Wikipedia has a
# reference to a setname command on another Unix variant:
# https://en.wikipedia.org/wiki/Uname
@_contextlib.contextmanager
def kernel(script, env, tree, testbed):
    setarch = _shell_ast.SimpleCommand.make('linux64', '--uname-2.6')
    # setarch = _shell_ast.SimpleCommand(
    #     '', 'linux64', _shell_ast.CmdSuffix(
    #         ['--uname-2.6', script.experiment[0].command]))
    # new_script = (script.experiment[:-1] +
    #               _shell_ast.List([_shell_ast.Term(setarch, ';')]))
    new_script = script.experiment.append_command(setarch)
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
@_contextlib.contextmanager
def locales(script, env, tree, testbed):
    # env1['LANG'] = 'C'
    new_env = add(add(env.experiment, 'LANG', 'fr_CH.UTF-8'),
                  'LC_ALL', 'fr_CH.UTF-8')
    # env1['LANGUAGE'] = 'en_US:en'
    # env2['LANGUAGE'] = 'fr_CH:fr'
    yield script, Pair(env.control, new_env), tree

# TODO: Linux-specific.  unshare --uts requires superuser privileges.
# How is this related to host/domainname?
# def namespace(script, env, tree, testbed):
#     # command1 = ['unshare', '--uts'] + command1
#     # command2 = ['unshare', '--uts'] + command2
#     yield script, env, tree

@_contextlib.contextmanager
def path(script, env, tree, testbed):
    new_env = add(env.experiment, 'PATH', env.control['PATH'] +
                  '/i_capture_the_path')
    yield script, Pair(env.control, new_env), tree

# This doesn't require superuser privileges, but the chsh command
# affects all user shells, which would be bad.
@_contextlib.contextmanager
def shell(script, env, tree, testbed):
    yield script, env, tree

@_contextlib.contextmanager
def timezone(script, env, tree, testbed):
    # These time zones are theoretically in the POSIX time zone format
    # (http://pubs.opengroup.org/onlinepubs/9699919799/basedefs/V1_chap08.html#tag_08),
    # so they should be cross-platform compatible.
    control = add(env.control, 'TZ', 'GMT+12')
    experiment = add(env.experiment, 'TZ', 'GMT-14')
    yield script, Pair(control, experiment), tree

@_contextlib.contextmanager
def umask(script, env, tree, testbed):
    # umask = _shell_ast.SimpleCommand('', 'umask', _shell_ast.CmdSuffix(['0002']))
    # new_script = (_shell_ast.List([_shell_ast.Term(umask, ';')])
    #               + script.experiment)
    umask = _shell_ast.SimpleCommand.make('umask', '0002')
    new_script = script.experiment.append_setup(umask)
    yield Pair(script.control, new_script), env, tree

# TODO: This requires superuser privileges.
@_contextlib.contextmanager
def user_group(script, env, tree, testbed):
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


def build(script, source_root, built_artifact, testbed, artifact_store, env):
    print(source_root)
    # testbed.execute(['ls', '-l', source_root])
    # testbed.execute(['pwd'])
    print(built_artifact)
    # cd = _shell_ast.SimpleCommand('', 'cd', _shell_ast.CmdSuffix([source_root]))
    # new_script = (_shell_ast.List([_shell_ast.Term(cd, ';')]) + script)
    cd = _shell_ast.SimpleCommand.make('cd', source_root)
    new_script = script.append_setup(cd)
    # lsof = _shell_ast.SimpleCommand.make('lsof', '-w', source_root)
    # new_script = new_script.append_cleanup(lsof)
    # ls = _shell_ast.SimpleCommand.make('ls', '-l', testbed.scratch)
    # new_script = new_script.append_cleanup(ls)
    # cd2 = _shell_ast.SimpleCommand.make('cd', '/')
    # new_script = new_script.append_cleanup(cd2)
    print(new_script)
    # exit_code, stdout, stderr = testbed.execute(['sh', '-ec', str(new_script)], xenv=[str(k) + '=' + str(v) for k, v in env.items()], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    testbed.check_exec(['sh', '-ec', str(new_script)], xenv=[str(k) + '=' + str(v) for k, v in env.items()])
    # exit_code, stdout, stderr = testbed.execute(['lsof', source_root], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # print(exit_code, stdout, stderr)
    # testbed.execute(['ls', '-l', source_root])
    # testbed.execute(['stat', source_root])
    # testbed.execute(['stat', built_artifact])
    testbed.command('copyup', (built_artifact, artifact_store))


def check(build_command, artifact_name, virtual_server_args, source_root,
          variations=VARIATIONS):
    # print(virtual_server_args)
    with tempfile.TemporaryDirectory() as temp_dir, start_testbed(virtual_server_args, temp_dir) as testbed:
        script = Pair(Script(build_command), Script(build_command))
        env = Pair(types.MappingProxyType(os.environ.copy()),
                   types.MappingProxyType(os.environ.copy()))
        # TODO, why?: directories need explicit '/' appended for VirtSubproc
        tree = Pair(testbed.scratch + '/control/', testbed.scratch + '/experiment/')
        testbed.command('copydown', (str(source_root) + '/', tree.control))
        testbed.command('copydown', (str(source_root) + '/', tree.experiment))
        # print(source_root)
        try:
            with _contextlib.ExitStack() as stack:
                for variation in variations:
                    # print('START')
                    # print(variation)
                    script, env, tree = stack.enter_context(VARIATIONS[variation](script, env, tree, testbed))
                    # print(script)
                    # print(env)
                    # print(tree)
                build(script.control, tree.control,
                      os.path.normpath(tree.control + artifact_name),
                      testbed,
                      os.path.normpath(temp_dir + '/control_artifact'),
                      env=env.control)
                build(script.experiment, tree.experiment,
                      os.path.normpath(tree.experiment + artifact_name),
                      testbed,
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
