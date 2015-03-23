from . import Account
from .. import sslutils

import imaplib
from contextlib import contextmanager
import socket, select
import time

# An IMAP4_SSL implementation that uses my_wrap_socket to verify certificates
class IMAP4_VerifiedSSL(imaplib.IMAP4_SSL):
    def open(self, host='', port=imaplib.IMAP4_SSL_PORT):
        self.host = host
        self.port = port
        self.sock = socket.create_connection((host, port))
        self.sslobj = sslutils.my_wrap_socket(self.sock, self.keyfile,
                                              self.certfile,
                                              server_hostname=host)
        self.file = self.sslobj.makefile('rb')

def imap_idle(imap, timeout=29*60):
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

class AccountIMAP(Account):
    def __init__(self, config, icon):
        #hostname, username, mailbox='INBOX', port=993):
        super(AccountIMAP, self).__init__(config, icon)

        # Actually only supports IMAP4_SSL
        self.hostname = config['server']
        self.port = int(config.get('port', 993))
        self.username = config['username']
        self.mailbox = config.get('mailbox', 'INBOX')
        self.password = '%s@%s' % (self.username, self.hostname)

        # Persistent IMAP connection
        self._imap = None
        self._refcount = 0

    @contextmanager
    def _connect(self):
        if not self._imap:
            try:
                self._imap = IMAP4_VerifiedSSL(self.hostname, self.port)
                self._imap.login(self.username, self.password)
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

    def watch(self):
        # Reuse a single connection to the server
        with self._connect() as imap:
            while True:
                yield self.check()
                if 'IDLE' in imap.capabilities:
                    # Use check interval or 29 minutes
                    imap_idle(imap, timeout=self.interval)
                else:
                    time.sleep(self.interval)

    def check(self):
        with self._connect() as imap:
            #results = imap.status(self.mailbox, '(UNSEEN)')
            #result = results[1][0].split('(')[1].split(')')[0].split(' ')
            #result = int(result[1])
            typ, msgnums = imap.search(None, '(UNSEEN)')
            assert typ == 'OK'
            result = len(msgnums[0].split())
        return result

Account = AccountIMAP
