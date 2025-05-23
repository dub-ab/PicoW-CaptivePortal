# captive_http.py
# https://ansonvandoren.com/posts/esp8266-captive-web-portal-part-1/

import gc
import uio  
import uerrno
import usocket as socket
import uselect as select
from collections import namedtuple

# Define named tuples after imports
WriteConn = namedtuple("WriteConn", ["body", "buff", "buffmv", "write_range"])
ReqInfo = namedtuple("ReqInfo", ["type", "path", "params", "host"])

from server import Server

class HTTPServer(Server):
    """Class to handle HTTP requests and responses."""
    def __init__(self, poller, local_ip):
        super().__init__(poller, 80, socket.SOCK_STREAM, "HTTP Server")
        if type(local_ip) is bytes:
            self.local_ip = local_ip
        else:
            self.local_ip = local_ip.encode()
        self.request = dict()
        self.conns = dict()
        self.routes = {b"/": b"./index.html", b"/login": self.login}
        self.saved_credentials = (None, None)

        # queue up to 5 connection requests before refusing
        self.sock.listen(5)
        self.sock.setblocking(False)
        
        self.ssid = None
    
    def handle(self, sock, event, others):

        if sock is self.sock:
            # client connecting on port 80, so spawn off a new
            # socket to handle this connection
            print("- Accepting new HTTP connection")
            self.accept(sock)
        elif event & select.POLLIN:
            # socket has data to read in
            print("- Reading incoming HTTP data")
            self.read(sock)
        elif event & select.POLLOUT:
            # existing connection has space to send more data
            print("- Sending outgoing HTTP data")
            self.write_to(sock)

    def accept(self, server_sock):
        """Accept a new client request socket and register it for polling."""

        try:
            client_sock, addr = server_sock.accept()
            print("Accepted connection from", addr)
        except OSError as e:
            if e.args[0] == uerrno.EAGAIN:
                # no pending connections
                return
            else:
                print("Error accepting connection:", e)
                return

        client_sock.setblocking(False)
        client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.poller.register(client_sock, select.POLLIN)

    def parse_request(self, req):
        """Safely parse a raw HTTP request to extract method, path, query params, and host."""

        try:
            req_lines = req.split(b"\r\n")
            if not req_lines or len(req_lines[0].split(b" ")) != 3:
                raise ValueError("Malformed request line")

            # Parse the request line
            req_type, full_path, http_ver = req_lines[0].split(b" ")

            # Separate path and query
            base_path, _, query_string = full_path.partition(b"?")
            query_params = {}

            if query_string:
                for param in query_string.split(b"&"):
                    if b"=" in param:
                        key, val = param.split(b"=", 1)
                        query_params[key] = val

            # Find the Host header
            host = None
            for line in req_lines[1:]:
                if line.lower().startswith(b"host:"):
                    _, _, host_val = line.partition(b": ")
                    host = host_val.strip()
                    break

            if host is None:
                raise ValueError("Host header missing")

            return ReqInfo(req_type, base_path, query_params, host)

        except Exception as e:
            print("Failed to parse HTTP request:", e)
            return ReqInfo(b"INVALID", b"/", {}, b"")

    def get_response(self, req):
        """generate a response body and headers, given a route"""

        headers = b"HTTP/1.1 200 OK\r\n"
        route = self.routes.get(req.path, None)

        # print("here get_response says Route:", route)

        if isinstance(route, bytes):
            # Static file route
            try:
                return open(route, "rb"), headers
            except OSError:
                return uio.BytesIO(b"File not found"), b"HTTP/1.1 404 Not d\r\n"
        if callable(route):
            response = route(req.params)
            # Defensive: Ensure it's a tuple of (body, headers)
            if isinstance(response, (tuple, list)) and len(response) == 2:
                body = response[0] or b""
                headers = response[1] or headers
                return uio.BytesIO(body), headers
            else:
                # Auto-wrap a simple body with default headers
                return uio.BytesIO(response if isinstance(response, bytes) else str(response).encode()), headers
        # No route match
        return uio.BytesIO(b"Not Found"), b"HTTP/1.1 404 Not Found\r\n"

    def set_ip(self, new_ip, new_ssid):
        """Update settings after connecting to local WiFi."""
        
        print(f"set_ip called! Updating IP: {new_ip}, SSID: {new_ssid}")  # Debug print
        
        self.local_ip = new_ip.encode()
        self.ssid = new_ssid
        self.routes = {b"/": self.connected}

        print(f"Updated local_ip: {self.local_ip}, routes: {self.routes}")  # Debug print

    def read(self, s):
        """Read incoming HTTP data and parse the request."""

        data = s.read()
        if not data:
            # no data, so close the socket
            # print("No data, closing connection")
            self.close(s)
            return
        
        # add new data to the full request
        sid = id(s)
        self.request[sid] = self.request.get(sid, b"") + data
        
        # check if the request is complete
        if data[-4:] != b"\r\n\r\n":
            # request is not complete, so return
            # print("Request not complete")
            return
        
        # get the complete request
        req = self.parse_request(self.request.pop(sid))

        if not self.is_valid_req(req):
            headers = (
                "HTTP/1.1 307 Temporary Redirect\r\n"
                "Location: http://{}/\r\n".format(self.local_ip.decode())
            ).encode()
            body = uio.BytesIO(b"")
            self.prepare_write(s, body, headers)
            return

        # get a body depending on the route
        body, headers = self.get_response(req)
        
        
        self.prepare_write(s, body, headers)

    def is_valid_req(self, req):
        if req.host != self.local_ip:
            # force a redirect to the MCU's IP address
            return False
        # redirect if we don't have a route for the requested path
        return req.path in self.routes

    def prepare_write(self, s, body, headers):
        """ Keep track of how much data has been written out for each response.
        """
        # add newline to headers to signify transition to body
        headers += "\r\n"
        # TCP/IP MSS is 536 bytes, so create buffer of this size and
        # initially populate with header data
        buff = bytearray(headers + "\x00" * (536 - len(headers)))
        # use memoryview to read directly into the buffer without copying
        buffmv = memoryview(buff)
        # start reading body data into the memoryview starting after
        # the headers, and writing at most the remaining space of the buffer
        # return the number of bytes written into the memoryview from the body
        bw = body.readinto(buffmv[len(headers) :], 536 - len(headers))
        # save place for next write event
        c = WriteConn(body, buff, buffmv, [0, len(headers) + bw])
        self.conns[id(s)] = c
        # let the poller know we want to know when it's OK to write
        self.poller.modify(s, select.POLLOUT)

    def write_to(self, sock):
        """ Write the next message to an open socket"""
        
        c = self.conns[id(sock)]
        if c:
            # write the next 536 bytes (max) to the socket
            bytes_written = sock.write(c.buffmv[c.write_range[0] : c.write_range[1]])
            if not bytes_written or c.write_range[1] < 536:
                # no more data to write, so close the socket
                self.close(sock)
            else:
                # update the memoryview with next portion of data
                self.buff_advance(c, bytes_written)

    def buff_advance(self, c, bytes_written):
        """ Advance the memoryview to the next portion of data to write.
        """
        
        if bytes_written == c.write_range[1] - c.write_range[0]:
            # all data written, set next write start to memoryview start
            c.write_range[0] = 0
            # set next write end to the end of the buffer
            c.write_range[1] = c.body.readinto(c.buff, 536)
        else:
            # only part of the data was written, so set next write start
            c.write_range[0] += bytes_written

    def login(self, params):
        """ Handle a login request and save the credentials.
        """
        ssid = params.get(b"ssid", None)
        password = params.get(b"password", None)
        if all([ssid, password]):
            self.saved_credentials = (ssid, password)
        
        headers = (
            "HTTP/1.1 307 Temporary Redirect\r\n"
            "Location: http://{}/\r\n".format(self.local_ip.decode())
        ).encode()

        # print(" Here login says saved credentials:", self.saved_credentials)
        return b"", headers

    def close(self, s):
        """ Close the socket and unregister it from the poller and delete connection
        """

        s.close()
        self.poller.unregister(s)
        sid = id(s)
        if sid in self.request:
            del self.request[sid]
        if sid in self.conns:
            del self.conns[sid]
        gc.collect()
        print("Closed connection")

    def connected(self, params):
        headers = b"HTTP/1.1 200 OK\r\n"
        body = open("./connected.html", "rb").read() % (self.ssid, self.local_ip)
        return body, headers


