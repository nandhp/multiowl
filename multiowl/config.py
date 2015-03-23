import logging

import os
from ConfigParser import RawConfigParser

CHECK_INTERVAL = 300

KEYRING_SERVICE = 'multiowl'

_CONFIG_DIR = os.environ.get('XDG_CONFIG_HOME',
                             os.path.join(os.path.expanduser('~'), '.config'))
CONFIG_FILE = os.path.join(_CONFIG_DIR, 'multiowl', 'config')

class PasswordManager(object):
    def __init__(self):
        self.passwords = {}
        self.keyring = __import__('keyring') # Must be imported after dbus, GTK
        self.log = logging.getLogger(self.__class__.__name__)

    def __getitem__(self, username):
        if username in self.passwords:
            return self.passwords[username]
        return None

    def load(self, username):
        #self.log.debug("Loading password for %s" % (username,))
        password = self.keyring.get_password(KEYRING_SERVICE, username)
        #if not password:
        #    self.log.warning("No password for %s" % (username,))
        self.passwords[username] = password

    def store(self, username, password):
        if password is None:
            #self.log.debug("Deleting password for %s" % (username,))
            self.keyring.delete_password(KEYRING_SERVICE, username)
            del self.passwords[username]
        else:
            #self.log.debug("Storing password for %s" % (username,))
            self.keyring.set_password(KEYRING_SERVICE, username, password)
            self.passwords[username] = password

    def get_keyring(self):
        return self.keyring.get_keyring()

class Config(RawConfigParser):
    def __init__(self):
        RawConfigParser.__init__(self)
        self.read(CONFIG_FILE)

    def accounts(self):
        for name in self.sections():
            config = dict(self.items(name))
            config['name'] = name
            if 'icon' not in config or not config['icon']:
                config['icon'] = 'default'
            if 'interval' not in config or not config['interval']:
                config['interval'] = 300
            else:
                config['interval'] = int(config['interval'])
            yield config
