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
            whether others succeed.  Examples: file_ordering.
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
# def cpu(env, build_dir, testbed):
#     yield script, env, build_dir

# TODO: Linux-specific.  unshare --uts requires superuser privileges.
# How is this related to host/domainname?
# def namespace(script, env, build_dir, testbed):
#     # command1 = ['unshare', '--uts'] + command1
#     # command2 = ['unshare', '--uts'] + command2
#     yield script, env, build_dir


@_contextlib.contextmanager
def identity(script, env, build_dir, testbed):
    '''Identity context manager for variations that don't need to do anything.'''
    yield script, env, build_dir

def add(mapping, key, value):
    '''Helper function for adding a key-value pair to an immutable mapping.'''
    new_mapping = mapping.copy()
    new_mapping[key] = value
    return types.MappingProxyType(new_mapping)

def environment_variable_variation(name, value):
    '''Create a context manager to set an environment variable to a value.'''
    @_contextlib.contextmanager
    def set_environment_variable(script, env, build_dir, testbed):
        yield script, add(env, name, value), build_dir
    return set_environment_variable

@_contextlib.contextmanager
def build_path(script, env, build_dir, testbed):
    new_build_dir = os.path.dirname(os.path.dirname(build_dir)) + '/other/'
    testbed.check_exec(['mv', build_dir, new_build_dir])
    yield script, env, new_build_dir

def file_ordering(disorderfs_mount):
    @_contextlib.contextmanager
    def file_ordering(script, env, build_dir, testbed):
        # testbed.check_exec(['id'])
        # Move the directory holding the source tree to a new path.
        real_dir = os.path.dirname(os.path.dirname(build_dir)) + '/real/'
        testbed.check_exec(['mv', build_dir, real_dir])
        # Recreate the original build directory and mount the real
        # source tree there.
        testbed.check_exec(['mkdir', '-p', build_dir])
        testbed.check_exec(disorderfs_mount + [real_dir, build_dir])
        unmount = _shell_ast.SimpleCommand.make('fusermount', '-u', build_dir)
        # If there's an error in the build process, the virt/ program will
        # try to delete the temporary directory containing disorderfs
        # before it's unmounted unless it's unmounted in the script
        # itself.
        new_script = script.append_cleanup(unmount)
        try:
            yield new_script, env, build_dir
        finally:
            testbed.check_exec(str(unmount).split())
    return file_ordering

# TODO: uname is a POSIX standard.  The related Linux command
# (setarch) only affects uname at the moment according to the docs.
# FreeBSD changes uname with environment variables.  Wikipedia has a
# reference to a setname command on another Unix variant:
# https://en.wikipedia.org/wiki/Uname
@_contextlib.contextmanager
def kernel(script, env, build_dir, testbed):
    setarch = _shell_ast.SimpleCommand.make('linux64', '--uname-2.6')
    # setarch = _shell_ast.SimpleCommand(
    #     '', 'linux64', _shell_ast.CmdSuffix(
    #         ['--uname-2.6', script.experiment[0].command]))
    # new_script = (script.experiment[:-1] +
    #               _shell_ast.List([_shell_ast.Term(setarch, ';')]))
    yield script.append_command(setarch), env, build_dir

# TODO: what exact locales and how to many test is probably a mailing
# list question.

# TODO: if this locale doesn't exist on the system, Python's
# locales.getlocale() will return (None, None) rather than this
# locale.  I imagine it will also probably cause false positives with
# builds being reproducible when they aren't because of locale-based
# issues if this locale isn't installed.  The right solution here is
# for this locale to be encoded into the dependencies so installing it
# installs the right locale.  A weaker but still reasonable solution
# is to figure out what locales are installed (how?) and use another
# locale if this one isn't installed.
@_contextlib.contextmanager
def locales(script, env, build_dir, testbed):
    # env1['LANG'] = 'C'
    new_env = add(add(env, 'LANG', 'fr_CH.UTF-8'), 'LC_ALL', 'fr_CH.UTF-8')
    # env1['LANGUAGE'] = 'en_US:en'
    # env2['LANGUAGE'] = 'fr_CH:fr'
    yield script, new_env, build_dir

@_contextlib.contextmanager
def path(script, env, build_dir, testbed):
    new_env = add(env, 'PATH', env['PATH'] + '/i_capture_the_path')
    yield script, new_env, build_dir

@_contextlib.contextmanager
def umask(script, env, build_dir, testbed):
    umask = _shell_ast.SimpleCommand.make('umask', '0002')
    yield script.append_setup(umask), env, build_dir


class MultipleDispatch(collections.OrderedDict):
    '''This is a mapping that imitates a dictionary with tuple keys using
    nested mappings and sequences, to make it easier to specify the
    full set of combinations without needing to write out a tuple for
    every possible combination.

    '''

    def __getitem__(self, keys):
        value = super().__getitem__(keys[0])
        for key in keys[1:]:
            try:
                value = value[key]
            except (IndexError, KeyError, TypeError):
                break
        return value


# The order of the dispatch tuple is designed so that the values that
# require the most different functions occur earlier.  Variations is
# first and run number second because each variation requires
# different code for each of control and experiment.  (Note: at the
# moment, two builds are hard-coded.)  Root privileges is third
# because only some variations change depending on root privileges.
# The last, OS/system, is not implemented at the moment but only has
# one variation I know of.

# TODO: still true?
# The order of the variations *is* important, because the command to
# be executed in the container needs to be built from the inside out.
VARIATIONS = types.MappingProxyType(MultipleDispatch([
    ('build_path', (identity, build_path)),
    ('captures_environment',
     (identity,
      environment_variable_variation(
          'CAPTURE_ENVIRONMENT', 'i_capture_the_environment'))),
    # TODO: this requires superuser privileges.
    ('domain_host', identity),
    ('file_ordering',
     (identity,
      types.MappingProxyType(collections.OrderedDict(
          [('user', file_ordering(['disorderfs', '--shuffle-dirents=yes'])),
           ('root', file_ordering(
                ['disorderfs', '--shuffle-dirents=yes', '--multi-user=yes']))
           ])))),
    ('home',
     (environment_variable_variation('HOME', '/nonexistent/first-build'),
      environment_variable_variation('HOME', '/nonexistent/second-build'))),
    ('kernel', (identity, kernel)),
    ('locales', (identity, locales)),
    ('path', (identity, path)),
    # TODO: This doesn't require superuser privileges, but the chsh command
    # affects all user shells, which would be bad.
    ('shell', identity),
    # These time zones are theoretically in the POSIX time zone format
    # (http://pubs.opengroup.org/onlinepubs/9699919799/basedefs/V1_chap08.html#tag_08),
    # so they should be cross-platform compatible.
    ('time_zone',
     (environment_variable_variation('TZ', 'GMT+12'),
      environment_variable_variation('TZ', 'GMT-14'))),
    ('umask', (identity, umask)),
    # TODO: This requires superuser privileges.
    ('user_group', identity)
]))


def build(script, source_root, build_dir, built_artifact, testbed,
          artifact_store, env):
    # print(source_root)
    # print(build_dir)
    # testbed.execute(['ls', '-l', build_dir])
    # testbed.execute(['pwd'])
    # print(built_artifact)
    cd = _shell_ast.SimpleCommand.make('cd', build_dir)
    new_script = script.append_setup(cd)
    # lsof = _shell_ast.SimpleCommand.make('lsof', '-w', build_dir)
    # new_script = new_script.append_cleanup(lsof)
    # ls = _shell_ast.SimpleCommand.make('ls', '-l', testbed.scratch)
    # new_script = new_script.append_cleanup(ls)
    # cd2 = _shell_ast.SimpleCommand.make('cd', '/')
    # new_script = new_script.append_cleanup(cd2)
    print(new_script)
    # exit_code, stdout, stderr = testbed.execute(['sh', '-ec', str(new_script)], xenv=[str(k) + '=' + str(v) for k, v in env.items()], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    testbed.check_exec(['sh', '-ec', str(new_script)], xenv=[str(k) + '=' + str(v) for k, v in env.items()])
    # exit_code, stdout, stderr = testbed.execute(['lsof', build_dir], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # print(exit_code, stdout, stderr)
    # testbed.execute(['ls', '-l', build_dir])
    # testbed.execute(['stat', build_dir])
    # testbed.execute(['stat', built_artifact])
    testbed.command('copyup', (built_artifact, artifact_store))


def check(build_command, artifact_name, virtual_server_args, source_root,
          variations=VARIATIONS):
    # print(virtual_server_args)
    with tempfile.TemporaryDirectory() as temp_dir, start_testbed(virtual_server_args, temp_dir) as testbed:
        script = Script(build_command)
        env = types.MappingProxyType(os.environ.copy())
        # TODO, why?: directories need explicit '/' appended for VirtSubproc
        build_dir = testbed.scratch + '/build/'
        if 'root-on-testbed' in testbed.capabilities:
            user = 'root'
        else:
            user = 'user'
        try:
            for i in range(2):
                testbed.command('copydown', (str(source_root) + '/', build_dir))
                new_script, new_env, new_build_dir = script, env, build_dir
                with _contextlib.ExitStack() as stack:
                    for variation in variations:
                        # print('START')
                        # print(variation)
                        new_script, new_env, new_build_dir = stack.enter_context(VARIATIONS[(variation, i, user)](new_script, new_env, new_build_dir, testbed))
                        # print(new_script)
                        # print(new_env)
                        # print(new_build_dir)
                    build(new_script, str(source_root), new_build_dir,
                          os.path.normpath(new_build_dir + artifact_name),
                          testbed,
                          os.path.normpath(temp_dir + '/artifact' + str(i)),
                          env=new_env)
        except Exception:
            traceback.print_exc()
            sys.exit(2)
        # sys.exit(subprocess.call(['diffoscope', temp_dir + '/artifact0', temp_dir + '/artifact1']))


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
