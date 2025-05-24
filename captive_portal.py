# captive_portal.py
# https://ansonvandoren.com/posts/esp8266-captive-web-portal-part-1/
import network
import ubinascii as binascii
import uerrno
import uos as os
import utime as time
import gc
import uselect as select

from captive_dns import DNSServer
from captive_http import HTTPServer

class CaptivePortal:
    AP_IP = "192.168.4.1"
    CRED_FILE = "./wifi.creds"
    MAX_CONN_ATTEMPTS = 10
    AP_OFF_DELAY = const(60 * 1000)

    def __init__(self, essid=None):
        self.local_ip = self.AP_IP
        self.poller = select.poll()
        self.dns_server = DNSServer(self.poller, self.local_ip)
        self.http_server = HTTPServer(self.poller, self.local_ip)
        self.sta_if = network.WLAN(network.STA_IF)
        self.ap_if = network.WLAN(network.AP_IF)

        self.mac_raw = self.ap_if.config("mac")

        if essid is None:
            self.mac_hex = binascii.hexlify(self.mac_raw).decode()
            essid = f"Pico-{self.mac_hex[-6:]}"  # Use last 6 
        self.essid = essid
        
        self.ssid = None
        self.password = None
        self.conn_time_start = None

    def start(self):
        # turn off station interface to force a reconnect
        self.sta_if.active(False)
        if not self.try_connect_from_file():
            self.captive_portal()

    def connect_to_wifi(self):
        # print(
        #     "Trying to connect to SSID '{:s}' with password {:s}".format(
        #         self.ssid, self.password
        #     )
        # )
        # initiate the connection
        self.sta_if.active(True)
        self.sta_if.connect(self.ssid, self.password)

        attempts = 0
        while attempts < self.MAX_CONN_ATTEMPTS:
            if not self.sta_if.isconnected():
                print("Connection in progress")
                time.sleep(2)
                attempts += 1
            else:
                print("Connected to {:s}".format(self.ssid))
                self.local_ip = self.sta_if.ifconfig()[0]
                self.write_creds(self.ssid, self.password)
                return True

        print("Failed to connect to {:s} with {:s}. WLAN status={:d}".format(
            self.ssid, self.password, self.sta_if.status()
        ))
        # forget the credentials since they didn't work, and turn off station mode
        self.ssid = self.password = None
        self.sta_if.active(False)
        return False

    def write_creds(self, ssid, password):
        open(self.CRED_FILE, 'wb').write(b','.join([ssid, password]))
        print("Wrote credentials to {:s}".format(self.CRED_FILE))

    def start_access_point(self):
        # sometimes need to turn off AP before it will come up properly.
        self.ap_if.active(False)
        while not self.ap_if.active():
            print(f"Waiting for access point to turn on")
            self.ap_if.active(True)
            time.sleep(1)
         # IP address, netmask, gateway, DNS
        self.ap_if.ifconfig(
            (self.local_ip, "255.255.255.0", self.local_ip, self.local_ip)
        )
        self.ap_if.config(essid=self.essid, security=0)
        print("AP mode configured:", self.ap_if.ifconfig())

    def captive_portal(self):
        print("Starting captive portal")
        self.start_access_point()

        # create the HTTP server
        if self.http_server is None:
            self.http_server = HTTPServer(self.poller, self.local_ip)
            print("Configured HTTP server") 
        # create the DNS server
        if self.dns_server is None:
            self.dns_server = DNSServer(self.poller, self.local_ip)
            print("Configured DNS server")

        try:
            while True:
                gc.collect()
                # check for socket events and handle them
                for response in self.poller.ipoll(1000):
                    sock, event, *others = response
                    is_handled = self.handle_dns(sock, event, others)
                    if not is_handled:
                        self.handle_http(sock, event, others)
                # print("having handled socket events, checking valid wifi\n")
                if self.check_valid_wifi():
                    # print(" how come we never get here")
                    self.dns_server.stop(self.poller)
                    self.http_server.set_ip(self.local_ip, self.ssid)

        except KeyboardInterrupt:
            print("Captive portal stopped")
        self.cleanup()

    def handle_http(self, sock, event, others):
        self.http_server.handle(sock, event, others)

    def handle_dns(self, sock, event, others):
        if sock is self.dns_server.sock:
            # ignore UDP socket hangups
            if event == select.POLLHUP:
                return True
            self.dns_server.handle(sock, event, others)
            return True
        return False

    def cleanup(self):
        print("Cleaning up")
        if self.dns_server:
            self.dns_server.stop(self.poller)
        gc.collect()        

    def try_connect_from_file(self):
        print("Trying to load WiFi credentials from {:s}".format(self.CRED_FILE))
        try:
            os.stat(self.CRED_FILE)
        except OSError as e:
            if e.args[0] == uerrno.ENOENT:
                print("{:s} does not exist".format(self.CRED_FILE))
                return False

        contents = open(self.CRED_FILE, 'rb').read().split(b',')
        if len(contents) == 2:
            self.ssid, self.password = contents
        else:
            print("Invalid credentials file:", contents)
            return False

        if not self.connect_to_wifi():
            print("Connect with saved credentials failed, starting captive portal")
            os.remove(self.CRED_FILE)
            return False

        return True
    
    def check_valid_wifi(self):
        # print("Checking WiFi validity...")

        # Check if station interface is connected
        if not self.sta_if.isconnected():
            print("WiFi not connected.")

            if self.has_creds():
                print("Credentials found, attempting to connect...")
                return self.connect_to_wifi()

            print("No credentials available.")
            return False

        # print("WiFi is connected.")

        # Check if AP mode is active
        if not self.ap_if.active():
            print("Access Point mode is OFF.")
            return False

        # print("Access Point mode is ACTIVE.")

        # Handle delay before turning off AP mode
        if self.conn_time_start is None:
            # print("First time connection detected. Starting AP off delay...")
            self.conn_time_start = time.ticks_ms()
            remaining = self.AP_OFF_DELAY
        else:
            remaining = self.AP_OFF_DELAY - time.ticks_diff(time.ticks_ms(), self.conn_time_start)
            # print(f"AP turn-off delay remaining: {remaining} ms")

            if remaining <= 0:
                self.ap_if.active(False)
                print("Access Point turned OFF.")

        return False
    
    def has_creds(self):
        # print(". . . .    Checking has_creds for saved credentials...")
        self.ssid, self.password = self.http_server.saved_credentials
        # print("saved_credentials:", self.ssid, self.password)
        return None not in self.http_server.saved_credentials
