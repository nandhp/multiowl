#!/usr/bin/env python

""" multiowl.py - Mail checker for system notification area with enhanced
                  support for multiple accounts."""

import threading
import time
import logging
#from collections import OrderedDict
from ConfigParser import RawConfigParser
import os

# For icon (GTK+ version)
import gtk, gobject, cairo
from StringIO import StringIO
import dbus, dbus.service, dbus.mainloop.glib

# For Gmail
import urllib2, httplib, socket, ssl
from xml.dom import minidom

# For IMAP
import imaplib
from contextlib import contextmanager
import select

DATA_DIR = os.path.dirname(os.path.realpath(__file__))
os.chdir(DATA_DIR)

gtk.threads_init()

_CONFIG_DIR = os.environ.get('XDG_CONFIG_HOME',
                             os.path.join(os.path.expanduser('~'), '.config'))
CONFIG_FILE = os.path.join(_CONFIG_DIR, 'multiowl', 'config')

KEYRING_SERVICE = 'multiowl'

CHECK_INTERVAL = 300

def _make_pixbuf(base, color, label):
    # Convert the base image to a Cairo surface
    size = base.get_width()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    gdkctx = gtk.gdk.CairoContext(cr)
    gdkctx.set_source_pixbuf(base, 0, 0)
    gdkctx.paint()

    # Measure text
    my_str = unicode(label)
    cr.move_to(size, size)
    cr.select_font_face('sans', cairo.FONT_SLANT_NORMAL,
                        cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(10.0*size/22)
    extents = cr.text_extents(my_str) # x_bearing,y_bearing, width,height, ...
    #print label, extents
    text_width = extents[2] #+extents[0]
    text_height = extents[3] #-extents[1]
    text_margin = 2

    # Draw a color-coded 3D box around the text
    cr.set_line_width(1.0*size/22)
    rect_width = text_width+text_margin*2-1
    rect_height = text_height+text_margin*2
    # Bottom and left
    cr.move_to(size-.5, size-.5-rect_height)
    cr.rel_line_to(0, rect_height)
    cr.rel_line_to(-rect_width, 0)
    cr.set_source_rgb(0, 0, 0) #r*0.5, r*0.5, r*0.5)
    cr.stroke()
    # Top and right
    cr.move_to(size-0.5, size-0.5-rect_height)
    cr.rel_line_to(-rect_width, 0)
    cr.rel_line_to(0, rect_height)
    cr.set_source_rgb(1, 1, 1) #min(1,r*1.5), min(1,r*1.5), min(1, r*1.5))
    cr.stroke()
    # Fill
    cr.set_source_rgb(color[0], color[1], color[2])
    cr.rectangle(size-0.5, size-0.5, -rect_width, -rect_height)
    cr.fill()
    # Alternative: get colors from the GTK style

    # Determine whether black or white text will contrast better.
    # Via http://24ways.org/2010/calculating-color-contrast/
    yiq = color[0]*0.299 + color[1]*0.587 + color[2]*0.114
    if yiq >= .5:
        cr.set_source_rgb(0, 0, 0)
    else:
        cr.set_source_rgb(1, 1, 1)

    # Draw the text
    cr.move_to(size-text_width-text_margin-1, size-text_margin)
    #cr.move_to((size-text_width-1)/2.0, (size+text_height)/2.0)
    cr.show_text(my_str)

    # Render back to a GdkPixbuf via a PNG (!)
    png = StringIO()
    surface.write_to_png(png)
    loader = gtk.gdk.PixbufLoader()
    loader.write(png.getvalue())
    # Is this sufficient to ensure the image is completely loaded?
    loader.close()
    return loader.get_pixbuf()

def _make_color(colorstr):
    color = gtk.gdk.color_parse(colorstr)
    return (color.red_float, color.green_float, color.blue_float)

class MailIcon(gtk.StatusIcon):
    INTERVAL = 3000

    def __init__(self, app):
        super(MailIcon, self).__init__()
        self.log = logging.getLogger('icon')
        self.app = app
        self.connect('size-changed', self.resize_icon)
        self.nactive = 0
        self.displaying = None
        self.cycler = None
        self._lock = threading.Lock()
        self.base = None
        self.pixbufcache = {}
        self.updatetime = {}
        gobject.timeout_add(60*1000, self.check_obsolete)

    def lock(self):
        self._lock.acquire()
        gtk.threads_enter()

    def unlock(self):
        gtk.threads_leave()
        self._lock.release()

    def resize_icon(self, the_icon, size):
        self.log.debug("Now %d pixels" % size)
        self.base = gtk.gdk.pixbuf_new_from_file_at_size('mail.svg',
                                                         size, size)
        #self.base = gtk.gdk.pixbuf_new_from_icon_name('mail-unread')
        self.set_from_pixbuf(self.base)
        # Wait for icon to realize, else bad things happen (the Cairo text
        # API will have trouble with text)
        gobject.timeout_add(1, self.update_icon)
        return True

    def update_icon(self):
        if self.displaying:
            self.set_from_pixbuf(self.pixbufcache[self.displaying])
            self.set_visible(True)
        else:
            self.set_visible(False)

    def cycle(self):
        # Determine the next account to display
        keys = self.app.accountnames
        if self.displaying:
            pos = keys.index(self.displaying) + 1
        else:
            pos = 0
        permuted = keys[pos:] + keys[:pos]
        # Restrict the permuted list to active accounts
        eligible = [x for x in permuted if x in self.pixbufcache and
                    self.pixbufcache[x]]
        if not eligible:
            # No active accounts
            self.displaying = None
        else:
            self.displaying = eligible[0]
        self.update_icon()
        return True

    def auto_cycle(self):
        self.lock()
        self.cycle()
        self.unlock()
        return True

    def update_happened(self, updated=None):
        self.lock()
        # Determine how many active accounts we have for this icon
        new_active = 0
        new_tooltip = ["No new messages"]
        new_total = 0
        current_active = False
        for name in self.app.accountnames:
            account = self.app.accounts[name]
            count = account.count
            if count:
                self.pixbufcache[name] = _make_pixbuf(self.base,
                                                      account.color,
                                                      account.count)
                new_tooltip.append("  %s in %s" % (count, name))
                if type(count) is int:
                    new_total += count
                if self.displaying and name == self.displaying:
                    current_active = True
                new_active += 1
            else:
                self.pixbufcache[name] = None

        # Update the tooltip
        if new_total > 0:
            new_tooltip[0] = "<b>%d new messages</b>" % new_total
        self.set_tooltip_markup('\n'.join(new_tooltip))

        # Do we need to be cycling between accounts?
        self.log.info("%d active accounts" % new_active)
        if new_active > 1:
            if not self.cycler:
                self.cycler = gobject.timeout_add(self.INTERVAL, \
                    self.auto_cycle)
        elif self.cycler:
            gobject.source_remove(self.cycler)
            self.cycler = None

        # Monitor for dead threads
        if updated:
            if updated not in self.updatetime:
                self.updatetime[updated] = [time.time(), 0]
            else:
                self.updatetime[updated][0] = time.time()
                self.updatetime[updated][1] = 0

        # If the currently displayed account has no mail, cycle immediately
        if (self.displaying and not current_active) or \
            (not self.displaying and new_active > 0):
            self.cycle()
        else:
            self.update_icon()

        self.unlock()

    def check_obsolete(self):
        now = time.time()
        self.lock()
        for name in self.app.accountnames:
            if name not in self.updatetime:
                self.updatetime[name] = [now, 0]
            when, strikes = self.updatetime[name]
            if now > when + CHECK_INTERVAL*3/2:
                # FIXME: use Network Manager
                if strikes >= 1:
                    self.app.accounts[name].log.warning("Respawning thread")
                    del self.updatetime[name]
                    self.app.accounts[name].spawn_thread()
                else:
                    self.app.accounts[name].log.warning("Thread not responding")
                    self.updatetime[name][0] = now
                    self.updatetime[name][1] += 1
        self.unlock()
        return True

class Account(object):
    def __init__(self):
        self._count = '?'
        self.log = logging.getLogger("(unknown account)")
        self.callback = self._callback_warning
        self.daemon = True
        self.app = None
        self.name = None
        self.color = None
        self._thread = None

    def register(self, app, name, color, callback):
        if self.app or self.name:
            raise Exception
        self.app = app
        self.name = name
        self.color = color
        self.callback = callback
        self.log = logging.getLogger(self.name)
        self.spawn_thread()

    def spawn_thread(self):
        if self._thread:
            self._thread.abort = True
            self.log.warning("Aborting existing thread")
        self._thread = CheckerThread(self)
        self._thread.start()

    @property
    def count(self):
        return self._count

    @count.setter
    def count(self, value):
        self._count = value
        self.log.info("Got %s messages" % (self._count,))
        self.callback(self.name)

    def watch(self):
        while True:
            yield self.check()
            time.sleep(CHECK_INTERVAL)

    def _callback_warning(self):
        self.log.warning("Account callback not yet set")

class AccountIMAP(Account):
    def __init__(self, hostname, username, mailbox='INBOX', port=993):
        super(AccountIMAP, self).__init__()

        # Actually only supports IMAP4_SSL
        self.hostname = hostname
        self.port = port
        self.username = username
        self.mailbox = mailbox

        # Persistent IMAP connection
        self._imap = None
        self._refcount = 0

    def _get_password(self):
        return self.app.passwords['%s@%s' % (self.username, self.hostname)]

    @contextmanager
    def _connect(self):
        if not self._imap:
            try:
                self._imap = IMAP4_VerifiedSSL(self.hostname, self.port)
                self._imap.login(self.username, self._get_password())
                self._imap.select(self.mailbox, True)
                #print "IMAP Connected"
            except Exception:
                if self._imap:
                    self._imap.logout()
                #print "IMAP Failed"
                self._imap = None
                raise
        # Return reference to IMAP object
        self._refcount += 1     # FIXME: thread-safe?
        try:
            yield self._imap
        finally:
            self._refcount -= 1
            # Log out of the IMAP server
            assert self._refcount >= 0
            if self._refcount == 0 and self._imap:
                self._imap.logout()
                self._imap = None
                #print "IMAP Disconnected"

    def _idle(self, imap, timeout=29*60):
        # Wait for something to happen
        #
        # See: http://stackoverflow.com/questions/18103278/
        # See also: http://bugs.python.org/file27400/imapidle.patch
        tag = imap._new_tag()
        try:
            imap.send('%s IDLE%s' % (tag, imaplib.CRLF))
            sock = imap.socket()
            deadline = time.time() + timeout
            while True:
                timeleft = deadline - time.time()
                if timeleft <= 0:
                    break
                #print "Waiting on IDLE (%d sec left)" % (timeleft,)
                ready = select.select((sock,), (), (sock,), timeleft)
                if sock not in ready[0]:
                    break
                # Something happened
                resp = imap.readline().strip()
                #print "Got %s from IMAP" % (resp,)
                if not resp or resp[0] == '*':
                    if resp:
                        command = resp[1:].strip().split(None, 1)[0].upper()
                        if command in ('OK',):
                            continue
                        # Handle '* 661 FETCH (FLAGS (\Flagged \Seen))'
                        # Handle '* 664 EXISTS'
                        # Handle '* 664 EXPUNGE'
                        # (but will need to track all messages in mailbox)
                    break
                elif resp[0] != '+':
                    raise Exception("Unexpected IMAP IDLE response: %s" %
                                    (resp,))
        finally:
            imap.send('DONE%s' % (imaplib.CRLF))
            imap._get_response()

    def watch(self):
        # Reuse a single connection to the server
        with self._connect() as imap:
            while True:
                yield self.check()
                if 'IDLE' in imap.capabilities:
                    self._idle(imap, timeout=CHECK_INTERVAL) # or 29 minutes
                else:
                    time.sleep(CHECK_INTERVAL)

    def check(self):
        with self._connect() as imap:
            #results = imap.status(self.mailbox, '(UNSEEN)')
            #result = results[1][0].split('(')[1].split(')')[0].split(' ')
            #result = int(result[1])
            typ, msgnums = imap.search(None, '(UNSEEN)')
            assert typ == 'OK'
            result = len(msgnums[0].split())
        return result

class AccountGmailAtom(Account):
    class MyPasswordMgr(object):
        def __init__(self, account):
            self.account = account
            self.uri = None
            self.user = None
            self.password = None
        def add_password(self, realm, uri, user, passwd):
            self.uri = uri
            self.user = user
            self.password = passwd
        def find_user_password(self, realm, authuri):
            if authuri == self.uri:
                return (self.user, self.password)
            else:
                return (None, None)

    def __init__(self, username):
        super(AccountGmailAtom, self).__init__()

        self.username = username
        self.password_mgr = self.MyPasswordMgr(self)
        auth_handler = urllib2.HTTPBasicAuthHandler(self.password_mgr)
        https_handler = VerifiedHTTPSHandler()
        self.url_opener = urllib2.build_opener(auth_handler, https_handler)
        # FIXME: Also support XMPP

    def check(self):
        handle = None
        url = 'https://mail.google.com/mail/feed/atom'
        self.password_mgr.add_password(['New mail feed', 'mail.google.com'],
                                       url, self.username,
                                       self.app.passwords[self.username])
        try:
            handle = self.url_opener.open(url)
            dom = minidom.parse(handle)
            fullcount = dom.getElementsByTagName('fullcount')[0]
            return int(fullcount.firstChild.data)
        finally:
            if handle:
                handle.close()

class AccountGmailImap(AccountIMAP):
    def __init__(self, username):
        super(AccountGmailImap, self).__init__('imap.googlemail.com', username)

    def _get_password(self):
        return self.app.passwords[self.username]

AccountGmail = AccountGmailImap

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
            minwatch = time.time() + CHECK_INTERVAL
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


# A wrap_socket implementation that verifies certificates using system
# CA certificates
def my_wrap_socket(sock, keyfile=None, certfile=None,
                   do_handshake_on_connect=True,
                   suppress_ragged_eofs=True,
                   server_hostname=None):
    sslctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    if keyfile or certfile:
        sslctx.load_cert_chain(certfile, keyfile)
    sslctx.verify_mode = ssl.CERT_REQUIRED # Should be default
    return sslctx.wrap_socket(sock, server_side=False,
                              do_handshake_on_connect=do_handshake_on_connect,
                              suppress_ragged_eofs=suppress_ragged_eofs,
                              server_hostname=server_hostname)

# An IMAP4_SSL implementation that uses my_wrap_socket to verify certificates
class IMAP4_VerifiedSSL(imaplib.IMAP4_SSL):
    def open(self, host='', port=imaplib.IMAP4_SSL_PORT):
        """Setup connection to remote server on "host:port".
            (default: localhost:standard IMAP4 SSL port).
        This connection will be used by the routines:
            read, readline, send, shutdown.
        """
        self.host = host
        self.port = port
        self.sock = socket.create_connection((host, port))
        self.sslobj = my_wrap_socket(self.sock, self.keyfile, self.certfile,
                                     server_hostname=host)
        self.file = self.sslobj.makefile('rb')

# From
# http://thejosephturner.com/blog/2011/03/19/

class VerifiedHTTPSConnection(httplib.HTTPSConnection):
    def connect(self):
        # overrides the version in httplib so that we do
        #    certificate verification
        sock = socket.create_connection((self.host, self.port), self.timeout)
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
        # wrap the socket using verification with the root
        #    certs in trusted_root_certs
        #ca = ssl.get_server_certificate((host, port),
        #    ssl_version=ssl.PROTOCOL_SSLv3|ssl.PROTOCOL_TLSv1)
        self.sock = my_wrap_socket(sock, server_hostname=self.host)

# wraps https connections with ssl certificate verification
class VerifiedHTTPSHandler(urllib2.HTTPSHandler):
    def __init__(self, connection_class=VerifiedHTTPSConnection):
        self.specialized_conn_class = connection_class
        urllib2.HTTPSHandler.__init__(self)
    def https_open(self, req):
        return self.do_open(self.specialized_conn_class, req)

class PasswordManager(object):
    def __init__(self):
        self.passwords = {}
        self.log = logging.getLogger(self.__class__.__name__)

    def __getitem__(self, username):
        if username in self.passwords:
            return self.passwords[username]
        return None

    def load(self, username):
        import keyring          # Must be imported after dbus
        password = keyring.get_password(KEYRING_SERVICE, username)
        if not password:
            self.log.warning("No password for %s" % (username,))
        self.passwords[username] = password

    def store(self, username, password):
        import keyring          # Must be imported after dbus
        if password is None:
            keyring.delete_password(KEYRING_SERVICE, username)
            del self.passwords[username]
        else:
            keyring.set_password(KEYRING_SERVICE, username, password)
            self.passwords[username] = password

class MultiowlConfigurator(gtk.Dialog):
    def __init__(self, app):
        super(MultiowlConfigurator, self).__init__("Multiowl Preferences", \
            buttons=(gtk.STOCK_CLOSE, gtk.RESPONSE_ACCEPT))
        self.connect('response', self.dismiss)

    def show(self):
        self.show_all()
    def dismiss(self, dialog, response):
        self.hide()

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

class MultiowlApp(object):
    def __init__(self):
        # Check single-instance
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        if dbus.SessionBus().request_name(DBUS_NAME) != \
           dbus.bus.REQUEST_NAME_REPLY_PRIMARY_OWNER:
            print "Sending message to existing instance"
            instance = dbus.SessionBus().get_object(DBUS_NAME, DBUS_PATH)
            trigger = instance.get_dbus_method('preferences')
            trigger()
            return
        self.service = DBusService(self)

        # Create icon(s)
        self.icon = MailIcon(self)

        # Accounts
        self.passwords = PasswordManager()
        self.accounts = {}
        self.accountnames = []

        # Read configuration
        self.settings = RawConfigParser()
        self.settings.read(CONFIG_FILE)
        self.configurator = MultiowlConfigurator(self)
        self.add_accounts()

        self.main()

    def add_account(self, name, account, color, index=None):
        self.icon.lock()
        self.accounts[name] = account
        if index is None:
            index = len(self.accountnames)
        self.accountnames.insert(index, name)
        color = _make_color(color) # Do this with the GTK lock
        self.icon.unlock()
        account.register(self, name, color, callback=self.icon.update_happened)
        self.icon.update_happened()

    def remove_account(self, name):
        self.icon.lock()
        del self.accounts[name]
        self.accountnames.remove(name)
        self.icon.unlock()
        self.icon.update_happened()

    def add_accounts(self):
        if not self.icon.base:
            # Wait for icon to become ready
            gobject.timeout_add(1, self.add_accounts)
            return False

        for name in self.settings.sections():
            account_type = self.settings.get(name, 'type')
            #account_class = globals()['Account'+account_type] # FIXME
            color = self.settings.get(name, 'color')
            if account_type == 'Gmail':
                username = self.settings.get(name, 'username')
                self.passwords.load(username)
                account = AccountGmail(username)
            elif account_type == 'IMAP':
                server = self.settings.get(name, 'server')
                username = self.settings.get(name, 'username')
                self.passwords.load('%s@%s' % (username, server))
                account = AccountIMAP(server, username)
            self.add_account(name, account, color)

        return False

    @staticmethod
    def main():
        gtk.threads_enter()     # FIXME: Rewrite in Qt
        gtk.main()
        gtk.threads_leave()

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG,
                        datefmt='%Y-%m-%d %H:%M:%S',
                        format="[%(asctime)s] <%(name)s> %(message)s")
    MultiowlApp()
