reprotest's CLI takes two mandatory arguments, the build command to
run and the build artifact file to test after running the build.  If
the build command or build artifact have spaces, they have to be
passed as strings, e.g. `"debuild -b -uc -us"`.  For optional
arguments, it has `--variations`, which accepts a list of possible
build variations to test, one or more of 'captures_environment',
'domain_host', 'filesystem', 'home', 'kernel', 'locales', 'path',
'shell', 'time', 'timezone', 'umask', and 'user_group' (see
[variations](https://tests.reproducible-builds.org/index_variations.html)
for more information); `--dont_vary`, which makes reprotest *not* test
any variations in the given list (the default is to run all
variations); `--source_root`, which accepts a directory to run the
build command in and defaults to the current working directory; and
--verbose, which will eventually enable more detailed logging.  To get
help for the CLI, run `reprotest -h` or `reprotest --help`.

The config file has one section, basics, and the same options as the
CLI, except there's no dont_vary option, and there are `build_command`
and `artifact` options.  If `build_command` and/or `artifact` are set
in the config file, reprotest can be run without passing those as
command-line arguments.  Command-line arguments always override config
file options.  Reprotest currently searches the working directory for
the config file, but it will also eventually search the user's home
directory.  A sample config file is below.

    [basics]
    build_command = setup.py sdist
    artifact = dist/reprotest-0.1.tar.gz
    source_root = reprotest/
    variations =
      captures_environment
      domain_host
      filesystem
      home
      host
      kernel
      locales
      path
      shell
      time
      timezone
      umask
      user_group
