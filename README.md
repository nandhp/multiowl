multiowl
========

Flexible mail notification icon for the system notification area, with support for multiple accounts.

I'm planning for it to be a lot like [mail-notification](http://www.nongnu.org/mailnotify/), but it's currently very rough around the edges.
In particular, there is no Settings dialog.

Therefore, you will need to create your own `~/.config/multiowl/config` file.
It should look like this:

    [example@gmail.com]
    type=Gmail
    username=example@gmail.com
    color=#ff0000

    [user@example.com]
    type=IMAP
    server=imap.example.com
    username=user
    color=#0000ff

Where Gmail and Google Apps accounts are supported under the `gmail` type, and IMAP accounts are supported under the `imap` type.

Passwords are stored using the [`keyring`](https://pypi.python.org/pypi/keyring) module, so they will probably end up in Gnome Keyring or something.
To set your passwords, use `multiowl_passwd.py` passing as a parameter one of the following:

  - For a Gmail account, use the username field from the configuration file
  - For an IMAP account, use the syntax username@server.
    For example, user@imap.example.com.

Eventually I may get around to making this easier by implementing a proper Settings dialog box.
And the ability to change passwords while MultiOwl is running.
