# VirtSubproc is part of autopkgtest
# autopkgtest is a tool for testing Debian binary packages
#
# autopkgtest is Copyright (C) 2006 Canonical Ltd.
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

debuglevel = None
progname = "<VirtSubproc>"
devnull_read = file('/dev/null','r')
caller = __main__

class Quit:
	def __init__(q,ec,m): q.ec = ec; q.m = m

def debug(m):
	if not debuglevel: return
	print >> sys.stderr, progname+": debug:", m

def bomb(m):
	raise Quit(12, progname+": failure: %s" % m)

def ok(): print 'ok'

def cmdnumargs(c, ce, nargs=0):
	if len(c) == nargs + 1: return
	bomb("wrong number of arguments to command `%s'" % ce[0])

def cmd_capabilities(c, ce):
	cmdnumargs(c, ce)
	return caller.hook_capabilities()

def cmd_quit(c, ce):
	cmdnumargs(c, ce)
	raise Quit(0, '')

def execute_raw(what, instr, *popenargs, **popenargsk):
	debug(" ++ %s" % string.join(popenargs[0]))
	sp = subprocess.Popen(*popenargs, **popenargsk)
	if instr is None: popenargsk['stdin'] = devnull_read
	(out, err) = sp.communicate(instr)
	if err: bomb("%s unexpectedly produced stderr output `%s'" %
			(what, err))
	status = sp.wait()
	return (status, out)

def execute(cmd_string, cmd_list=[], downp=False, outp=False):
	cmdl = cmd_string.split()

	if downp: perhaps_down = down
	else: downp = []

	if outp: stdout = subprocess.PIPE
	else: stdout = None

	cmd = cmdl + cmd_list
	if len(perhaps_down): cmd = perhaps_down + [' '.join(cmd)]

	(status, out) = execute_raw(cmdl[0], None, cmd, stdout=stdout)

	if status: bomb("%s%s failed (exit status %d)" %
			((downp and "(down) " or ""), cmdl[0], status))

	if outp and out and out[-1]=='\n': out = out[:-1]
	return out

def cmd_open(c, ce):
	global downtmp
	cmdnumargs(c, ce)
	if downtmp: bomb("`open' when already open")
	downtmp = caller.hook_open()
	return [downtmp]

def cmd_reset(c, ce):
	cmdnumargs(c, ce)
	if not downtmp: bomb("`reset' when not open")
	if not 'revert' in caller.hook_capabilities():
		bomb("`reset' when `revert' not advertised")
	caller.hook_reset()

def down_python_script(gobody, functions=''):
	# Many things are made much harder by the inability of
	# dchroot, ssh, et al, to cope without mangling the arguments.
	# So we run a sub-python on the testbed and feed it a script
	# on stdin.  The sub-python decodes the arguments.

	script = (	"import urllib\n"
			"import os\n"
			"def setfd(fd,fnamee,write,mode=0666):\n"
			"	fname = urllib.unquote(fnamee)\n"
			"	if write: rw = os.O_WRONLY|os.O_CREAT\n"
			"	else: rw = os.O_RDONLY\n"
			"	nfd = os.open(fname, rw, mode)\n"
			"	os.dup2(nfd,fd)\n"
			+ functions +
			"def go():\n" )
	script += (	"	os.environ['TMPDIR']= urllib.unquote('%s')\n" %
				urllib.quote(downtmp)	)
	script += (	"	os.chdir(os.environ['TMPDIR'])\n" )
	script += (	gobody +
			"go()\n" )

	debug("+P ...\n"+script)

	scripte = urllib.quote(script)
	cmdl = down + ['python','-c',
		"'import urllib; s = urllib.unquote(%s); exec s'" %
			('"%s"' % scripte)]
	return cmdl

def cmd_execute(c, ce):
	cmdnumargs(c, ce, 5)
	gobody = "	import sys\n"
	for ioe in range(3):
		gobody += "	setfd(%d,'%s',%d)\n" % (
			ioe, ce[ioe+2], ioe>0 )
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
		"	except OSError, e:\n"
		"		print >>sys.stderr, \"%s: %s\" % (\n"
		"			(c0, os.strerror(e.errno)))\n"
		"		os._exit(127)\n")
	cmdl = down_python_script(gobody)

	(status, out) = execute_raw('sub-python', None, cmdl,
				stdin=devnull_read, stderr=subprocess.PIPE)
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
	preexecfns = [None, None]
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
			except OSError, oe:
				if oe.errno != errno.EEXIST: raise
		else:
			gobody += ("	try: os.mkdir(dir)\n"
				"	except OSError, oe:\n"
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
			stdout=subprocess.PIPE, preexec_fn=preexecfns[0])
	debug(" +> %s" % string.join(cmdls[1]))
	subprocs[1] = subprocess.Popen(cmdls[1], stdin=subprocs[0].stdout,
			stdout=deststdout, preexec_fn=preexecfns[1])
	for sdn in [1,0]:
		status = subprocs[sdn].wait()
		if status: bomb("%s %s failed, status %d" %
			(wh, ['source','destination'][sdn], status))

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
	try: f = globals()['cmd_'+c[0]]
	except KeyError: bomb("unknown command `%s'" % ce[0])
	r = f(c, ce)
	if not r: r = []
	r.insert(0, 'ok')
	ru = map(urllib.quote, r)
	print string.join(ru)

def cleanup():
	global downtmp, cleaning
	cleaning = True
	if downtmp: caller.hook_cleanup()
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
	signal_list = [	signal.SIGHUP, signal.SIGTERM,
			signal.SIGINT, signal.SIGPIPE ]
	def sethandlers(f):
		for signum in signal_list: signal.signal(signum, f)
	def handler(sig, *any):
		sethandlers(signal.SIG_DFL)
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
	debug("down = %s" % string.join(down))
	ok()
	prepare()
	mainloop()
