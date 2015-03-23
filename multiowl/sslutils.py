import ssl
import socket, httplib, urllib2

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
