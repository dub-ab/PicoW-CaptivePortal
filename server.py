# server.py
# https://ansonvandoren.com/posts/esp8266-captive-web-portal-part-1/
import usocket as socket
import uselect as select


class Server:
    """Base class for a server socket."""
    def __init__(self, poller, port, sock_type, name):
        """
        Initialize the server socket.
        """
        self.name = name
        # create socket with correct type: stream (TCP) or datagram (UDP)
        self.sock = socket.socket(socket.AF_INET, sock_type)

        # register to get event updates for this socket
        self.poller = poller
        self.poller.register(self.sock, select.POLLIN)

        addr = socket.getaddrinfo("0.0.0.0", port)[0][-1]
        # allow new requests while still sending last response
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(addr)

        print(self.name, "listening on", addr)

    def stop(self, poller):
        """
        Stop the server socket.
        """ 
        poller.unregister(self.sock)
        self.sock.close()
        print(self.name, "stopped")