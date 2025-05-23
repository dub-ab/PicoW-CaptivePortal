# main.py
# https://ansonvandoren.com/posts/esp8266-captive-web-portal-part-1/
from captive_portal import CaptivePortal

portal = CaptivePortal()

portal.start()