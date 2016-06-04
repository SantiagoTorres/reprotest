# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import argparse
import os
import subprocess
import sys
import tempfile

# TODO: what happens when these environment variables are set on
# Windows?  Hopefully nothing?

# TODO: if this locale doesn't exist on the system, Python's
# locales.getlocale() will return (None, None) rather than this
# locale.  I imagine it will also probably cause false positives with
# builds being reproducible when they aren't because of locale-based
# issues if this locale isn't installed.  The right solution here is
# for this locale to be encoded into the dependencies so installing it
# installs the right locale.  A weaker but still reasonable solution
# is to figure out what locales are installed (how?) and use another
# locale if this one isn't installed.  

# TODO: is this time zone location actually system-independent?

ENVIRONMENT_VARIABLES1 = {'TZ': '/usr/share/zoneinfo/Etc/GMT+12'}

ENVIRONMENT_VARIABLES2 = {'TZ': '/usr/share/zoneinfo/Etc/GMT-14', 'LANG': 'fr_CH.UTF-8', 'LC_ALL': 'fr_CH.UTF-8'}

def build(command, artifact_name, temp, **kws):
    return_code = subprocess.call(command, **kws)
    if return_code != 0:
        sys.exit(2)
    else:
        with open(artifact_name, 'rb') as artifact:
            temp.write(artifact.read())
            temp.flush()

def check(build_command, artifact_name):
    with tempfile.TemporaryDirectory() as temp:
        env = os.environ.copy()
        # print(env)
        env.update(ENVIRONMENT_VARIABLES1)
        # print(env)
        build(build_command, artifact_name, open(temp + '/b1', 'wb'),
              env=env)
        env.update(ENVIRONMENT_VARIABLES2)
        # print(env)
        build(build_command, artifact_name, open(temp + '/b2', 'wb'),
              env=env)
        sys.exit(subprocess.call(['diffoscope', temp + '/b1', temp + '/b2']))

def main():
    arg_parser = argparse.ArgumentParser(
        description='Build packages and check them for reproducibility.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    arg_parser.add_argument('build_command', help='Build command to execute.')
    arg_parse.add_argument(
        'artifact', help='Build artifact to test for reproducibility.')
    # Argparse exits with status code 2 if something goes wrong, which
    # is already the right status exit code for reprotest.
    args = arg_parser.parse_args()
    check(args.build_command.split(), args.artifact)
