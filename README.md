Introduction
============

reprotest is a command-line tool for building the same source code
in different environments.  It builds two binaries then checks the
binaries produced to see if changing the environment, without changing
the source code, changed the produced binaries.  reprotest can run
builds on an existing system but can also do so with virtualization.



Command Line Interface
=====================

reprotest's CLI takes at least three mandatory arguments, the build
command to run, a build artifact file to test for differences after
running the build, and an argument or arguments describing the
virtualization tool.  If the build command or build artifact have
spaces, they have to be passed as strings, e.g. `"python3 setup.py
sdist"`.  The valid values for the kind of virtualization are null (no
virtualization), chroot, schroot, and qemu (http://qemu.org/).
(reprotest will also accept lxc and lxd, for Linux containers, and
ssh, for a remote server, but these are as-yet untested and may not
work.)  The non-null virtualization arguments require an additional
argument, either the path to the chroot or qemu image or the name of
the schroot.  Here are some sample command-line invocations for
running reprotest on itself:

    reprotest 'python3 setup.py bdist' dist/reprotest-0.2.linux-x86_64.tar.gz null
    reprotest 'python3 setup.py sdist 2>/dev/null' dist/reprotest-0.2.tar.gz chroot /path/to/chroot
    reprotest 'python3 setup.py bdist_wheel' dist/reprotest-0.2-py3-none-any.whl qemu /path/to/qemu.img
    reprotest 'debuild -b -uc -us' ../reprotest_0.2_all.deb schroot unstable-amd64

For optional arguments, it has `--variations`, which accepts a list of
possible build variations to test, one or more of
'captures_environment', 'file_ordering', 'home', 'kernel', 'locales',
'path', 'time', 'time_zone', and 'umask' (see
https://tests.reproducible-builds.org/index_variations.html for more
information); `--dont_vary`, which makes reprotest *not* test any
variations in the given list (the default is to run all variations);
`--source_root`, which accepts a path to use as a directory to copy
the source from and run the build command in, and defaults to the
current working directory; and --verbose, which will eventually enable
more detailed logging.  To get help for the CLI, run `reprotest -h` or
`reprotest --help`.

reprotest accepts additional optional arguments for the
virtualization.  It uses virtualization code from autopkgtest
(https://people.debian.org/~mpitt/autopkgtest/README.virtualisation-server.html),
so accepts the same optional virtualization arguments as described in
http://manpages.ubuntu.com/manpages/xenial/man1/adt-virt-null.1.html
http://manpages.ubuntu.com/manpages/xenial/man1/adt-virt-chroot.1.html,
http://manpages.ubuntu.com/manpages/xenial/man1/adt-virt-schroot.1.html,
and
http://manpages.ubuntu.com/manpages/xenial/man1/adt-virt-qemu.1.html.



Config File
===========

reprotest will read a config file from the current working directory.
This config file has one section, basics, and the same options as the
CLI except that it also has `build_command`, `artifact`, and
`virtualization_args` options.  If `build_command`, `artifact`, and/or
`virtualization_args` are set in the config file, reprotest can be run
without passing those as command-line arguments.  Command-line
arguments always override config file options.  A sample config file
is below.

    [basics]
    build_command = python3 setup.py sdist
    artifact = dist/reprotest-0.2.tar.gz
    source_root = reprotest/
    virtualization_args = qemu /path/to/qemu.img
    variations =
      captures_environment
      file_ordering
      home
      kernel
      locales
      path
      time_zone
      umask



Setting up a Virtualization Environment
=======================================

To set up a virtualization for using reprotest, first set up the
environment (chroot, schroot, or qemu).  For Debian, the autopkgtest
documentation recommends using mk-sbuild for schroot or vmdebootstrap
for qemu.  (autopkgtest also includes a script to use in setting up
qemu with vmdebootstrap, `setup-testbed`.)

1. Install the `fr_CH.UTF-8` locale.  (`apt-get install locales-all`
on Debian/Ubuntu.)

2. Install disorderfs
(https://anonscm.debian.org/cgit/reproducible/disorderfs.git). (`apt-get
install disorderfs` on Debian/Ubuntu) In chroots, also run `mknod -m
666 /dev/fuse c 10 229`.  (disorderfs is Linux-specific at the moment.
For non-Linux systems, use `--dont-vary=file_ordering` instead.)




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
