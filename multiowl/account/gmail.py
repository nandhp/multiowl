from .. import sslutils
from . import Account
from .imap import AccountIMAP

import urllib2
from xml.dom import minidom

class AccountGmailIMAP(AccountIMAP):
    def __init__(self, config, icon):
        config['server'] = 'imap.googlemail.com'
        super(AccountGmailIMAP, self).__init__(config, icon)
        self.password = self.username

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

    def __init__(self, config, icon):
        super(AccountGmailAtom, self).__init__(config, icon)

        self.username = config['username']
        self.password = self.username
        self.password_mgr = self.MyPasswordMgr(self)
        auth_handler = urllib2.HTTPBasicAuthHandler(self.password_mgr)
        https_handler = sslutils.VerifiedHTTPSHandler()
        self.url_opener = urllib2.build_opener(auth_handler, https_handler)
        # Future: Use XMPP?

    def check(self):
        handle = None
        url = 'https://mail.google.com/mail/feed/atom'
        self.password_mgr.add_password(['New mail feed', 'mail.google.com'],
                                       url, self.username, self.password)
        try:
            handle = self.url_opener.open(url)
            dom = minidom.parse(handle)
            fullcount = dom.getElementsByTagName('fullcount')[0]
            return int(fullcount.firstChild.data)
        finally:
            if handle:
                handle.close()

Account = AccountGmailIMAP
