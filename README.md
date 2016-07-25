Command Line Interface
=====================

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
help for the CLI, run `reprotest -h` or `reprotest --help`.  Here are
some sample command-line invocations for running reprotest on itself:

    reprotest 'python3 setup.py bdist' dist/reprotest-0.2.linux-x86_64.tar.gz null
    reprotest 'python3 setup.py bdist_wheel' dist/reprotest-0.2-py3-none-any.whl qemu /path/to/qemu.img
    reprotest 'debuild -b -uc -us' '../reprotest_0.2_all.deb' schroot unstable-amd64


Config File
===========

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
    artifact = dist/reprotest-0.2.tar.gz
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



Running the Tests
=================

The easiest way to run the tests is with
[Tox](https://pypi.python.org/pypi/tox).  Install it,
[Coverage](https://pypi.python.org/pypi/coverage), and
[pytest](https://pypi.python.org/pypi/pytest).  (On Debian, this can
be done with `apt-get install python3-coverage tox python3-pytest`.)
Next, setup the virtualization servers, for null (no virtualization),
schroot, and qemu.

Some of the following instructions rely on Debian utilities.  For
schroot, run `mk-sbuild --debootstrap-include=devscripts stable`.  (If
you aren't on `amd64`, you'll have to include `--arch`.)  For qemu,
first `apt-get install autopkgtest vmdebootstrap qemu`, then run:

    vmdebootstrap --verbose --serial-console --distribution=sid \
                 --customize=/usr/share/autopkgtest/setup-commands/setup-testbed \
                 --user=adt/adt --size=10000000000 --grub --image=adt-sid.raw
    qemu-img convert -O qcow2 adt-sid.raw  adt-sid.img
    rm adt-sid.raw

The last two commands reduce the size of the image but aren't strictly
necessary.  Move `adt-sid.img` to `linux/` under your home directory.

To log into the schroot and qemu containers, respectively, run:

    sudo schroot -c source:stable-amd64
    qemu-system-x86_64 -enable-kvm -drive file=~/linux/adt-sid.img,if=virtio -net user -net nic,model=virtio -m 1024

After replacing `~` with your home directory.

For the host system and the two containers, run:

    apt-get install disorderfs
    (Additionally for mk-sbuild stable,  enable the backports repository.)
    (Additionally for chroot, run:
    mknod -m 666 /dev/fuse c 10 229)
    apt-get install python3 python3-pip
    apt-get install locales-all

Then, clone the repository.  Go to the root of the repository, where
`tox.ini` is, and run `tox`.  For more verbose output, run `tox --
-s`.
