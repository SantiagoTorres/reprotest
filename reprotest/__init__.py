# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import argparse
import collections
import configparser
import logging
import os
import pathlib
import random
import re
import shlex
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
from reprotest import _contextlib
from reprotest import _shell_ast
from reprotest import presets


adtlog.verbosity = 1


def get_server_path(server_name):
    return pkg_resources.resource_filename(__name__, os.path.join("virt", server_name))

def is_executable(parent, fn):
    path = os.path.join(parent, fn)
    return os.path.isfile(path) and os.access(path, os.X_OK)

all_servers = None
def get_all_servers():
    global all_servers
    if all_servers is None:
        server_dir = get_server_path("")
        all_servers = sorted(fn for fn in os.listdir(server_dir) if is_executable(server_dir, fn))
    return all_servers


# chroot is the only form of OS virtualization that's available on
# most POSIX OSes.  Linux containers (lxc) and namespaces are specific
# to Linux.  Some versions of BSD have jails (MacOS X?).  There are a
# variety of other options including Docker etc that use different
# approaches.

@_contextlib.contextmanager
def start_testbed(args, temp_dir, no_clean_on_error=False):
    '''This is a simple wrapper around adt_testbed that automates the
    initialization and cleanup.'''
    # Find the location of reprotest using setuptools and then get the
    # path for the correct virt-server script.
    server_path = get_server_path(args[0])
    print('VIRTUAL SERVER', [server_path] + args[1:])
    testbed = adt_testbed.Testbed([server_path] + args[1:], temp_dir, None)
    testbed.start()
    testbed.open()
    should_clean = True
    try:
        yield testbed
    except:
        if no_clean_on_error:
            should_clean = False
        raise
    finally:
        if should_clean:
            # TODO: we could probably do *some* level of cleanup even if
            # should_clean is False; investigate this further...
            testbed.stop()


class Pair(collections.namedtuple('_Pair', 'control experiment')):
    """Holds one object for each run of the build process."""
    @classmethod
    def of(cls, x):
        return cls(x, x)

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
        build_command = _shell_ast.SimpleCommand(
            "sh", "-ec", _shell_ast.Quote(build_command))
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

    def append_setup_exec(self, *args):
        return self.append_setup(_shell_ast.SimpleCommand.make(*map(_shell_ast.Quote, args)))

    def prepend_cleanup(self, command):
        '''Adds a command to the cleanup phase.

        '''
        # if this command fails, save the exit code but keep executing
        # we run with -e, so it would fail otherwise
        new_cleanup = (_shell_ast.List([_shell_ast.Term(
                            "{0} || __c=$?".format(command), ';')])
                       + self.cleanup)
        return self._replace(cleanup=new_cleanup)

    def prepend_cleanup_exec(self, *args):
        return self.prepend_cleanup(_shell_ast.SimpleCommand.make(*map(_shell_ast.Quote, args)))

    def move_tree(self, source, target):
        return self.append_setup_exec(
            'mv', source, target).prepend_cleanup_exec(
            'mv', target, source)

    def __str__(self):
        '''Generates the shell code for the script.

        The build command is only executed if all the setup commands
        finish without errors.  The setup and build commands are
        executed in a subshell so that changes they make to the shell
        don't affect the cleanup commands.  (This avoids the problem
        with the disorderfs mount being kept open as a current working
        directory when the cleanup tries to unmount it.)

        '''
        subshell = _shell_ast.Subshell(self.setup +
                                       _shell_ast.AndList([self.build_command]))

        if self.cleanup:
            cleanup = """( __c=0; {0} exit $__c; )""".format(str(self.cleanup))
            return """\
if {0}; then
    {1};
else
    __x=$?;
    if {1}; then exit $__x; else
        echo >&2; "cleanup failed with exit code $?"; exit $__x;
    fi;
fi
""".format(str(subshell), str(cleanup))
        else:
            return str(subshell)


def dirname(p):
    # works more intuitively for paths with a trailing /
    return os.path.normpath(os.path.dirname(os.path.normpath(p)))

def basename(p):
    # works more intuitively for paths with a trailing /
    return os.path.normpath(os.path.basename(os.path.normpath(p)))


# time zone, locales, disorderfs, host name, user/group, shell, CPU
# number, architecture for uname (using linux64), umask, HOME, see
# also: https://tests.reproducible-builds.org/index_variations.html
# TODO: the below ideally should *read the current value*, and pick
# something that's different for the experiment.

# TODO: relies on a pbuilder-specific command to parallelize
# def cpu(script, env, tree):
#     return script, env, tree

def environment(script, env, tree, *args):
    new_env = add(env.experiment, 'CAPTURE_ENVIRONMENT',
                  'i_capture_the_environment')
    return script, Pair(env.control, new_env), tree

# TODO: this requires superuser privileges.
# def domain_host(script, env, tree):
#     return script, env, tree

# Note: this has to go before fileordering because we can't move mountpoints
# TODO: this variation makes it impossible to parallelise the build, for most
# of the current virtual servers. (It's theoretically possible to make it work)
def build_path_same(script, env, tree, *args):
    const_path = os.path.join(dirname(tree.control), 'const_build_path')
    assert const_path == os.path.join(dirname(tree.experiment), 'const_build_path')
    new_control = script.control.move_tree(tree.control, const_path)
    new_experiment = script.experiment.move_tree(tree.experiment, const_path)
    const_path_dir = os.path.join(const_path, '')
    return Pair(new_control, new_experiment), env, Pair.of(const_path_dir)
build_path_same.negative = True

def fileordering(script, env, tree, *args):
    old_tree = os.path.join(dirname(tree.experiment), basename(tree.experiment) + '-before-disorderfs', '')
    disorderfs = ['sh', '-ec',
        'disorderfs --shuffle-dirents=yes --multi-user="$(if [ $(id -u) = 0 ]; then echo yes; else echo no; fi)" "$@"',
        '-', old_tree, tree.experiment]
    _ = script.experiment.move_tree(tree.experiment, old_tree)
    _ = _.append_setup_exec('mkdir', '-p', tree.experiment)
    _ = _.prepend_cleanup_exec('rmdir', tree.experiment)
    _ = _.append_setup_exec(*disorderfs)
    _ = _.prepend_cleanup_exec('fusermount', '-u', tree.experiment)
    new_script = _
    return Pair(script.control, new_script), env, tree

# # def fileordering(script, env, tree):
#     return script, env, tree

def home(script, env, tree, *args):
    control = add(env.control, 'HOME', '/nonexistent/first-build')
    experiment = add(env.experiment, 'HOME', '/nonexistent/second-build')
    return script, Pair(control, experiment), tree

# TODO: uname is a POSIX standard.  The related Linux command
# (setarch) only affects uname at the moment according to the docs.
# FreeBSD changes uname with environment variables.  Wikipedia has a
# reference to a setname command on another Unix variant:
# https://en.wikipedia.org/wiki/Uname
def kernel(script, env, tree, *args):
    # set these two explicitly different. otherwise, when reprotest is
    # reprotesting itself, then one of the builds will fail its tests, because
    # its two child reprotests will see the same value for "uname" but the
    # tests expect different values.
    setarch = _shell_ast.SimpleCommand.make('linux64', '--uname-2.6')
    setarch2 = _shell_ast.SimpleCommand.make('linux32')
    new_control = script.control.append_command(setarch)
    new_experiment = script.experiment.append_command(setarch2)
    return Pair(new_control, new_experiment), env, tree

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
def locales(script, env, tree, *args):
    new_control = add(add(env.control, 'LANG', 'C.UTF-8'), 'LANGUAGE', 'en_US:en')
    # if there is an issue with this being random, we could instead select it
    # based on a deterministic hash of the inputs
    loc = random.choice(['fr_CH.UTF-8', 'es_ES', 'ru_RU.CP1251', 'kk_KZ.RK1048', 'zh_CN'])
    new_experiment = add(add(add(env.experiment, 'LANG', loc), 'LC_ALL', loc), 'LANGUAGE', '%s:fr' % loc)
    return script, Pair(new_control, new_experiment), tree

# TODO: Linux-specific.  unshare --uts requires superuser privileges.
# How is this related to host/domainname?
# def namespace(script, env, tree, *args):
#     # command1 = ['unshare', '--uts'] + command1
#     # command2 = ['unshare', '--uts'] + command2
#     return script, env, tree

def path(script, env, tree, *args):
    new_env = add(env.experiment, 'PATH', env.control['PATH'] +
                  ':/i_capture_the_path')
    return script, Pair(env.control, new_env), tree

# This doesn't require superuser privileges, but the chsh command
# affects all user shells, which would be bad.
# # def shell(script, env, tree, *args):
#     return script, env, tree

def timezone(script, env, tree, *args):
    # These time zones are theoretically in the POSIX time zone format
    # (http://pubs.opengroup.org/onlinepubs/9699919799/basedefs/V1_chap08.html#tag_08),
    # so they should be cross-platform compatible.
    control = add(env.control, 'TZ', 'GMT+12')
    experiment = add(env.experiment, 'TZ', 'GMT-14')
    return script, Pair(control, experiment), tree

def faketime(script, env, tree, source_root):
    # Get the latest modification date of all the files in the source root.
    # This tries hard to avoid bad interactions with faketime and make(1) etc.
    # However if you're building this too soon after changing one of the source
    # files then the effect of this variation is not very great.
    filemtimes = (os.path.getmtime(os.path.join(root, f)) for root, dirs, files in os.walk(source_root) for f in files)
    now = time.time()
    lastmt = int(max(filemtimes, default=now))
    if lastmt < now - 32253180:
        # if lastmt is far in the past, use that, it's a bit safer
        faket = '@%s' % lastmt
    else:
        # otherwise use a date far in the future
        faket = '+373days+7hours+13minutes'
    settime = _shell_ast.SimpleCommand.make('faketime', faket)
    new_experiment = script.experiment.append_command(settime)
    # faketime's manpages are stupidly misleading; it also modifies file timestamps.
    # this is only mentioned in the README. we do not want this, it really really
    # messes with GNU make and other buildsystems that look at timestamps.
    new_experiment_env = add(env.experiment, 'NO_FAKE_STAT', '1')
    return Pair(script.control, new_experiment), Pair(env.control, new_experiment_env), tree

def umask(script, env, tree, *args):
    new_control = script.control.append_setup_exec('umask', '0022')
    new_experiment = script.experiment.append_setup_exec('umask', '0002')
    return Pair(new_control, new_experiment), env, tree

# TODO: This requires superuser privileges.
# # def user_group(script, env, tree, *args):
#     return script, env, tree


# The order of the variations *is* important, because the command to
# be executed in the container needs to be built from the inside out.
VARIATIONS = types.MappingProxyType(collections.OrderedDict([
    ('environment', environment),
    ('build_path', build_path_same),
    # ('cpu', cpu),
    # ('domain_host', domain_host),
    ('fileordering', fileordering),
    ('home', home),
    ('kernel', kernel),
    ('locales', locales),
    # ('namespace', namespace),
    ('path', path),
    # ('shell', shell),
    ('timezone', timezone),
    ('faketime', faketime),
    ('umask', umask),
    # ('user_group', user_group),
]))


def build(script, env, source_root_orig, source_root_build, dist_root, artifact_store, artifact_pattern, testbed):
    print("source directory:", source_root_orig)
    print("artifact_pattern:", artifact_pattern)
    # remove any existing artifact, in case the build script doesn't overwrite
    # it e.g. like how make(1) sometimes works.
    if re.search(r"""(^| )['"]*/""", artifact_pattern):
        raise ValueError("artifact_pattern is possibly dangerous; maybe use a relative path instead?")
    testbed.check_exec(
        ['sh', '-ec', 'cd "%s" && rm -rf %s' %
        (source_root_orig, artifact_pattern)])
    new_script = script.append_setup_exec('cd', source_root_build)
    print("executing:", new_script)
    argv = ['sh', '-ec', str(new_script)]
    xenv = ['%s=%s' % (k, v) for k, v in env.items()]
    (code, _, _) = testbed.execute(argv, xenv=xenv, kind='build')
    if code != 0:
        testbed.bomb('"%s" failed with status %i' % (' '.join(argv), code), adtlog.AutopkgtestError)
    # exit_code, stdout, stderr = testbed.execute(['lsof', source_root], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # print(exit_code, stdout, stderr)
    testbed.check_exec(
        ['sh', '-ec', 'mkdir -p "%s" && cd "%s" && cp -a -t "%s" %s && touch -d@0 "%s" "%s"/*' %
        (dist_root, source_root_orig, dist_root, artifact_pattern, dist_root, dist_root)])


def run_or_tee(progargs, filename, store_dir, *args, **kwargs):
    if store_dir:
        tee = subprocess.Popen(['tee', filename], stdin=subprocess.PIPE, cwd=store_dir)
        r = subprocess.run(progargs, *args, stdout=tee.stdin, **kwargs)
        tee.communicate()
        return r
    else:
        return subprocess.run(progargs)


def check(build_command, artifact_pattern, virtual_server_args, source_root,
          no_clean_on_error=False, variations=VARIATIONS,
          store_dir=None, diffoscope_args=[],
          testbed_pre=None, testbed_init=None):
    # default argument [] is safe here because we never mutate it.
    if not source_root:
        raise ValueError("invalid source root: %s" % source_root)
    if store_dir:
        store_dir = str(store_dir)
        if not os.path.exists(store_dir):
            os.makedirs(store_dir, exist_ok=False)
        elif os.listdir(store_dir):
            raise ValueError("store_dir must be empty: %s" % store_dir)
        store = Pair(os.path.join(store_dir, "control"),
                     os.path.join(store_dir, "experiment"))

    # print(virtual_server_args)
    script = Pair.of(Script(build_command))
    env = Pair(types.MappingProxyType(os.environ.copy()),
               types.MappingProxyType(os.environ.copy()))

    source_root = str(source_root)
    with tempfile.TemporaryDirectory() as temp_dir:
        if testbed_pre:
            new_source_root = os.path.join(temp_dir, "testbed_pre")
            shutil.copytree(source_root, new_source_root, symlinks=True)
            subprocess.check_call(["sh", "-ec", testbed_pre], cwd=new_source_root)
            source_root = new_source_root
        # print(source_root)

        result = Pair(os.path.join(temp_dir, 'control_artifact/'),
                      os.path.join(temp_dir, 'experiment_artifact/'))

        # TODO: an alternative strategy is to run the testbed twice; not sure
        # if it's worth implementing at this stage, but perhaps in the future.
        with start_testbed(virtual_server_args, temp_dir, no_clean_on_error) as testbed:
            # directories need explicit '/' appended for VirtSubproc
            tree = Pair(testbed.scratch + '/control/', testbed.scratch + '/experiment/')
            dist = Pair(testbed.scratch + '/control-dist/', testbed.scratch + '/experiment-dist/')
            source_root = source_root + '/'

            orig_tree = tree
            # print(script, env, tree)
            # build the scripts to run the variations
            for variation in VARIATIONS:
                vary = VARIATIONS[variation]
                negative = hasattr(vary, "negative") and vary.negative
                if (variation in variations) != negative:
                    script, env, tree = vary(script, env, tree, source_root)
                    print("== will %s %s ==" % ("FIX" if negative else "vary", variation))
                    # print(script, env, tree)

            try:
                # run the scripts
                if testbed_init:
                    testbed.check_exec(["sh", "-ec", testbed_init])

                for i in (0, 1):
                    testbed.command('copydown', (source_root, orig_tree[i]))

                for i in (0, 1):
                    build(script[i], env[i], orig_tree[i], tree[i], dist[i], result[i],
                          artifact_pattern, testbed)

                for i in (0, 1):
                    testbed.command('copyup', (dist[i], result[i]))
            except Exception:
                traceback.print_exc()
                return 2

        if store_dir:
            shutil.copytree(result.control, store.control, symlinks=True)
            shutil.copytree(result.experiment, store.experiment, symlinks=True)

        if diffoscope_args is None: # don't run diffoscope
            diffprogram = ['diff', '-ru', result.control, result.experiment]
            print("Running diff: ", diffprogram)
        else:
            diffprogram = ['diffoscope', result.control, result.experiment] + diffoscope_args
            print("Running diffoscope: ", diffprogram)

        retcode = run_or_tee(diffprogram, 'diffoscope.out', store_dir).returncode
        if retcode == 0:
            print("=======================")
            print("Reproduction successful")
            print("=======================")
            print("No differences in %s" % artifact_pattern, flush=True)
            run_or_tee(['find', '.', '-type', 'f', '-exec', 'sha256sum', '{}', ';'],
                'SHA256SUMS', store_dir,
                cwd=result.control)

            if store_dir:
                shutil.rmtree(store.experiment)
                os.symlink("control", store.experiment)
        else:
            # a slight hack, to trigger no_clean_on_error
            raise SystemExit(retcode)
        return retcode


COMMAND_LINE_OPTIONS = types.MappingProxyType(collections.OrderedDict([
    ('build_command', types.MappingProxyType({
        'default': None, 'nargs': '?', # 'type': str.split
        'help': 'Build command to execute, or "auto" to guess this - in '
                'the latter case then the subsequent argument will not be '
                'interpreted as an artifact but rather as the source to build, '
                'e.g. "." or some other path.'})),
    ('artifact', types.MappingProxyType({
        'default': None, 'nargs': '?',
        'help': 'Build artifact to test for reproducibility. May be a shell '
                'pattern such as "*.deb *.changes".'})),
    ('virtual_server_args', types.MappingProxyType({
        'default': None, 'nargs': '*',
        'help': 'Arguments to pass to the virtual_server, the first argument '
                'being the name of the server. If this itself contains options '
                '(of the form -xxx or --xxx), you should put a "--" between '
                'these arguments and reprotest\'s own options. '
                'Default: "null", to run directly in /tmp. Choices: %s' %
                ', '.join(get_all_servers())})),
    ('--help', types.MappingProxyType({
        'dest': 'help', 'default': None, 'const': True, 'nargs': '?',
        'choices': get_all_servers(),
        'metavar': 'VIRTUAL_SERVER_NAME',
        'help': 'Show this help message and exit. When given an argument, '
        'show instead the help message for that virtual server and exit. '})),
    ('--verbosity', types.MappingProxyType({
        'type': int, 'default': 0,
        'help': 'An integer.  Control which messages are displayed.'})),
    ('--source-root', types.MappingProxyType({
        'dest': 'source_root', 'type': pathlib.Path,
        'help': 'Root of the source tree, if not the '
        'current working directory.'})),
    ('--store-dir', types.MappingProxyType({
        'default': None, 'type': pathlib.Path,
        'help': 'Save the artifacts in this directory, which must be empty or '
        'non-existent. Otherwise, the artifacts will be deleted and you only '
        'see their hashes (if reproducible) or the diff output (if not).'})),
    ('--testbed-pre', types.MappingProxyType({
        'default': None, 'metavar': 'COMMANDS',
        'help': 'Shell commands to run before starting the test bed, in the '
        'context of the current system environment. This may be used to e.g. '
        'compute information needed by the build, where the computation needs '
        'packages you don\'t want installed in the testbed itself.'})),
    ('--testbed-init', types.MappingProxyType({
        'default': None, 'metavar': 'COMMANDS',
        'help': 'Shell commands to run after starting the test bed, but before '
        'applying variations. Used to e.g. install disorderfs in a chroot.'})),
    ('--auto-preset-expr', types.MappingProxyType({
        'default': "_", 'metavar': 'PYTHON_EXPRESSION',
        'help': 'This may be used to transform the presets returned by the '
        'auto-detection feature. The value should be a python expression '
        'that transforms the _ variable, which is of type reprotest.presets.ReprotestPreset. '
        'See that class\'s documentation for ways you can write this '
        'expression. Default: %(default)s'})),
    ('--variations', types.MappingProxyType({
        'type': lambda s: frozenset(s.split(',')),
        'default': frozenset(VARIATIONS.keys()),
        'help': 'Build variations to test as a comma-separated list '
        '(without spaces).  Default is to test all available '
        'variations: %s.' % ', '.join(VARIATIONS.keys())})),
    ('--dont-vary', types.MappingProxyType({
        'dest': 'dont_vary',
        'type': lambda s: frozenset(s.split(',')),
        'default': frozenset(),
        'help': 'Build variations *not* to test as a comma-separated '
        'list (without spaces).  These take precedence over what '
        'you set for "variations". Default is nothing, i.e. test '
        'whatever you set for "variations".'})),
    ('--diffoscope-arg', types.MappingProxyType({
        'default': [], 'action': 'append',
        'help': 'Give extra arguments to diffoscope when running it.'})),
    ('--no-diffoscope', types.MappingProxyType({
        'action': 'store_true', 'default': False,
        'help': 'Don\'t run diffoscope; instead run diff(1). Useful if you '
        'don\'t want to install diffoscope and/or just want a quick answer '
        'on whether the reproduction was successful or not, without spending '
        'time to compute all the detailed differences.'})),
    ('--no-clean-on-error', types.MappingProxyType({
        'action': 'store_true', 'default': False,
        'help': 'Don\'t clean the virtual_server if there was an error. '
                'Useful for debugging, but WARNING: this is currently not '
                'implemented very well and may leave cruft on your system.'})),
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

def command_line(*argv):
    arg_parser = argparse.ArgumentParser(
        description='Build packages and check them for reproducibility.',
        formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False)
    for option in COMMAND_LINE_OPTIONS:
        arg_parser.add_argument(option, **COMMAND_LINE_OPTIONS[option])
    args, remainder = arg_parser.parse_known_args(argv)

    # work around python issue 14191; this allows us to accept command lines like
    # $ reprotest build stuff --option=val --option=val -- schroot unstable-amd64-sbuild
    # where optional args appear in between positional args, but there must be a '--'
    if remainder:
        if remainder[0] != '--':
            # however we disallow split command lines that don't have '--', e.g.:
            # $ reprotest build stuff --option=val --option=val schroot unstable-amd64-sbuild
            # since it's too complex to support that in a way that's not counter-intuitive
            arg_parser.parse_args(argv)
        args.virtual_server_args = (args.virtual_server_args or []) + remainder[1:]
    args.virtual_server_args = args.virtual_server_args or ["null"]
    # print(args)

    if args.help:
        if args.help == True:
            arg_parser.print_help()
            sys.exit(0)
        else:
            sys.exit(subprocess.call([get_server_path(args.help), "-h"]))

    return types.MappingProxyType({k:v for k, v in vars(args).items() if v is not None})


def main():
    config_options = config()

    # Argparse exits with status code 2 if something goes wrong, which
    # is already the right status exit code for reprotest.
    command_line_options = command_line(*sys.argv[1:])

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
    no_clean_on_error = command_line_options.get(
        'no_clean_on_error',
        config_options.get('no_clean_on_error'))
    diffoscope_args = command_line_options.get('diffoscope_arg')
    if command_line_options.get('no_diffoscope'):
        diffoscope_args = None
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
    verbosity = command_line_options.get(
        'verbosity',
        config_options.get('verbosity', 0))

    if not build_command:
        print("No build command provided. See --help for options.")
        sys.exit(2)
    if not artifact:
        print("No build artifact to test for differences provided.")
        sys.exit(2)
    if not virtual_server_args:
        print("No virtual_server to run the build in specified.")
        sys.exit(2)
    logging.basicConfig(
        format='%(message)s', level=30-10*verbosity, stream=sys.stdout)

    store_dir = command_line_options.get("store_dir")
    testbed_pre = command_line_options.get("testbed_pre")
    testbed_init = command_line_options.get("testbed_init")

    if build_command == 'auto':
        source_root = os.path.normpath(os.path.dirname(artifact)) if os.path.isfile(artifact) else artifact
        auto_preset_expr = command_line_options.get("auto_preset_expr")
        values = presets.get_presets(artifact, virtual_server_args[0])
        values = eval(auto_preset_expr, {'_':values}, {})
        print(values)
        build_command = values.build_command
        artifact = values.artifact
        testbed_pre = values.testbed_pre
        testbed_init = values.testbed_init

    # print(build_command, artifact, virtual_server_args)
    return check(build_command, artifact, virtual_server_args, source_root,
                 no_clean_on_error, variations, store_dir, diffoscope_args,
                 testbed_pre, testbed_init)
