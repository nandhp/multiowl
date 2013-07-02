#!/usr/bin/env python

""" multiowl.py - Mail checker for system notification area with enhanced
                  support for multiple accounts.""" 

import traceback
import threading
import gtk, gobject, cairo
from StringIO import StringIO
import time
from collections import OrderedDict
from ConfigParser import RawConfigParser
import os, os.path, sys
import dbus, dbus.service, dbus.mainloop.glib

# For Gmail
import urllib2, httplib, socket, ssl
from xml.dom import minidom

# For IMAP
import imaplib

DATA_DIR = os.path.dirname(os.path.realpath(__file__))
os.chdir(DATA_DIR)

gtk.threads_init()

_CONFIG_DIR = os.environ.get('XDG_CONFIG_HOME',
    os.path.join(os.path.expanduser('~'), '.config'))
CONFIG_FILE = os.path.join(_CONFIG_DIR, 'multiowl', 'config')

KEYRING_SERVICE = 'multiowl'

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
    cr.select_font_face("sans", cairo.FONT_SLANT_NORMAL,
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

    def __init__(self):
        super(MailIcon, self).__init__()
        self.connect('size-changed', self.resize_icon)
        self.accounts = {}
        self.accountnames = []
        self.nactive = 0
        self.displaying = None
        self.cycler = None
        self.lock = threading.Lock()
        self.base = None

    def add_account(self, account, name, color, index=None):
        self.lock.acquire()
        self.accounts[name] = [account, _make_color(color), 0, None]
        if index is None:
            index = len(self.accountnames)
        self.accountnames.insert(index, name)
        self.lock.release()
        account.set_callback(self.update_happened)
        self.update_happened()

    def remove_account(self, name):
        self.lock.acquire()
        del self.accounts[name]
        self.accountnames.remove(name)
        self.lock.release()
        self.update_happened()

    def resize_icon(self, the_icon, size):
        print "Now %d pixels" % size
        self.base = gtk.gdk.pixbuf_new_from_file_at_size('mail.svg',
            size, size)
        #self.base = gtk.gdk.pixbuf_new_from_icon_name('mail-unread')
        self.set_from_pixbuf(self.base)
        # Wait for icon to realize, else bad things happen (the Cairo text
        # API will have trouble with text)
        gobject.timeout_add(1, self.update_icon)
        return True

    def update_pixbuf(self, account):
        data = self.accounts[account]
        self.accounts[account][3] = _make_pixbuf(self.base, data[1], data[2])

    def update_icon(self):
        if self.displaying:
            data = self.accounts[self.displaying]
            self.set_from_pixbuf(data[3])
            self.set_visible(True)
        else:
            self.set_visible(False)

    def cycle(self):
        # Determine the next account to display
        keys = self.accountnames
        if self.displaying:
            pos = keys.index(self.displaying) + 1
        else:
            pos = 0
        permuted = keys[pos:] + keys[:pos]
        # Restrict the permuted list to active accounts
        eligible = [x for x in permuted if self.accounts[x][2]]
        if not eligible:
            # No active accounts
            self.displaying = None
        else:
            self.displaying = eligible[0]
            self.update_icon()
        return True

    def auto_cycle(self):
        self.lock.acquire()
        gtk.threads_enter()
        self.cycle()
        gtk.threads_leave()
        self.lock.release()
        return True

    def update_happened(self):
        self.lock.acquire()
        gtk.threads_enter()

        # Determine how many active accounts we have for this icon
        new_active = 0
        new_tooltip = ['No new messages']
        new_total = 0
        for name in self.accountnames:
            data = self.accounts[name]
            data[2] = data[0].count()
            if data[2]:
                new_active += 1
                self.update_pixbuf(name)
                new_tooltip.append('  %s in %s' % (data[2], name))
                if type(data[2]) is int:
                    new_total += data[2]

        # Update the tooltip
        if new_total > 0:
            new_tooltip[0] = '<b>%d new messages</b>' % new_total
        self.set_tooltip_markup('\n'.join(new_tooltip))
            
        # Do we need to be cycling between accounts?
        print "Have %d active accounts" % new_active
        if new_active > 1:
            if not self.cycler:
                self.cycler = gobject.timeout_add(self.INTERVAL, \
                    self.auto_cycle)
        elif self.cycler:
            gobject.source_remove(self.cycler)
            self.cycler = None

        # If the currently displayed account has no mail, cycle immediately 
        if (self.displaying and self.accounts[self.displaying][2] <= 0) or \
            (not self.displaying and new_active > 0):
            self.cycle()
        else:
            self.update_icon()

        gtk.threads_leave()
        self.lock.release()

def _raise_NotImplementedError():
    raise NotImplementedError

class Account(threading.Thread):
    def __init__(self):
        super(Account, self).__init__()
        self.nmsgs = 0
        self.callback = _raise_NotImplementedError
        self.daemon = True

    def set_callback(self, callback):
        self.callback = callback

    def count(self):
        return self.nmsgs


class AccountIMAP(Account):
    def __init__(self, hostname, username, mailbox='INBOX', port=993):
        super(AccountIMAP, self).__init__()

        # Actually only supports IMAP4_SSL
        self.hostname = hostname
        self.port = port
        self.username = username
        self.mailbox = mailbox
        # FIXME: maintain connection to IMAP server and/or use IDLE

        self.start()

    def run(self):
        while True:
            try:
                # FIXME: verify certificate
                password = get_password('%s@%s' % (self.username,
                                                   self.hostname))
                imap = imaplib.IMAP4_SSL(self.hostname, self.port)
                imap.login(self.username, password)
                results = imap.status(self.mailbox, '(UNSEEN)')
                result = results[1][0].split('(')[1].split(')')[0].split(' ')
                self.nmsgs = int(result[1])
                imap.logout()
            except Exception:
                print 'AccountIMAP: %s@%s: Exception.' % \
                    (self.username, self.hostname)
                traceback.print_exc()
                self.nmsgs = '?'

            print "[%s@%s] Got %s messages" % \
                (self.username, self.hostname,self.nmsgs)
            self.callback()
            time.sleep(300)
        
class AccountGmail(Account):
    class MyPasswordMgr(object):
        def __init__(self, realm, uri, user):
            self.realm = realm
            self.uri = uri
            self.user = user
        def add_password(self, realm, uri, user, passwd):
            raise NotImplemented
        def find_user_password(self, realm, authuri):
            if realm == self.realm and authuri == self.uri:
                return (self.user, get_password(self.user))
            else:
                return (None, None)

    def __init__(self, username):
        super(AccountGmail, self).__init__()

        # For error reporting 
        self.username = username

        self.url = 'https://mail.google.com/mail/feed/atom'
        password_mgr = self.MyPasswordMgr(realm='New mail feed',
                                          uri=self.url,
                                          user=username)
        auth_handler = urllib2.HTTPBasicAuthHandler(password_mgr)
        https_handler = VerifiedHTTPSHandler()
        self.url_opener = urllib2.build_opener(auth_handler, https_handler)

        # FIXME: Also support XMPP

        self.start()

    def run(self):
        # Fetch/monitor unread count
        while True:
            handle = None
            try:
                handle = self.url_opener.open(self.url)
                dom = minidom.parse(handle)
                fullcount = dom.getElementsByTagName('fullcount')[0]
                self.nmsgs = int(fullcount.firstChild.data)
            except Exception:
                print 'AccountGmail: %s: Exception.' % self.username
                traceback.print_exc()
                self.nmsgs = '?'
            if handle:
                handle.close()

            print "[%s] Got %s messages" % (self.username, self.nmsgs)
            self.callback()

            try:
                time.sleep(300)
            except KeyboardInterrupt:
                return

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

        self.sock = ssl.wrap_socket(sock,
                                    self.key_file,
                                    self.cert_file,
                                    cert_reqs=ssl.CERT_REQUIRED,
                                    # FIXME: bad practice
                                    ca_certs="Equifax.pem")

# wraps https connections with ssl certificate verification
class VerifiedHTTPSHandler(urllib2.HTTPSHandler):
    def __init__(self, connection_class = VerifiedHTTPSConnection):
        self.specialized_conn_class = connection_class
        urllib2.HTTPSHandler.__init__(self)
    def https_open(self, req):
        return self.do_open(self.specialized_conn_class, req)

passwords = {}                  # FIXME
def get_password(username):
    if username in passwords:
        return passwords[username]
    return None
def load_password(username):
    import keyring              # Must be imported after dbus
    passwords[username] = keyring.get_password(KEYRING_SERVICE, username)
    if not passwords[username]:
        sys.stderr.write("No password for %s.\n" % username)
def store_password(username, password):
    import keyring              # Must be imported after dbus
    if password is None:
        keyring.delete_password(KEYRING_SERVICE, username)
    else:
        keyring.set_password(KEYRING_SERVICE, username, password)

def add_accounts(icon, settings):
    if not icon.base:
        # Wait for icon to become ready
        return True

    # FIXME: Use keyring (or gnomekeyring)
    # https://bitbucket.org/kang/python-keyring-lib/src/8aeb01ec6b36bc92bfde504482c00297c42bb792/keyring/backends/Gnome.py?at=default
    # https://bitbucket.org/kang/python-keyring-lib
    for name in settings.sections():
        account_type = settings.get(name, 'type')
        #account_class = globals()['Account'+account_type] # FIXME 
        color = settings.get(name, 'color')
        if account_type == 'Gmail':
            username = settings.get(name, 'username')
            load_password(username)
            account = AccountGmail(username)
        elif account_type == 'IMAP':
            server = settings.get(name, 'server')
            username = settings.get(name, 'username')
            load_password('%s@%s' % (username, server))
            account = AccountIMAP(server, username)
        icon.add_account(account, name, color)

    return False

class MultiowlConfigurator(gtk.Dialog):
    def __init__(self, settings):
        super(MultiowlConfigurator, self).__init__('Multiowl Preferences', \
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
    def __init__(self, configurator):
        name = dbus.service.BusName(DBUS_NAME, bus=dbus.SessionBus())
        dbus.service.Object.__init__(self, name, DBUS_PATH)
        self.configurator = configurator

    @dbus.service.method(dbus_interface=DBUS_NAME)
    def test(self):
        print "Alerted!"
        
    @dbus.service.method(dbus_interface=DBUS_NAME)
    def preferences(self):
        self.configurator.show()

def main():
    # Read configuration
    settings = RawConfigParser()
    settings.read(CONFIG_FILE)
    # Check single-instance
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    if dbus.SessionBus().request_name(DBUS_NAME) != \
            dbus.bus.REQUEST_NAME_REPLY_PRIMARY_OWNER:
        print "Sending message to existing instance"
        instance = dbus.SessionBus().get_object(DBUS_NAME, DBUS_PATH)
        trigger = instance.get_dbus_method('preferences')
        trigger()
        return
    # Configurator
    configurator = MultiowlConfigurator(settings)
    service = DBusService(configurator)
    # Create icon(s)
    icon = MailIcon()
    gobject.timeout_add(1, add_accounts, icon, settings)
    gtk.main()

if __name__ == '__main__':
    main()
