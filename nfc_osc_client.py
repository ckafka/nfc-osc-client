"""
nfc ocs server
"""
import errno
import signal
import time
import sys

import nfc
import nfc.clf.device
import nfc.clf.transport
from pythonosc.udp_client import SimpleUDPClient

from nfc_tags import CustomTextTag


class Sighandler:
    """SIGTERM and SIGINT handler"""

    def __init__(self) -> None:
        self.sigint = False

    def signal_handler(self, sig, han):
        """Ack received handler"""
        self.sigint = True
        print(f"\n***{signal.Signals(sig).name} received. Exiting...***")


class NfcReader:
    """
    NFC reader
    """

    def __init__(self, clf):
        self.clf = clf
        self.last_tag = None
        self.current_tag = None

        self.active_tag: CustomTextTag = None
        self.activated = False

    def update(self, tag):
        """Set new tag information"""
        self.last_tag = self.current_tag
        self.current_tag = tag

    def is_current_tag_new_and_valid(self):
        """Return true if the current tag is new and valid"""

        if self.activated:
            print("A tag is already active")
            return False

        valid = False
        if self.current_tag is not None:
            print("Checking if tag is valid....")
            if self.current_tag.ndef is not None:
                print("Detected tag with NDEF record. Checking header...")
                new_text_tag = CustomTextTag(self.current_tag)
                if new_text_tag.is_header_valid():
                    print("Valid header")
                    self.active_tag = new_text_tag
                    valid = True
                print("Missing NFC NDEF header text. Format and try again")
            else:
                print("Detected tag without NDEF record.")
                # check the list of hard coded serial numbers
                valid = False
            return valid

    def pattern_activated(self):
        """Set pattern as active once OCS enable command is sent to the server"""
        self.activated = True

    def tag_removed(self):
        """Set tag as removed once OCS disable command is sent to the server"""
        print("Tag Removed")
        self.activated = False
        self.active_tag = None


class ChromatikOcsClient:
    """Osc Client for Chromatik"""

    OSC_SERVER_IP = "127.0.0.1"
    OSC_SERVER_PORT = 7777

    def __init__(self) -> None:
        self.client = SimpleUDPClient(self.OSC_SERVER_IP, self.OSC_SERVER_PORT)

    def tx_pattern_enable(self, reader_index, pattern_name):
        """Send msg to enable a pattern"""
        address = f"/channel/{reader_index}/pattern/{pattern_name}/enable"
        self.client.send_message(address, "T")
        print(f'Sent msg: {address}/{"T"}')

    def tx_pattern_disable(self, reader_index, pattern_name):
        """Send msg to disable a pattern"""
        address = f"/channel/{reader_index}/pattern/{pattern_name}/enable"
        self.client.send_message(address, "F")
        print(f'Sent msg: {address}/{"F"}')


class NfcController:
    """
    NFC Controller -- supports polling multiple readers
    """

    def __init__(self) -> None:
        self.readers = []
        self.reader_index = 0

        self.chromatik_client = ChromatikOcsClient()

        self.rw_params = {
            "on-startup": self.start_poll,
            "on-connect": self.tag_detected,
            "iterations": 1,
            "interval": 0.5,
        }

        self.start_time_ms = time.time_ns() / 1000
        self.TIMEOUT_ms = 100

    def tag_detected(self, tag):
        """Print detected tag's NDEF data"""
        print("Tag detected")
        current_reader = self.readers[self.reader_index]
        current_reader.update(tag)
        if current_reader.is_tag_new_and_valid():
            self.chromatik_client.tx_pattern_enable(
                self.reader_index, current_reader.active_tag.get_pattern()
            )
            current_reader.pattern_activated()
        return True

    def start_poll(self, targets):
        """Start the stop watch. Must return targets to clf"""
        self.start_time_ms = time.time_ns() / 1000000
        return targets

    def timeout(self):
        """
        Return whether time > TIMEOUT_S has elapsed since last call of start_poll()
        """
        elapsed = (time.time_ns() / 1000000) - self.start_time_ms
        return elapsed > self.TIMEOUT_ms

    def close_all(self):
        """
        Close all detected NFC readers. If reader is not closed correctly, it
        will not initialize correctly on the next run due issue on PN532
        """
        for nfc_reader in self.readers:
            nfc_reader.clf.close()
        print("***Closed all readers***")

    def discover_readers(self):
        """Discover readers connected via FTDI USB to serial cables"""
        print("***Discovering Readers***")
        for dev in nfc.clf.transport.TTY.find("ttyUSB")[0]:
            path = f"tty:{dev[8:]}"
            try:
                clf = nfc.ContactlessFrontend(path)
                print(f"Found device: {clf.device}")
                self.readers.append(NfcReader(clf))
            except IOError as error:
                if error.errno == errno.ENODEV:
                    print(
                        f"Reader on {path} unresponsive. Power cycle reader and try again"
                    )
                else:
                    print(f"Unkown error: {error}")

    def poll_readers(self):
        """Poll each reader for a card, print the tag"""
        print("***Polling***")

        self.reader_index = 0
        for nfc_reader in self.readers:
            try:
                print(f"Polling reader {nfc_reader.clf.device}")
                tag = nfc_reader.clf.connect(
                    rdwr=self.rw_params, terminate=self.timeout
                )

                # Send disable command once the tag is removed
                # Don't send disable commands if it's one-shot
                if tag is None:
                    nfc_reader.update(tag)
                    if nfc_reader.activated and not nfc_reader.active_tag.is_one_shot():
                        self.chromatik_client.tx_pattern_disable(
                            self.reader_index, nfc_reader.active_tag.get_pattern()
                        )
                        nfc_reader.tag_removed()

            except Exception as unknown_exception:
                print(f"{unknown_exception}")
            self.reader_index += 1


if __name__ == "__main__":
    print("***CTRL+C or pskill python to exit***")
    controller = NfcController()
    controller.discover_readers()

    if len(controller.readers) == 0:
        print("***No devices found. Exiting***")
        sys.exit()

    handler = Sighandler()
    signal.signal(signal.SIGINT, handler.signal_handler)
    signal.signal(signal.SIGTERM, handler.signal_handler)

    while not handler.sigint:
        try:
            controller.poll_readers()
            time.sleep(0.2)
        except Exception as uknown_exception:
            controller.close_all()
    controller.close_all()
