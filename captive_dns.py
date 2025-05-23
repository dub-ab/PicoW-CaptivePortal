# captive_dns.py
# As seen at:# https://ansonvandoren.com/posts/esp8266-captive-web-portal-part-1/
import usocket as socket
import gc

from server import Server

class DNSServer(Server):
    """Class to handle DNS requests and responses."""
    def __init__(self, poller, ip_addr):
        super().__init__(poller, 53, socket.SOCK_DGRAM, "DNS Server")
        self.ip_addr = ip_addr

    def handle(self, sock, event, others):
        """Handle incoming DNS requests."""
        # server doesn't spawn other sockets, so only respond to its own socket
        if sock is not self.sock:
            return

        # check the DNS question, and respond with an answer
        try:
            data, sender = sock.recvfrom(1024)
            request = DNSQuery(data)

            print("Sending {:s} -> {:s}".format(request.domain, self.ip_addr))
            sock.sendto(request.answer(self.ip_addr), sender)

            # help MicroPython with memory management
            del request
            gc.collect()

        except Exception as e:
            print("DNS server exception:", e)

class DNSQuery:
    """Class to handle DNS queries and responses."""
    def __init__(self, data):
        self.data = data
        self.domain = ""
        # header is bytes 0-11, so question starts at 12
        head = 12
        # length of this label is defined in the first byte
        length = data[head] 
        while length != 0:
            label = head + 1
            # add the label to the requested domain and insert a dot after
            self.domain += data[label : label + length].decode("utf-8") + "."
            # check the next label
            head += length + 1
            length = data[head]

    def answer(self, ip_addr):
        """Create a DNS response with the given IP address."""
        # create a DNS response packet
        # copy the ID from the incoming request
        packet = self.data[:2]
        # set response flags
        packet += b"\x81\x80"
        # copy over QDCOUNT and ANCOUNT equal
        packet += self.data[4:6] + self.data[4:6]
        # set NSCOUNT and ARCOUNT to 0
        packet += b"\x00\x00\x00\x00"

        # create the answer body
        # answer with the original domain question
        packet += self.data[12:]
        # pointer back to domain name (at byte 12)
        packet += b"\xc0\x0c"
        # set TYPE and CLASS (A record and IN class)
        packet += b"\x00\x01\x00\x01"
        # set TTL to 60sec
        packet += b"\x00\x00\x00\x3C"
        # set response length to 4 bytes (to hold one IPv4 address)
        packet += b"\x00\x04"
        # now actually send the IP address as 4 bytes (without the "."s)
        packet += bytes(map(int, ip_addr.split(".")))

        gc.collect()

        return packet
