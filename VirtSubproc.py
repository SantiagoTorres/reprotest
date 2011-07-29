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
import re as regexp

from Autopkgtest import *

debuglevel = None
progname = "<VirtSubproc>"
devnull_read = file('/dev/null','r')
caller = __main__
copy_timeout = 300

class Quit:
	def __init__(q,ec,m): q.ec = ec; q.m = m

class Timeout: pass
def alarm_handler(*a): raise Timeout()
def timeout_start(to): signal.alarm(to)
def timeout_stop(): signal.alarm(0)

class FailedCmd:
	def __init__(fc,e): fc.e = e

def debug(m):
	if not debuglevel: return
	print >> sys.stderr, progname+": debug:", m

def bomb(m):
	raise Quit(12, progname+": failure: %s" % m)

def ok(): print 'ok'

def cmdnumargs(c, ce, nargs=0, noptargs=0):
	if len(c) < 1+nargs:
		bomb("too few arguments to command `%s'" % ce[0])
	if noptargs is not None and len(c) > 1+nargs+noptargs:
		bomb("too many arguments to command `%s'" % ce[0])

def cmd_capabilities(c, ce):
	cmdnumargs(c, ce)
	return caller.hook_capabilities() + ['execute-debug']

def cmd_quit(c, ce):
	cmdnumargs(c, ce)
	raise Quit(0, '')

def cmd_close(c, ce):
	cmdnumargs(c, ce)
	if not downtmp: bomb("`close' when not open")
	cleanup()

def cmd_print_auxverb_command(c, ce): return print_command('auxverb', c, ce)
def cmd_print_shstring_command(c, ce): return print_command('shstring', c, ce)

def print_command(which, c, ce):
	global downs
	cmdnumargs(c, ce)
	if not downtmp: bomb("`print-%s-command' when not open" % which)
	cl = downs[which]
	if not len(cl):
		cl = ['sh','-c','exec "$@"','x'] + cl
	return [','.join(map(urllib.quote, cl))]

def preexecfn():
	caller.hook_forked_inchild()

def execute_raw(what, instr, timeout, *popenargs, **popenargsk):
	debug(" ++ %s" % string.join(popenargs[0]))
	sp = subprocess.Popen(preexec_fn=preexecfn, *popenargs, **popenargsk)
	if instr is None: popenargsk['stdin'] = devnull_read
	timeout_start(timeout)
	(out, err) = sp.communicate(instr)
	timeout_stop()
	if err: bomb("%s unexpectedly produced stderr output `%s'" %
			(what, err))
	status = sp.wait()
	return (status, out)

def execute(cmd_string, cmd_list=[], downp=False, outp=False, timeout=0):
	cmdl = cmd_string.split()

	if downp: perhaps_down = downs['auxverb']
	else: perhaps_down = []

	if outp: stdout = subprocess.PIPE
	else: stdout = None

	cmd = cmdl + cmd_list
	if len(perhaps_down): cmd = perhaps_down + cmd

	(status, out) = execute_raw(cmdl[0], None, timeout,
				cmd, stdout=stdout)

	if status: bomb("%s%s failed (exit status %d)" %
			((downp and "(down) " or ""), cmdl[0], status))

	if outp and out and out[-1]=='\n': out = out[:-1]
	return out

def cmd_open(c, ce):
	global downtmp
	cmdnumargs(c, ce)
	if downtmp: bomb("`open' when already open")
	caller.hook_open()
	opened1()
	downtmp = caller.hook_downtmp()
	return opened2()

def downtmp_mktemp():
	global downtmp
	return execute('mktemp -t -d', downp=True, outp=True)

def downtmp_remove():
	global downtmp
	execute('rm -rf --', [downtmp], downp=True)

perl_quote_re = regexp.compile('[^-+=_.,;:() 0-9a-zA-Z]')
def perl_quote_1chargroup(m): return '\\x%02x' % ord(m.group(0))
def perl_quote(s): return '"'+perl_quote_re.sub(perl_quote_1chargroup, s)+'"'

def opened1():
	global down, downkind, downs
	debug("downkind = %s, down = %s" % (downkind, `down`))
	if downkind == 'auxverb':
		downs = { 'auxverb': down,
			  'shstring': down + ['sh','-c'] }
	elif downkind == 'shstring':
		downs = { 'shstring': down,
			  'auxverb': ['perl','-e','''
			@cmd=('''+(','.join(map(perl_quote,down)))+''');
			my $shstring = pop @ARGV;
			s/'/'\\\\''/g foreach @ARGV;
			push @cmd, "'$_'" foreach @ARGV;
			my $argv0=$cmd[0];
			exec $argv0 @cmd;
			die "$argv0: $!";
		'''] }
	debug("downs = %s" % `downs`)

def opened2():
	global downtmp, downs
	debug("downtmp = %s" % (downtmp))
	return [downtmp]

def cmd_revert(c, ce):
	global downtmp
	cmdnumargs(c, ce)
	if not downtmp: bomb("`revert' when not open")
	if not 'revert' in caller.hook_capabilities():
		bomb("`revert' when `revert' not advertised")
	caller.hook_revert()
	opened1()
	downtmp = caller.hook_downtmp()
	return opened2()

def down_python_script(gobody, functions=''):
	# Many things are made much harder by the inability of
	# dchroot, ssh, et al, to cope without mangling the arguments.
	# So we run a sub-python on the testbed and feed it a script
	# on stdin.  The sub-python decodes the arguments.

	script = (	"import urllib\n"
			"import os\n"
			"import errno\n"
			"def setfd(fd,fnamee,write,mode=0666):\n"
			"	fname = urllib.unquote(fnamee)\n"
			"	if write: rw = os.O_WRONLY|os.O_CREAT|os.O_TRUNC\n"
			"	else: rw = os.O_RDONLY\n"
			"	nfd = os.open(fname, rw, mode)\n"
			"	if fd >= 0: os.dup2(nfd,fd)\n"
			+ functions +
			"def go():\n" )
	script += (	"	os.environ['TMPDIR']= urllib.unquote('%s')\n" %
				urllib.quote(downtmp)	)
	script += (	"	os.chdir(os.environ['TMPDIR'])\n" )
	script += (	gobody +
			"go()\n" )

	debug("+P ...\n"+script)

	scripte = urllib.quote(script)
	cmdl = (downs['shstring'] +
		["exec python -c 'import urllib; s = urllib.unquote(%s);"
		 " exec s'" % ('"%s"' % scripte)])
	return cmdl

def cmd_execute(c, ce):
	cmdnumargs(c, ce, 5, None)
	debug_re = regexp.compile('debug=(\d+)\-(\d+)$')
	debug_g = None
	timeout = 0
	envs = []
	for kw in ce[6:]:
		if kw.startswith('debug='):
			if debug_g: bomb("multiple debug= in execute")
			m = debug_re.match(kw)
			if not m: bomb("invalid execute debug arg `%s'" % kw)
			debug_g = m.groups()
		elif kw.startswith('timeout='):
			try: timeout = int(kw[8:],0)
			except ValueError: bomb("invalid timeout arg `%s'" %kw)
		elif kw.startswith('env='):
			es = kw[4:]; eq = es.find('=')
			if eq <= 0: bomb("invalid env arg `%s'" % kw)
			envs.append((es[:eq], es[eq+1:]))
		else: bomb("invalid execute kw arg `%s'" % kw)
		
	gobody = "	import sys\n"
	stdout = None
	tfd = None
	if debug_g:
		(tfd,hfd) = m.groups()
		tfd = int(tfd)
		gobody += "	os.dup2(1,%d)\n" % tfd
		stdout = int(hfd)
	for ioe in range(3):
		ioe_tfd = ioe
		if ioe == tfd: ioe_tfd = -1
		gobody += "	setfd(%d,'%s',%d)\n" % (
			ioe_tfd, ce[ioe+2], ioe>0 )
	for e in envs:
		gobody += ("	os.environ[urllib.unquote('%s')]"
			   " = urllib.unquote('%s')\n"
				% tuple(map(urllib.quote, e)))
	gobody += "	os.chdir(urllib.unquote('" + ce[5] +"'))\n"
	gobody += "	cmd = '%s'\n" % ce[1]
	gobody += ("	cmd = cmd.split(',')\n"
		"	cmd = map(urllib.unquote, cmd)\n"
		"	c0 = cmd[0]\n"
		"	if '/' in c0:\n"
		"		if not os.access(c0, os.X_OK):\n"
		"			status = os.stat(c0)\n"
		"			mode = status.st_mode | 0111\n"
		"			os.chmod(c0, mode)\n"
		"	try: os.execvp(c0, cmd)\n"
		"	except (IOError,OSError), e:\n"
		"		print >>sys.stderr, \"%s: %s\" % (\n"
		"			(c0, os.strerror(e.errno)))\n"
		"		os._exit(127)\n")
	cmdl = down_python_script(gobody)

	stdout_copy = None
	try:
		if type(stdout) == type(2): stdout_copy = os.dup(stdout)
		try:
			(status, out) = execute_raw('sub-python', None,
				timeout, cmdl, stdout=stdout_copy,
				stdin=devnull_read, stderr=subprocess.PIPE)
		except Timeout:
			raise FailedCmd(['timeout'])
	finally:
		if stdout_copy is not None: os.close(stdout_copy)

	if out: bomb("sub-python unexpected produced stdout"
			" visible to us `%s'" % out)
	return [`status`]

def copyupdown(c, ce, upp):
	cmdnumargs(c, ce, 2)
	isrc = 0
	idst = 1
	ilocal = 0 + upp
	iremote = 1 - upp
	wh = ce[0]
	sd = c[1:]
	sde = ce[1:]
	if not sd[0] or not sd[1]:
		bomb("%s paths must be nonempty" % wh)
	dirsp = sd[0][-1]=='/'
	functions = "import errno\n"
	if dirsp != (sd[1][-1]=='/'):
		bomb("% paths must agree about directoryness"
			" (presence or absence of trailing /)" % wh)
	localfd = None
	deststdout = devnull_read
	srcstdin = devnull_read
	if not dirsp:
		modestr = ''
		if upp:
			deststdout = file(sd[idst], 'w')
		else:
			srcstdin = file(sd[isrc], 'r')
			status = os.fstat(srcstdin.fileno())
			if status.st_mode & 0111: modestr = ',0777'
		gobody = "	setfd(%s,'%s',%s%s)\n" % (
					1-upp, sde[iremote], not upp, modestr)
		gobody += "	os.execvp('cat', ['cat'])\n"
		localcmdl = ['cat']
	else:
		gobody = "	dir = urllib.unquote('%s')\n" % sde[iremote]
		if upp:
			try: os.mkdir(sd[ilocal])
			except (IOError,OSError), oe:
				if oe.errno != errno.EEXIST: raise
		else:
			gobody += ("	try: os.mkdir(dir)\n"
				"	except (IOError,OSError), oe:\n"
				"		if oe.errno != errno.EEXIST: raise\n")
		gobody +=( "	os.chdir(dir)\n"
			"	tarcmd = 'tar -f -'.split()\n")
		localcmdl = 'tar -f -'.split()
		taropts = [None, None]
		taropts[isrc] = '-c .'
		taropts[idst] = '-p -x --no-same-owner'
		gobody += "	tarcmd += '%s'.split()\n" % taropts[iremote]
		localcmdl += ['-C',sd[ilocal]]
		localcmdl += taropts[ilocal].split()
		gobody += "	os.execvp('tar', tarcmd)\n";

	downcmdl = down_python_script(gobody, functions)

	if upp: cmdls = (downcmdl, localcmdl)
	else: cmdls = (localcmdl, downcmdl)

	debug(`["cmdls", `cmdls`]`)
	debug(`["srcstdin", `srcstdin`, "deststdout", `deststdout`, "devnull_read", devnull_read]`)

	subprocs = [None,None]
	debug(" +< %s" % string.join(cmdls[0]))
	subprocs[0] = subprocess.Popen(cmdls[0], stdin=srcstdin,
			stdout=subprocess.PIPE, preexec_fn=preexecfn)
	debug(" +> %s" % string.join(cmdls[1]))
	subprocs[1] = subprocess.Popen(cmdls[1], stdin=subprocs[0].stdout,
			stdout=deststdout, preexec_fn=preexecfn)
	subprocs[0].stdout.close()
	timeout_start(copy_timeout)
	for sdn in [1,0]:
		debug(" +"+"<>"[sdn]+"?");
		status = subprocs[sdn].wait()
		if not (status==0 or (sdn==0 and status==-13)):
			timeout_stop()
			bomb("%s %s failed, status %d" %
				(wh, ['source','destination'][sdn], status))
	timeout_stop()

def cmd_copydown(c, ce): copyupdown(c, ce, False)
def cmd_copyup(c, ce): copyupdown(c, ce, True)

def command():
	sys.stdout.flush()
	ce = sys.stdin.readline()
	if not ce: bomb('end of file - caller quit?')
	ce = ce.rstrip().split()
	c = map(urllib.unquote, ce)
	if not c: bomb('empty commands are not permitted')
	debug('executing '+string.join(ce))
	c_lookup = c[0].replace('-','_')
	try: f = globals()['cmd_'+c_lookup]
	except KeyError: bomb("unknown command `%s'" % ce[0])
	try:
		r = f(c, ce)
		if not r: r = []
		r.insert(0, 'ok')
	except FailedCmd, fc:
		r = fc.e
	print string.join(r)

signal_list = [	signal.SIGHUP, signal.SIGTERM,
		signal.SIGINT, signal.SIGPIPE ]

def sethandlers(f):
	for signum in signal_list: signal.signal(signum, f)

def cleanup():
	global downtmp, cleaning
	debug("cleanup...");
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
	global downtmp, cleaning
	downtmp = None
	def handler(sig, *any):
		cleanup()
		os.kill(os.getpid(), sig)
	sethandlers(handler)

def mainloop():
	try:
		while True: command()
	except Quit, q:
		error_cleanup()
		if q.m: print >> sys.stderr, q.m
		sys.exit(q.ec)
	except:
		error_cleanup()
		print >> sys.stderr, "Unexpected error:"
		traceback.print_exc()
		sys.exit(16)

def main():
	signal.signal(signal.SIGALRM, alarm_handler)
	ok()
	prepare()
	mainloop()
