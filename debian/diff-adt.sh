#!/bin/bash
# Show the patches that we made to autopkgtest code, since when we imported it
exec git diff "$@" -M 557707f0432769e1f903545335869ded5ce881d2  -- reprotest/lib/* reprotest/{virt/,virt-subproc/adt-virt-}{chroot,lxc,lxd,null,qemu,schroot,ssh}
