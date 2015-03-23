import logging

import threading
import time

class Account(object):
    def __init__(self, config, icon):
        self.name = config['name']
        self.log = logging.getLogger(self.name)
        self.interval = config['interval']

        self._count = '?'
        self._password = None
        self._thread = None

        self.icon = icon
        self.icondata = None
        self.app = icon.app

    def start(self, icondata=None):
        self.icondata = icondata
        self.spawn_thread()

    def stop(self):
        # FIXME: clarify relationship between account, icon, icondata
        raise Exception
        #account.icondata = None
        #account.icon = None
        #if self._thread:
        #    self._thread.abort = True

    def spawn_thread(self):
        if self._thread:
            self._thread.abort = True
            self.log.warning("Aborting existing thread")
        self._thread = CheckerThread(self)
        self._thread.start()

    @property
    def password(self):
        if self._password:
            return self.app.passwords[self._password]
        self.log.warning("No password loaded for this account")
        return None

    @password.setter
    def password(self, value):
        self._password = value
        self.log.debug("Loading password for %s" % (self._password,))
        self.app.passwords.load(self._password)

    @property
    def count(self):
        return self._count

    @count.setter
    def count(self, value):
        self._count = value
        self.log.info("Got %s messages" % (self._count,))
        if self.icon:
            self.icon.notify(self)

    def check(self):
        raise NotImplementedError

    def watch(self):
        while True:
            yield self.check()
            time.sleep(self.interval)

class CheckerThread(threading.Thread):
    def __init__(self, account):
        super(CheckerThread, self).__init__()
        self.account = account
        self.abort = False

        self.daemon = True      # Exit with main thread
        # self.start()

    def run(self):
        # Fetch/monitor unread count
        while not self.abort:
            minwatch = time.time() + self.account.interval
            try:
                watcher = self.account.watch()
                for count in watcher:
                    if self.abort:
                        break
                    self.account.count = count
            except KeyboardInterrupt:
                break
            except Exception:
                self.account.log.exception("Got an exception")
                self.account.count = '?'
            try:
                diffwatch = minwatch - time.time()
                if diffwatch > 0:
                    self.account.log.info("Waiting %d sec before retrying" %
                                          (diffwatch,))
                    time.sleep(diffwatch)
            except KeyboardInterrupt:
                break
        self.account.log.warning("Thread exiting")
