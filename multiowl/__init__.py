import os
import logging
import threading
import dbus, dbus.service
import importlib
import time

from . import config as configmgr # Must be imported after GTK

# FIXME
DATA_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..')
os.chdir(DATA_DIR)

# Based on http://www.eurion.net/python-snippets/snippet/Single%20Instance.html
# By Simon Vermeersch <simonvermeersch@gmail.com>, GPL.
DBUS_NAME = 'nandhp.multiowl'
DBUS_PATH = '/nandhp/multiowl'
class DBusService(dbus.service.Object):
    def __init__(self, app):
        name = dbus.service.BusName(DBUS_NAME, bus=dbus.SessionBus())
        dbus.service.Object.__init__(self, name, DBUS_PATH)
        self.app = app

    @dbus.service.method(dbus_interface=DBUS_NAME)
    def test(self):
        print "Alerted!"

    @dbus.service.method(dbus_interface=DBUS_NAME)
    def preferences(self):
        self.app.configurator.show()

MAIN_THREAD = threading.current_thread()

class AccountIconDataBase(object):
    def __init__(self, account, config):
        self.active = False
        self.count = None
        self.updated(0)

    def updated(self, strike):
        self.updatetime = (time.time(), self.updatetime[1]+1 if strike else 0)

class MailIconBase(object):
    INTERVAL = 3000

    def __init__(self, app):
        self.log = logging.getLogger('icon')
        self.app = app
        self.displaying = None
        self.base = None
        self._account_data_class = AccountIconDataBase
        self.check_obsolete_timer(180*1000)

    def refresh_tooltip(self, heading, accounts):
        raise NotImplementedError

    def refresh_display(self):
        raise NotImplementedError

    def refresh_account_icon(self, account):
        raise NotImplementedError

    def check_obsolete_timer(self, interval):
        raise NotImplementedError

    def cycle_timer(self, interval):
        raise NotImplementedError

    def notify(self, account=None):
        raise NotImplementedError

    def has_account(self, account, require_active=False):
        return account.icon == self and account.icondata and \
            (account.icondata.active or not require_active)

    def cycle(self):
        assert threading.current_thread() == MAIN_THREAD
        # Determine the next account to display
        keys = self.app.accountnames
        if self.displaying:
            pos = keys.index(self.displaying) + 1
        else:
            pos = 0
        permuted = keys[pos:] + keys[:pos]
        # Restrict the permuted list to active accounts
        eligible = [x for x in permuted if \
                    self.has_account(self.app.accounts[x], True)]
        if not eligible:
            # No active accounts
            self.displaying = None
        else:
            self.displaying = eligible[0]
        self.refresh_display()

    def update(self, account=None):
        assert threading.current_thread() == MAIN_THREAD

        # Monitor for dead threads
        if account:
            if account.icon != self:
                return
            account.icondata.updated(0)

        # Determine how many active accounts we have for this icon
        tooltip = []
        total_messages = 0
        current_active = False
        for name in self.app.accountnames:
            account = self.app.accounts[name]
            self.refresh_account_icon(account)
            count = account.count
            if count:
                tooltip.append("%s in %s" % (count, name))
                if type(count) is int:
                    total_messages += count
                if self.displaying and name == self.displaying:
                    current_active = True
                account.icondata.active = True
            else:
                account.icondata.active = False

        # Update the tooltip
        self.refresh_tooltip("%d new messages" % (total_messages,)
                             if total_messages > 0 else "No new messages",
                             tooltip)

        # Do we need to be cycling between accounts?
        self.log.info("%d active accounts" % len(tooltip))
        self.cycle_timer(self.INTERVAL if len(tooltip) > 1 else None)

        # If the currently displayed account has no mail, cycle immediately
        if (self.displaying and not current_active) or \
            (not self.displaying and tooltip):
            self.cycle()
        else:
            self.refresh_display()

    def check_obsolete(self):
        assert threading.current_thread() == MAIN_THREAD
        now = time.time()
        for account in self.app.accounts.values():
            if not self.has_account(account):
                continue
            when, strikes = account.icondata.updatetime
            if now > when + account.interval*3/2:
                # FIXME: use Network Manager
                if strikes >= 1:
                    account.log.warning("Respawning thread")
                    account.icondata.updated(0)
                    account.spawn_thread()
                else:
                    account.log.warning("Thread not responding")
                    account.icondata.updated(1)

    def add_account(self, account, config):
        assert threading.current_thread() == MAIN_THREAD
        assert not self.has_account(account)
        account.start(self._account_data_class(account, config))
        self.notify(account)

    def remove_account(self, account):
        assert threading.current_thread() == MAIN_THREAD
        assert self.has_account(account)
        account.stop()
        self.notify()

class MultiowlApp(object):
    def __init__(self, ui):
        self.log = logging.getLogger(self.__class__.__name__)
        self.lock = threading.Lock()
        self.ui = ui

        # Check single-instance
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        if dbus.SessionBus().request_name(DBUS_NAME) != \
           dbus.bus.REQUEST_NAME_REPLY_PRIMARY_OWNER:
            self.log.info("Sending message to existing instance")
            instance = dbus.SessionBus().get_object(DBUS_NAME, DBUS_PATH)
            trigger = instance.get_dbus_method('preferences')
            trigger()
            return
        self.service = DBusService(self)

        # Icons
        self.icons = {}

        # Accounts
        self.passwords = configmgr.PasswordManager()
        self.accounts = {}
        self.accountnames = []

        # Read configuration
        self.config = configmgr.Config()
        self.configurator = self.ui.MultiowlConfigurator(self) # FIXME
        self.add_accounts()

        self.ui.main()     # FIXME: Rewrite in Qt

    def add_account(self, config, index=None): #account, color):
        assert threading.current_thread() == MAIN_THREAD
        account_name = config['name']
        account_type = config['type'].lower()
        if not account_type.isalnum():
            self.log.error("Invalid account type: %s" % (config['type'],))
            return
        account_module = importlib.import_module('.account.' + account_type,
                                                 'multiowl')
        with self.lock:
            # Find icon for this account
            icon = config['icon']
            if icon not in self.icons:
                self.log.info("Creating new icon %s" % (icon,))
                self.icons[icon] = self.ui.MailIcon(self)
            icon = self.icons[icon]

            account = account_module.Account(config, icon)
            icon.add_account(account, config)

            if index is None:
                index = len(self.accountnames)
            self.accounts[account_name] = account
            self.accountnames.insert(index, account_name)

    def remove_account(self, account):
        assert threading.current_thread() == MAIN_THREAD
        account.icon.remove_account(account)
        del self.accounts[account.name]

    def add_accounts(self):
        for config in self.config.accounts():
            self.add_account(config)

