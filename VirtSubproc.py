# VirtSubproc is part of autopkgtest
# autopkgtest is a tool for testing Debian binary packages
#
# autopkgtest is Copyright (C) 2006-2007 Canonical Ltd.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
#
# See the file CREDITS for a full list of credits information (often
# installed as /usr/share/doc/autopkgtest/CREDITS).

import __main__

import sys
import os
import string
import urllib
import signal
import subprocess
import traceback
import errno
import time
import re
import pipes
import socket
import shutil

debuglevel = None
progname = "<VirtSubproc>"
devnull_read = file('/dev/null', 'r')
caller = __main__
copy_timeout = int(os.getenv('ADT_VIRT_COPY_TIMEOUT', '300'))

downtmp = None
down = None
downkind = None
downs = None
cleaning = False
in_mainloop = False


class Quit:

    def __init__(self, ec, m):
        self.ec = ec
        self.m = m


class Timeout(RuntimeError):
    pass


def alarm_handler(*a):
    raise Timeout()


def timeout_start(to):
    signal.signal(signal.SIGALRM, alarm_handler)
    signal.alarm(to)


def timeout_stop():
    signal.alarm(0)


class FailedCmd:

    def __init__(self, e):
        self.e = e


def debug(m):
    if not debuglevel:
        return
    print >> sys.stderr, progname + ": debug:", m


def bomb(m):
    if in_mainloop:
        raise Quit(12, progname + ": failure: %s" % m)
    else:
        sys.stderr.write(m)
        sys.stderr.write('\n')
        sys.exit(1)


def ok():
    print 'ok'


def cmdnumargs(c, ce, nargs=0, noptargs=0):
    if len(c) < 1 + nargs:
        bomb("too few arguments to command `%s'" % ce[0])
    if noptargs is not None and len(c) > 1 + nargs + noptargs:
        bomb("too many arguments to command `%s'" % ce[0])


def cmd_capabilities(c, ce):
    cmdnumargs(c, ce)
    return caller.hook_capabilities() + ['execute-debug']


def cmd_quit(c, ce):
    cmdnumargs(c, ce)
    raise Quit(0, '')


def cmd_close(c, ce):
    cmdnumargs(c, ce)
    if not downtmp:
        bomb("`close' when not open")
    cleanup()


def cmd_print_auxverb_command(c, ce):
    return print_command('auxverb', c, ce)


def cmd_print_shstring_command(c, ce):
    return print_command('shstring', c, ce)


def print_command(which, c, ce):
    global downs
    cmdnumargs(c, ce)
    if not downtmp:
        bomb("`print-%s-command' when not open" % which)
    cl = downs[which]
    if not len(cl):
        cl = ['sh', '-c', 'exec "$@"', 'x'] + cl
    return [','.join(map(urllib.quote, cl))]


def preexecfn():
    caller.hook_forked_inchild()


def execute_raw(what, instr, timeout, *popenargs, **popenargsk):
    debug(" ++ %s" % string.join(popenargs[0]))
    sp = subprocess.Popen(preexec_fn=preexecfn, *popenargs, **popenargsk)
    if instr is None:
        popenargsk['stdin'] = devnull_read
    timeout_start(timeout)
    try:
        (out, err) = sp.communicate(instr)
    except Timeout:
        sp.kill()
        sp.wait()
        raise
    timeout_stop()
    if err:
        bomb("%s unexpectedly produced stderr output `%s'" %
            (what, err))
    status = sp.wait()
    return (status, out)


def execute(cmd_string, cmd_list=[], downp=False, outp=False, timeout=0):
    cmdl = cmd_string.split()

    if downp:
        perhaps_down = downs['auxverb']
    else:
        perhaps_down = []

    if outp:
        stdout = subprocess.PIPE
    else:
        stdout = None

    cmd = cmdl + cmd_list
    if len(perhaps_down):
        cmd = perhaps_down + cmd

    (status, out) = execute_raw(cmdl[0], None, timeout,
                                cmd, stdout=stdout)

    if status:
        bomb("%s%s failed (exit status %d)" %
            ((downp and "(down) " or ""), cmdl[0], status))

    if outp and out and out[-1] == '\n':
        out = out[:-1]
    return out


class timeout:
    def __init__(self, secs, exit_msg=None):
        '''Context manager that times out after given number of seconds.

        If exit_msg is given, the program bomb()s with that message,
        otherwise it raises a Timeout exception.
        '''
        self.secs = secs
        self.exit_msg = exit_msg

    def __enter__(self):
        timeout_start(self.secs)

    def __exit__(self, type_, value, traceback):
        timeout_stop()
        if type_ is Timeout and self.exit_msg:
            bomb(self.exit_msg)
            return True
        return False


def get_unix_socket(path):
    '''Open a connected client socket to given Unix socket with a 5s timeout'''

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    with timeout(5, 'Timed out waiting for %s socket\n' % path):
        while True:
            try:
                s.connect(path)
                break
            except socket.error:
                continue
    return s


def expect(sock, search_str, timeout_sec, description=None):
    debug('expect: "%s"' % search_str)
    what = '"%s"' % (description or search_str or 'data')
    out = b''
    with timeout(timeout_sec,
                 description and ('timed out waiting for %s' % what) or None):
        while True:
            time.sleep(0.1)
            block = sock.recv(4096)
            #debug('expect: got block: %s' % block)
            out += block
            if search_str is None or search_str in out:
                debug('expect: found "%s"' % what)
                break


def cmd_open(c, ce):
    global downtmp
    cmdnumargs(c, ce)
    if downtmp:
        bomb("`open' when already open")
    caller.hook_open()
    opened1()
    downtmp = caller.hook_downtmp()
    return opened2()


def downtmp_mktemp():
    d = execute('mktemp -d /tmp/adt-run.XXXXXX', [], downp=True, outp=True)
    execute('chmod 1777', [d], downp=True)
    return d


def downtmp_remove():
    global downtmp
    execute('rm -rf --', [downtmp], downp=True)

perl_quote_re = re.compile('[^-+=_.,;:() 0-9a-zA-Z]')


def perl_quote_1chargroup(m):
    return '\\x%02x' % ord(m.group(0))


def perl_quote(s):
    return '"' + perl_quote_re.sub(perl_quote_1chargroup, s) + '"'


def opened1():
    global down, downkind, downs
    debug("downkind = %s, down = %s" % (downkind, str(down)))
    if downkind == 'auxverb':
        downs = {'auxverb': down,
                 'shstring': down + ['sh', '-c']}
    elif downkind == 'shstring':
        downs = {'shstring': down,
                 'auxverb': ['perl', '-e', '''
                @cmd=(''' + (','.join(map(perl_quote, down))) + ''');
                my $shstring = pop @ARGV;
                s/'/'\\\\''/g foreach @ARGV;
                push @cmd, "'$_'" foreach @ARGV;
                my $argv0=$cmd[0];
                exec $argv0 @cmd;
                die "$argv0: $!"''']}
    debug("downs = %s" % str(downs))


def opened2():
    global downtmp, downs
    debug("downtmp = %s" % (downtmp))
    return [downtmp]


def cmd_revert(c, ce):
    global downtmp
    cmdnumargs(c, ce)
    if not downtmp:
        bomb("`revert' when not open")
    if not 'revert' in caller.hook_capabilities():
        bomb("`revert' when `revert' not advertised")
    caller.hook_revert()
    opened1()
    downtmp = caller.hook_downtmp()
    return opened2()


def cmd_execute(c, ce):
    cmdnumargs(c, ce, 5, None)
    if not downtmp:
        bomb("`execute' when not open")
    debug_re = re.compile('debug=(\d+)\-(\d+)$')
    debug_g = None
    timeout = 0
    envs = []
    for kw in ce[6:]:
        if kw.startswith('debug='):
            if debug_g:
                bomb("multiple debug= in execute")
            m = debug_re.match(kw)
            if not m:
                bomb("invalid execute debug arg `%s'" % kw)
            debug_g = m.groups()
        elif kw.startswith('timeout='):
            try:
                timeout = int(kw[8:], 0)
            except ValueError:
                bomb("invalid timeout arg `%s'" % kw)
        elif kw.startswith('env='):
            es = kw[4:]
            eq = es.find('=')
            if eq <= 0:
                bomb("invalid env arg `%s'" % kw)
            envs.append((es[:eq], es[eq + 1:]))
        else:
            bomb("invalid execute kw arg `%s'" % kw)

    rune = 'set -e; exec '

    stdout = None
    tfd = None
    if debug_g:
        rune += " 3>&1"

    for ioe in range(3):
        rune += " %d%s%s" % (ioe, '<>'[ioe > 0],
                             pipes.quote(ce[ioe + 2]))
    if debug_g:
        (tfd, hfd) = m.groups()
        tfd = int(tfd)
        rune += " %d>&3 3>&-" % tfd
        stdout = int(hfd)

    rune += '; '

    rune += 'cd %s; ' % pipes.quote(ce[5])

    for e in envs:
        (en, ev) = map(urllib.unquote, e)
        rune += "%s=%s " % (en, pipes.quote(ev))

    cmdl = map(urllib.unquote, ce[1].split(','))
    rune += 'exec ' + ' '.join(map(pipes.quote, cmdl))

    cmdl = downs['shstring'] + [rune]

    stdout_copy = None
    try:
        if isinstance(stdout, int):
            stdout_copy = os.dup(stdout)
        try:
            (status, out) = execute_raw('target-cmd', None,
                                        timeout, cmdl, stdout=stdout_copy,
                                        stdin=devnull_read,
                                        stderr=subprocess.PIPE)
        except Timeout:
            raise FailedCmd(['timeout'])
    finally:
        if stdout_copy is not None:
            os.close(stdout_copy)

    if out:
        bomb("target command unexpected produced stdout"
             " visible to us `%s'" % out)
    return [str(status)]


def get_downtmp_host():
    '''Return host directory of the testbed's downtmp dir, if supported'''

    for cap in caller.hook_capabilities():
        if cap.startswith('downtmp-host='):
            return cap.split('=', 1)[1]
    return None


def copytree(src, dst):
    '''Like shutils.copytree(), but merges with existing dst'''

    if not os.path.exists(dst):
        shutil.copytree(src, dst, symlinks=True)
        return

    for f in os.listdir(src):
        fsrc = os.path.join(src, f)
        subprocess.check_call(['cp', '-r', '--preserve=timestamps,links', '-t',
                               dst, fsrc])


def copyup_shareddir(tb, host, is_dir, downtmp_host):
    debug('copyup_shareddir: tb %s, host %s, is_dir %s, downtmp_host %s' % (
        tb, host, is_dir, downtmp_host))

    host = os.path.normpath(host)
    tb = os.path.normpath(tb)
    downtmp_host = os.path.normpath(downtmp_host)

    timeout_start(copy_timeout)
    cp = None
    try:
        tb_tmp = None
        if tb.startswith(downtmp):
            # translate into host path
            tb = downtmp_host + tb[len(downtmp):]
        else:
            tb_tmp = os.path.join(downtmp, os.path.basename(host))
            debug('copyup_shareddir: tb path %s is not already in downtmp, '
                  'copying to %s' % (tb, tb_tmp))
            cp = subprocess.Popen(
                downs['auxverb'] + ['cp', '-r', '--preserve=timestamps,links',
                                    tb, tb_tmp], preexec_fn=preexecfn)
            cp.communicate()
            if cp.returncode != 0:
                bomb('copyup_shareddir: cp exited with code %i' %
                     cp.returncode)
            cp = None
            # translate into host path
            tb = os.path.join(downtmp_host, os.path.basename(host))

        if tb == host:
            tb_tmp = None
        else:
            debug('copyup_shareddir: tb(host) %s is not already at '
                  'destination %s, copying' % (tb, host))
            if is_dir:
                copytree(tb, host)
            else:
                shutil.copy(tb, host)

        if tb_tmp:
            debug('copyup_shareddir: cleaning intermediate copy: %s' % tb)
            subprocess.call(downs['auxverb'] + ['rm', '-rf', tb_tmp],
                            preexec_fn=preexecfn)
    finally:
        if cp:
            cp.kill()
            cp.wait()
        timeout_stop()


def copydown_shareddir(host, tb, is_dir, downtmp_host):
    debug('copydown_shareddir: host %s, tb %s, is_dir %s, downtmp_host %s' % (
        host, tb, is_dir, downtmp_host))

    host = os.path.normpath(host)
    tb = os.path.normpath(tb)
    downtmp_host = os.path.normpath(downtmp_host)

    timeout_start(copy_timeout)
    cp = None
    try:
        host_tmp = None
        if host.startswith(downtmp_host):
            # translate into tb path
            host = downtmp + host[len(downtmp_host):]
        else:
            host_tmp = os.path.join(downtmp_host, os.path.basename(tb))
            if is_dir:
                shutil.rmtree(host_tmp, ignore_errors=True)
                shutil.copytree(host, host_tmp, symlinks=True)
            else:
                shutil.copy(host, host_tmp)
            # translate into tb path
            host = os.path.join(downtmp, os.path.basename(tb))

        if host == tb:
            host_tmp = None
        else:
            subprocess.call(downs['auxverb'] + ['rm', '-rf', tb],
                            preexec_fn=preexecfn)
            cp = subprocess.Popen(
                downs['auxverb'] + ['cp', '-r', '--preserve=timestamps,links',
                                    host, tb], preexec_fn=preexecfn)
            cp.communicate()
            if cp.returncode != 0:
                bomb('copydown_shareddir: cp exited with code %i' %
                     cp.returncode)
            cp = None

        if host_tmp:
            (is_dir and shutil.rmtree or os.unlink)(host_tmp)
    finally:
        if cp:
            cp.kill()
            cp.wait()
        timeout_stop()


def copyupdown(c, ce, upp):
    cmdnumargs(c, ce, 2)
    if not downtmp:
        bomb("`copyup'/`copydown' when not open")
    isrc = 0
    idst = 1
    ilocal = 0 + upp
    iremote = 1 - upp
    wh = ce[0]
    sd = c[1:]
    if not sd[0] or not sd[1]:
        bomb("%s paths must be nonempty" % wh)
    dirsp = sd[0][-1] == '/'
    if dirsp != (sd[1][-1] == '/'):
        bomb("% paths must agree about directoryness"
             " (presence or absence of trailing /)" % wh)

    # if we have a shared directory, we just need to copy it from/to there; in
    # most cases, it's testbed end is already in the downtmp dir
    downtmp_host = get_downtmp_host()
    if downtmp_host:
        try:
            if upp:
                copyup_shareddir(sd[0], sd[1], dirsp, downtmp_host)
            else:
                copydown_shareddir(sd[0], sd[1], dirsp, downtmp_host)
        except Timeout:
            raise FailedCmd(['timeout'])
        return

    deststdout = devnull_read
    srcstdin = devnull_read
    remfileq = pipes.quote(sd[iremote])
    if not dirsp:
        rune = 'cat %s%s' % ('><'[upp], remfileq)
        if upp:
            deststdout = file(sd[idst], 'w')
        else:
            srcstdin = file(sd[isrc], 'r')
            status = os.fstat(srcstdin.fileno())
            if status.st_mode & 0111:
                rune += '; chmod +x -- %s' % (remfileq)
        localcmdl = ['cat']
    else:
        taropts = [None, None]
        taropts[isrc] = '-c .'
        taropts[idst] = '-p -x --no-same-owner'

        rune = 'cd %s; tar %s -f -' % (remfileq, taropts[iremote])
        if upp:
            try:
                os.mkdir(sd[ilocal])
            except (IOError, OSError), oe:
                if oe.errno != errno.EEXIST:
                    raise
        else:
            rune = ('if ! test -d %s; then mkdir -- %s; fi; ' % (
                remfileq, remfileq)
            ) + rune

        localcmdl = ['tar', '-C', sd[ilocal]] + (
            ('%s -f -' % taropts[ilocal]).split()
        )
    rune = 'set -e; ' + rune
    downcmdl = downs['shstring'] + [rune]

    if upp:
        cmdls = (downcmdl, localcmdl)
    else:
        cmdls = (localcmdl, downcmdl)

    debug(str(["cmdls", str(cmdls)]))
    debug(str(["srcstdin", str(srcstdin), "deststdout",
          str(deststdout), "devnull_read", devnull_read]))

    subprocs = [None, None]
    debug(" +< %s" % string.join(cmdls[0]))
    subprocs[0] = subprocess.Popen(cmdls[0], stdin=srcstdin,
                                   stdout=subprocess.PIPE,
                                   preexec_fn=preexecfn)
    debug(" +> %s" % string.join(cmdls[1]))
    subprocs[1] = subprocess.Popen(cmdls[1], stdin=subprocs[0].stdout,
                                   stdout=deststdout,
                                   preexec_fn=preexecfn)
    subprocs[0].stdout.close()
    try:
        timeout_start(copy_timeout)
        for sdn in [1, 0]:
            debug(" +" + "<>"[sdn] + "?")
            status = subprocs[sdn].wait()
            if not (status == 0 or (sdn == 0 and status == -13)):
                timeout_stop()
                bomb("%s %s failed, status %d" %
                    (wh, ['source', 'destination'][sdn], status))
        timeout_stop()
    except Timeout:
        for sdn in [1, 0]:
            subprocs[sdn].kill()
            subprocs[sdn].wait()
        raise FailedCmd(['timeout'])


def cmd_copydown(c, ce):
    copyupdown(c, ce, False)


def cmd_copyup(c, ce):
    copyupdown(c, ce, True)


def cmd_shell(c, ce):
    cmdnumargs(c, ce, 4)
    if not downtmp:
        bomb("`shell' when not open")
    try:
        caller.hook_shell(c[1], c[2], c[3], c[4])
    except AttributeError:
        raise FailedCmd(['not supported by virt server'])


def command():
    sys.stdout.flush()
    while True:
        try:
            ce = sys.stdin.readline()
            break
        except IOError as e:
            if e.errno == errno.EAGAIN:
                time.sleep(0.1)
                continue
            else:
                raise
    if not ce:
        bomb('end of file - caller quit?')
    ce = ce.rstrip().split()
    c = map(urllib.unquote, ce)
    if not c:
        bomb('empty commands are not permitted')
    debug('executing ' + string.join(ce))
    c_lookup = c[0].replace('-', '_')
    try:
        f = globals()['cmd_' + c_lookup]
    except KeyError:
        bomb("unknown command `%s'" % ce[0])
    try:
        r = f(c, ce)
        if not r:
            r = []
        r.insert(0, 'ok')
    except FailedCmd, fc:
        r = fc.e
    print string.join(r)

signal_list = [	signal.SIGHUP, signal.SIGTERM,
                signal.SIGINT, signal.SIGPIPE]


def sethandlers(f):
    for signum in signal_list:
        signal.signal(signum, f)


def cleanup():
    global downtmp, cleaning
    debug("cleanup...")
    sethandlers(signal.SIG_DFL)
    cleaning = True
    if downtmp:
        caller.hook_cleanup()
    cleaning = False
    downtmp = False


def error_cleanup():
    try:
        ok = False
        try:
            cleanup()
            ok = True
        except Quit, q:
            print >> sys.stderr, q.m
        except:
            print >> sys.stderr, "Unexpected cleanup error:"
            traceback.print_exc()
            print >> sys.stderr, ''
        if not ok:
            print >> sys.stderr, ("while cleaning up"
                                  " because of another error:")
    except:
        pass


def prepare():
    global downtmp
    downtmp = None

    def handler(sig, *any):
        cleanup()
        os.kill(os.getpid(), sig)
    sethandlers(handler)


def mainloop():
    global in_mainloop
    in_mainloop = True

    try:
        while True:
            command()
    except Quit, q:
        error_cleanup()
        if q.m:
            print >> sys.stderr, q.m
        sys.exit(q.ec)
    except:
        error_cleanup()
        print >> sys.stderr, "Unexpected error:"
        traceback.print_exc()
        sys.exit(16)
    finally:
        in_mainloop = False


def main():
    ok()
    prepare()
    mainloop()
