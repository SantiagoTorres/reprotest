# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import argparse
import collections
import configparser
import logging
import os
import pathlib
import random
import shlex
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
        cleanup = _shell_ast.BraceGroup(self.cleanup)
        return (str(subshell) +
                (' ||\n' + str(cleanup) if self.cleanup else ''))



# TODO: Linux-specific.  unshare --uts requires superuser privileges.
# How is this related to host/domainname?
# def namespace(script, env, build_path, testbed):
#     # command1 = ['unshare', '--uts'] + command1
#     # command2 = ['unshare', '--uts'] + command2
#     yield script, env, build_path

# This string describes the arguments and return values for all the
# variation context managers.
VARIATION_DOCSTRING = '''
    Args:
        script (Script): The build script to be executed.
        env (types.MappingProxyType[dict[str, str]]): A mapping of environment
            variable names to values.
        build_path (str): The absolute path to the directory on the testbed
            containing the source tree.
        testbed (adt_testbed.Testbed): The testbed instance, for running
            commands in the variations.
        past_variations (dict[str, object]): Information about the values
            assigned to variations in previous runs.

    Returns:
        A three-tuple containing a script, a mapping of environment variables,
        and an absolute path to a directory on the testbed.
'''

@_contextlib.contextmanager
def identity(script, env, build_path, testbed, past_variations):
    '''Identity context manager for variations that don't need to do anything.'''
    yield script, env, build_path, past_variations
identity.__doc__ += VARIATION_DOCSTRING

def add(mapping, key, value):
    '''Helper function for adding a key-value pair to an immutable mapping.

    Args:
         mapping (types.MappingProxyType[collections.Mapping]): The mapping to
             add keys to.
         key (typing.Hashable)
         value (object)
    '''
    new_mapping = mapping.copy()
    new_mapping[key] = value
    return types.MappingProxyType(new_mapping)

def environment_variable_variation(name, value):
    '''Creates a context manager to set an environment variable to a value.'''
    @_contextlib.contextmanager
    def set_environment_variable(script, env, build_path, testbed, past_variations):
        yield script, add(env, name, value), build_path, past_variations
    set_environment_variable.__doc__ = ('Set %s to %s.%s' %
                                        (name, value, VARIATION_DOCSTRING))
    return set_environment_variable

@_contextlib.contextmanager
def bin_sh(script, env, build_path, testbed, past_variations):
    '''Change the shell that /bin/sh points to.'''
    
    # new_build_path = os.path.dirname(os.path.dirname(build_path)) + '/other/'
    # testbed.check_exec(['mv', build_path, new_build_path])
    yield script, env, build_path, past_variations
bin_sh.__doc__ += VARIATION_DOCSTRING

@_contextlib.contextmanager
def build_path(script, env, build_path, testbed, past_variations):
    '''Change the name of the build path.'''
    new_build_path = os.path.dirname(os.path.dirname(build_path)) + '/other/'
    testbed.check_exec(['mv', build_path, new_build_path])
    yield script, env, new_build_path, past_variations
build_path.__doc__ += VARIATION_DOCSTRING

def domain_host(what_to_change):
    '''Creates a context manager that changes and reverts the domain or
    host name.

    Args:
         what_to_change (str): host or domain.

    '''
    command = what_to_change + 'name'
    new_name = 'i-capture-the-' + what_to_change
    @_contextlib.contextmanager
    def change_name(script, env, build_path, testbed, past_variations):
        '''Change and revert domain or host name before and after building,
        respectively.'''
        # Save the previous name in a local variable.  strip() is
        # necessary because the output of domainname/hostname contains
        # a newline.
        old_name = testbed.check_exec([command], stdout=True).strip()
        # print('DOMAIN_HOST')
        # print(command)
        # print(new_name)
        # print(old_name)
        testbed.check_exec([command, new_name])
        # domainname when not set seems to be rendered as '(none)',
        # which will cause errors when the shell script is run if not
        # quoted.
        revert = _shell_ast.SimpleCommand.make(command, shlex.quote(old_name))
        try:
            yield script.append_cleanup(revert), env, build_path, past_variations
        finally:
            testbed.check_exec(str(revert).split())
    change_name.__doc__ += VARIATION_DOCSTRING
    return change_name

def file_ordering(disorderfs_mount):
    '''Generate a context manager that mounts and unmounts disorderfs.

    Args:
         disorderfs_mount (list[str]): The command for mounting disorderfs.
    '''
    @_contextlib.contextmanager
    def file_ordering(script, env, build_path, testbed, past_variations):
        '''Mount the source tree with disorderfs at the build path, then
        unmount it after the build.'''
        # testbed.check_exec(['id'])
        # Move the directory holding the source tree to a new path.
        real_dir = os.path.dirname(os.path.dirname(build_path)) + '/real/'
        testbed.check_exec(['mv', build_path, real_dir])
        # Recreate the original build directory and mount the real
        # source tree there.
        testbed.check_exec(['mkdir', '-p', build_path])
        testbed.check_exec(disorderfs_mount + [real_dir, build_path])
        unmount = _shell_ast.SimpleCommand.make('fusermount', '-u', build_path)
        # If there's an error in the build process, the virt/ program will
        # try to delete the temporary directory containing disorderfs
        # before it's unmounted unless it's unmounted in the script
        # itself.
        new_script = script.append_cleanup(unmount)
        try:
            yield new_script, env, build_path, past_variations
        finally:
            testbed.check_exec(str(unmount).split())
    file_ordering.__doc__ += VARIATION_DOCSTRING
    return file_ordering

# TODO: uname is a POSIX standard.  The related Linux command
# (setarch) only affects uname at the moment according to the docs.
# FreeBSD changes uname with environment variables.  Wikipedia has a
# reference to a setname command on another Unix variant:
# https://en.wikipedia.org/wiki/Uname
@_contextlib.contextmanager
def kernel(script, env, build_path, testbed, past_variations):
    '''Mock the value that uname returns for the system kernel.'''
    setarch = _shell_ast.SimpleCommand.make('linux64', '--uname-2.6')
    # setarch = _shell_ast.SimpleCommand(
    #     '', 'linux64', _shell_ast.CmdSuffix(
    #         ['--uname-2.6', script.experiment[0].command]))
    # new_script = (script.experiment[:-1] +
    #               _shell_ast.List([_shell_ast.Term(setarch, ';')]))
    yield script.append_command(setarch), env, build_path, past_variations
kernel.__doc__ += VARIATION_DOCSTRING

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
def locales(first_choices):
    '''Generate a context manager for changing the locales environment
    variables.  

    Args:
         first_choices (dict[str, str]): A mapping of environment variable names
             to default choices for their values.
    '''
    @_contextlib.contextmanager
    def locales(script, env, build_path, testbed, past_variations):
        '''Change the locales environment variables LANG, LANGUAGE, and LC_ALL.

        '''
        # locale -a is specified in the POSIX standard.
        locales = frozenset(testbed.check_exec(['locale', '-a'], True).split())
        new_env = env
        # The values of locales used for this build.
        saved_locales = {}
        for variable in ('LANG', 'LANGUAGE', 'LC_ALL'):
            # If this variable isn't set, leave it unset.
            if variable in first_choices:
                # If the first choice is installed *and* it wasn't
                # used in a previous build, use it as the value.
                if first_choices[variable] in (locales - past_variations.keys()):
                    value = first_choices[variable]
                # If not, pick a random locale from those installed.
                else:
                    value = random.choice(tuple(locales - past_variations.keys()))
                new_env = add(new_env, variable, value)
                saved_locales[variable] = frozenset([value])
        yield (script, new_env, build_path, add(
            past_variations, 'locales', types.MappingProxyType(saved_locales)))
    locales.__doc__ += VARIATION_DOCSTRING
    return locales

@_contextlib.contextmanager
def login_shell(script, env, build_path, testbed, past_variations):
    '''Change the'''
    yield script, new_env, build_path, past_variations
login_shell.__doc__ += VARIATION_DOCSTRING

@_contextlib.contextmanager
def path(script, env, build_path, testbed, past_variations):
    '''Add a directory to the PATH environment variable.'''
    new_env = add(env, 'PATH', env['PATH'] + '/i_capture_the_path')
    yield script, new_env, build_path, past_variations
path.__doc__ += VARIATION_DOCSTRING

@_contextlib.contextmanager
def umask(script, env, build_path, testbed, past_variations):
    '''Change the umask that the build script is executed with.'''
    umask = _shell_ast.SimpleCommand.make('umask', '0002')
    yield script.append_setup(umask), env, build_path, past_variations
umask.__doc__ += VARIATION_DOCSTRING


class MultipleDispatch(collections.OrderedDict):
    '''This mapping holds the functions for creating the variations.

    This is intended to hold tuple keys.  To make it easier to specify
    the full set of combinations without needing to write out a tuple
    for every possible combination, when a tuple is not found, it will
    search to see if there's a shorter tuple in the mapping that the
    given tuple contains.  If so, it will return that function.  If
    not, it will return the identity variation.

    '''

    def __missing__(self, keys):
        value = identity
        for index in range(len(keys), 1, -1):
            key = keys[:index]
            if key in self:
                value = self[key]
                break
        return value


# The order of the dispatch tuple is designed so that the values that
# require the most different functions occur earlier.  Variations is
# first and run number second because each variation requires
# different code for each of control and experiment.  (Note: at the
# moment, two builds are hard-coded.)  Root privileges is third
# because only some variations change depending on root privileges.
# The fourth is execution environment, because some environments
# always offer root privileges.  The last is OS, because most of the
# OS-specific code occurs in variations that depend on root privileges
# and execution environment.

# The order of the variations is important.  At the moment, the only
# constraint is that build_path must appear before file_ordering so
# that the build path is changed before disorderfs is mounted.

# See also: https://tests.reproducible-builds.org/index_variations.html
DISPATCH = types.MappingProxyType(MultipleDispatch([
    (('bin_sh', 1), identity),
    (('build_path', 1), build_path),
    (('captures_environment', 1), 
      environment_variable_variation(
          'CAPTURE_ENVIRONMENT', 'i_capture_the_environment')),
    (('domain', 1, 'root', 'qemu'), domain_host('domain')),
    (('file_ordering', 1, 'user'), file_ordering(['disorderfs', '--shuffle-dirents=yes'])),
    (('file_ordering', 1, 'root'), file_ordering(['disorderfs', '--shuffle-dirents=yes', '--multi-user=yes'])),
    (('home', 0), environment_variable_variation('HOME', '/nonexistent/first-build')),
    (('home', 1), environment_variable_variation('HOME', '/nonexistent/second-build')),
    (('domain', 1, 'root', 'qemu'), domain_host('host')),
    (('kernel', 1), kernel),
    (('locales', 0), locales(types.MappingProxyType(
        {'LANG': 'C', 'LANGUAGE': 'en_US:en'}))),
    (('locales', 1), locales(types.MappingProxyType(
        {'LANG': 'fr_CH.utf8', 'LANGUAGE': 'fr_CH.utf8',
         'LC_ALL': 'fr_CH.utf8'}))),
    (('login_shell', 1), identity),
    (('path', 1), path),
    # These time zones are theoretically in the POSIX time zone format
    # (http://pubs.opengroup.org/onlinepubs/9699919799/basedefs/V1_chap08.html#tag_08),
    # so they should be cross-platform compatible.
    (('time', 1), identity),
    (('time_zone', 0), environment_variable_variation('TZ', 'GMT+12')),
    (('time_zone', 1), environment_variable_variation('TZ', 'GMT-14')),
    (('umask', 1), umask),
    (('user_group', 1, 'root'), identity)]))


# TODO: keeping the variations constant separate from the dispatch
# functions violates DRY in a way that will be easy to desynch.  This
# probably needs to be constructed from dispatch table.
VARIATIONS = ('bin_sh', 'build_path', 'captures_environment',
              'domain', 'file_ordering', 'home', 'host', 'kernel',
              'locales', 'login_shell', 'path', 'time', 'time_zone',
              'umask', 'user_group')


def build(script, source_root, build_path, built_artifact, testbed,
          artifact_store, env):
    # print(source_root)
    # print(build_path)
    # testbed.execute(['ls', '-l', build_path])
    # testbed.execute(['pwd'])
    # print(built_artifact)
    cd = _shell_ast.SimpleCommand.make('cd', build_path)
    new_script = script.append_setup(cd)
    # lsof = _shell_ast.SimpleCommand.make('lsof', '-w', build_path)
    # new_script = new_script.append_cleanup(lsof)
    # ls = _shell_ast.SimpleCommand.make('ls', '-l', testbed.scratch)
    # new_script = new_script.append_cleanup(ls)
    # cd2 = _shell_ast.SimpleCommand.make('cd', '/')
    # new_script = new_script.append_cleanup(cd2)
    print('SCRIPT')
    print(new_script)
    print(env)
    # exit_code, stdout, stderr = testbed.execute(['sh', '-ec', str(new_script)], xenv=[str(k) + '=' + str(v) for k, v in env.items()], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    testbed.check_exec(['sh', '-ec', str(new_script)], xenv=[str(k) + '=' + str(v) for k, v in env.items()])
    # exit_code, stdout, stderr = testbed.execute(['lsof', build_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # print(exit_code, stdout, stderr)
    # testbed.execute(['ls', '-l', build_path])
    # testbed.execute(['stat', build_path])
    # testbed.execute(['stat', built_artifact])
    testbed.command('copyup', (built_artifact, artifact_store))


def check(build_command, artifact_name, virtualization_args, source_root,
          variations=VARIATIONS):
    # print(virtualization_args)
    with tempfile.TemporaryDirectory() as temp_dir, start_testbed(virtualization_args, temp_dir) as testbed:
        script = Script(build_command)
        env = types.MappingProxyType(os.environ.copy())
        # TODO, why?: directories need explicit '/' appended for VirtSubproc
        build_path = testbed.scratch + '/build/'
        if 'root-on-testbed' in testbed.capabilities:
            user = 'root'
        else:
            user = 'user'
        # The POSIX standard specifies that the first word of the
        # uname's output should be the OS name.
        testbed_os = testbed.initial_kernel_version.split()[0]
        try:
            for i in range(2):
                testbed.command('copydown', (str(source_root) + '/', build_path))
                new_script, new_env, new_build_path = script, env, build_path
                with _contextlib.ExitStack() as stack:
                    for variation in variations:
                        # print('START')
                        # print(variation)
                        new_script, new_env, new_build_path, past_variations = stack.enter_context(DISPATCH[(variation, i, user)](new_script, new_env, new_build_path, testbed, types.MappingProxyType({})))
                        # print(new_script)
                        # print(new_env)
                        # print(new_build_path)
                    build(new_script, str(source_root), new_build_path,
                          os.path.normpath(new_build_path + artifact_name),
                          testbed,
                          os.path.normpath(temp_dir + '/artifact' + str(i)),
                          env=new_env)
        except Exception:
            traceback.print_exc()
            sys.exit(2)
        sys.exit(subprocess.call(['diffoscope', temp_dir + '/artifact0', temp_dir + '/artifact1']))


COMMAND_LINE_OPTIONS = types.MappingProxyType(collections.OrderedDict([
    ('build_command', types.MappingProxyType({
        'default': None, 'nargs': '?', # 'type': str.split
        'help': 'Build command to execute.'})),
    ('artifact', types.MappingProxyType({
        'default': None, 'nargs': '?',
        'help': 'Build artifact to test for reproducibility.'})),
    ('virtualization_args', types.MappingProxyType({
        'default': None, 'nargs': '*',
        'help': 'The virtual server and any arguments to pass to it.'})),
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
                               'variations', 'virtualization_args'])

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
    virtualization_args = command_line_options.get(
        'virtualization_args',
        config_options.get('virtualization_args'))
    # Reprotest will copy this tree and then run the build command.
    # If a source root isn't provided, assume it's the current working
    # directory.
    source_root = command_line_options.get(
        'source_root',
        config_options.get('source_root', pathlib.Path.cwd()))
    # The default is to try all variations.
    variations = frozenset(VARIATIONS)
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
    if not virtualization_args:
        print("No virtual_server to run the build in specified.")
        sys.exit(2)
    logging.basicConfig(
        format='%(message)s', level=30-10*verbosity, stream=sys.stdout)

    # print(build_command, artifact, virtualization_args)
    check(build_command, artifact, virtualization_args, source_root, variations)
