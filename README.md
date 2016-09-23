Command Line Interface
======================

reprotest's CLI takes two mandatory arguments, the build command to
run and the build artifact file/pattern to test after running the
build. Here are some sample invocations for running reprotest on
itself:

    reprotest 'python3 setup.py bdist' 'dist/*.tar.gz'
    reprotest 'python3 setup.py bdist_wheel' 'dist/*.whl' qemu /path/to/qemu.img
    reprotest 'debuild -b -uc -us' '../*.deb' schroot unstable-amd64
    reprotest 'debuild -b -uc -us' '../*.deb' -- null -d

When using reprotest from a shell:

If the build command has spaces, you will need to quote them, e.g.
`reprotest "debuild -b -uc -us" [..]`.

If you want to use several build artifact patterns, you will also
need to quote them, e.g. `reprotest [..] "*.tar.gz *.tar.xz"`.

If your build artifacts have spaces in their names, you will need to
quote these twice, e.g. `'"a file with spaces.gz"'` for a single
artifact or `'"dir 1"/* "dir 2"/*'` for multiple patterns.

To get more help for the CLI, including documentation on optional
arguments and what they do, run `reprotest --help`.


Config File
===========

The config file has one section, basics, and the same options as the
CLI, except there's no dont_vary option, and there are `build_command`
and `artifact` fields.  If `build_command` and/or `artifact` are set
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

To run the tests, go to the root of the repository, where `tox.ini` is
and run `tox`.  For more verbose output, run `tox -- -s`.

However, this runs the tests with no virtualization. To test that
reprotest works correctly with virtualization, you'll need to setup
the virtualization servers schroot and qemu.

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

Now, finally run the tests:

    REPROTEST_TEST_SERVERS=null,qemu,schroot tox -- -s
