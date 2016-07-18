# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright
'''This files monkey-patches contextlib.ExitStack to work around
CPython bugs, https://bugs.python.org/issue25782 and
https://bugs.python.org/issue25786.  Assigning an exception
__context__ to itself causes CPython to hang.  This is currently
scheduled to be fixed in 3.5.3.  This code is from the standard
library contextlib, patched with
https://bugs.python.org/file41216/Issue25786.patch.

'''

from contextlib import *

import sys

def _new_exit():
    def __exit__(self, *exc_details):
        received_exc = exc_details[0] is not None

        # We manipulate the exception state so it behaves as though
        # we were actually nesting multiple with statements
        frame_exc = sys.exc_info()[1]
        def _fix_exception_context(new_exc, old_exc):
            # Context may not be correct, so find the end of the chain
            if new_exc is old_exc:
                return
            while 1:
                exc_context = new_exc.__context__
                if exc_context is old_exc:
                    # Context is already set correctly (see issue 20317)
                    return
                if exc_context is None or exc_context is frame_exc:
                    break
                new_exc = exc_context
            # Change the end of the chain to point to the exception
            # we expect it to reference
            new_exc.__context__ = old_exc

        # Callbacks are invoked in LIFO order to match the behaviour of
        # nested context managers
        suppressed_exc = False
        pending_raise = False
        while self._exit_callbacks:
            cb = self._exit_callbacks.pop()
            try:
                if cb(*exc_details):
                    suppressed_exc = True
                    pending_raise = False
                    exc_details = (None, None, None)
            except:
                new_exc_details = sys.exc_info()
                # simulate the stack of exceptions by setting the context
                _fix_exception_context(new_exc_details[1], exc_details[1])
                pending_raise = True
                exc_details = new_exc_details
        if pending_raise:
            try:
                # bare "raise exc_details[1]" replaces our carefully
                # set-up context
                fixed_ctx = exc_details[1].__context__
                raise exc_details[1]
            except BaseException:
                exc_details[1].__context__ = fixed_ctx
                raise
        return received_exc and suppressed_exc
    return __exit__

ExitStack.__exit__ = _new_exit()
