# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import argparse
import subprocess
import sys
import tempfile

def build(command, artifact_name, temp):
    return_code = subprocess.call(command)
    if return_code != 0:
        sys.exit(2)
    else:
        with open(artifact_name, 'rb') as artifact:
            temp.write(artifact.read())
            temp.flush()

def check(build_command, artifact_name):
    with tempfile.TemporaryDirectory() as temp:
        build(build_command, artifact_name, open(temp + '/b1', 'wb'))
        build(build_command, artifact_name, open(temp + '/b2', 'wb'))
        sys.exit(subprocess.call(['diffoscope', temp + '/b1', temp + '/b2']))

def main():
    arg_parser = argparse.ArgumentParser(
        description='Build packages and check them for reproducibility.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    arg_parser.add_argument('build_command', help='Build command to execute.')
    arg_parse.add_argument(
        'artifact', help='Build artifact to test for reproducibility.')
    # Argparse exits with status code 2 if something goes wrong.
    args = arg_parser.parse_args()
    check(args.build_command.split(), args.artifact)
