# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import sys
import time
import errno
import os

summary_stream = None
verbosity = 1  # 0: quiet (warning/error only), 1: info, 2: debug
enable_colors = None


def log(message, level, prefix='', timestamp=False, color=None):
    '''Write a log message to stderr'''

    if level > verbosity:
        return

    # needs lazy initialization as it may be redirected to tee
    global enable_colors
    if enable_colors is None:
        enable_colors = os.isatty(sys.stderr.fileno())

    head = sys.argv[0].split('/')[-1]
    if timestamp:
        head += ' [%s]: ' % time.strftime('%H:%M:%S')
    else:
        head += ': '

    if prefix:
        head += prefix + ': '

    out = (head + message + '\n').encode('UTF-8')

    if color is not None and enable_colors:
        out = b'\033[3' + chr(47 + color).encode() + b'm' + out + b'\033[0m'

    # we sometimes hit EAGAIN here, try a few times
    retries = 10
    while retries >= 0:
        try:
            sys.stderr.buffer.write(out)
            break
        except IOError as e:
            if e.errno == errno.EAGAIN:
                retries -= 1
                time.sleep(0.05)
            else:
                raise
    sys.stderr.buffer.flush()


def error(message):
    log(message, 0, prefix='ERROR', timestamp=True, color=2)


def warning(message):
    log(message, 0, prefix='WARNING', color=5)


def info(message):
    log(message, 1, timestamp=True, color=4)


def debug(message):
    log(message, 2, prefix='DBG', timestamp=False, color=8)


def psummary(m):
    if summary_stream is not None:
        summary_stream.write(m.encode('UTF-8'))
        summary_stream.write(b'\n')


def preport(m):
    sys.stdout.buffer.write(m.encode('UTF-8'))
    sys.stdout.buffer.write(b'\n')
    sys.stdout.buffer.flush()
    psummary(m)


def report(tname, result):
    preport('%-20s %s' % (tname, result))


#
# Errors
#

class TestbedFailure(RuntimeError):
    pass


class BadPackageError(RuntimeError):
    pass


class AutopkgtestError(RuntimeError):
    pass


def bomb(m):
    raise AutopkgtestError(m)


def badpkg(m):
    raise BadPackageError(m)
