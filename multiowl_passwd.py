#!/usr/bin/env python

"""multiowl_passwd - Temporary program for storing passwords in keyring."""

import sys, getpass, multiowl

if len(sys.argv) < 2:
    print "Usage: %s <username> [...]" % (sys.argv[0])
    sys.exit(1)

import keyring
print "Using", keyring.get_keyring()
for username in sys.argv[1:]:
    prompt = "Password for %s: " % username
    password = getpass.getpass(prompt).strip()
    if not password:
        print "Deleting password for %s" % username
        password = None
    else:
        print "Storing new password for %s" % username
    multiowl.store_password(username, password)
