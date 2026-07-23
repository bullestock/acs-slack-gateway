import argparse
import certifi
import datetime
import hashlib
import json
import os
import requests
import ssl
import struct
import sys
import time

import paho.mqtt.client as paho

STATUS_TOPIC = "hal9k/acs/status"
BACKEND_TOPIC = "hal9k/acs/backend"

FRONTEND_DESC_MAP = {
    "main": "the space from outside",
    "barndoor": "the space from the barn",
    "woodshop": "the woodshop from the barn",
    "tester": "the backrooms",
}

MQTT_KEY = bytes.fromhex(os.environ['MQTT_KEY'])
MQTT_USER = os.environ['MQTT_USER']
MQTT_PASSWORD = os.environ['MQTT_PASSWORD']

ACS_DOOR_TOKEN = os.environ["ACS_DOOR_TOKEN"]
SLACK_WRITE_TOKEN = os.environ['SLACK_WRITE_TOKEN']

def verify_hash_with_timestamp(message: str, digest: bytes, timestamp: int) -> bool:
    hasher = hashlib.sha256()
    hasher.update(MQTT_KEY)
    hasher.update(struct.pack('<Q', timestamp))
    hasher.update(message.encode('utf-8'))

    return hasher.digest() == digest


class AcsMqtt(paho.Client):
    def __init__(self, logger, userdata):
        super().__init__(client_id="", userdata=userdata, protocol=paho.MQTTv5)
        self.logger = logger
        self.log_info("AcsMqtt init")

    def log_info(self, msg):
        if self.logger:
            self.logger.info(msg)

    def slack_write(self, msg, channel='jeg-står-herude-og-banker-på'):
        if "|" in msg:
            parts = msg.split("|")
            msg = parts[0]
            channel = parts[1]
            if len(parts) > 2:
                c_emoji = parts[2]
        self.log_info(f"slack_write: #{channel}: {msg}")
        try:
            body = { 'channel': channel, 'icon_emoji': ':panopticon:', 'parse': 'full', 'text': msg }
            headers = {
                    'content_type': 'application/json',
                    'Authorization': 'Bearer %s' % SLACK_WRITE_TOKEN
                }
            r = requests.post(url = 'https://slack.com/api/chat.postMessage', data = body, headers = headers)
            self.log_info(f"slack_write: {r}")
        except Exception as e:
            self.log_info(f"Slack exception: {e}")

    def log_backend(self, user_id, message):
        try:
            body = { "api_token": ACS_DOOR_TOKEN, "log": { "message": message } }
            if user_id is not None:
                body["log"]["user_id"] = user_id
            r = requests.post(url = 'https://panopticon.hal9k.dk/api/v1/logs', json = body)
        except Exception as e:
            self.log_info(f"log_backend exception: {e}")

    def log_unknown_card(self, card_id):
        try:
            body = { "api_token": ACS_DOOR_TOKEN, "card_id": card_id }
            r = requests.post(url = 'https://panopticon.hal9k.dk/api/v1/unknown_cards', json = body)
        except Exception as e:
            self.log_info(f"log_unknown_card exception: {e}")

    def on_connect(self, client, userdata, flags, rc, props=None):
        self.log_info("MQTT connected")
        client.subscribe(f"{STATUS_TOPIC}/#", qos=1)
        client.subscribe(f"{BACKEND_TOPIC}/#", qos=1)

    def on_disconnect(self, client, userdata, flags, rc, props=None):
        self.log_info("MQTT disconnected")
        while True:
            # loop until client.reconnect()
            # returns 0, which means the
            # client is connected
            try:
                if not client.reconnect():
                    break
            except ConnectionRefusedError:
                # if the server is not running,
                # then the host rejects the connection
                # and a ConnectionRefusedError is thrown
                # getting this error > continue trying to
                # connect
                pass
            # if the reconnect was not successful,
            # wait one second
            time.sleep(1)

    def is_backend_request_valid(self, data):
        """
        Validate backend request using MQTT_KEY
        """
        if not "identifier" in data:
            self.log_info(f"Missing identifier: {data}")
            return False
        if not "text" in data:
            self.log_info(f"Missing text: {data}")
            return False
        if not "stamp" in data:
            self.log_info(f"Missing stamp: {data}")
            return False
        if not "hash" in data:
            self.log_info(f"Missing hash: {data}")
            return False
        stamp = int(data["stamp"])
        text = data["text"]
        hash = data["hash"]
        # Verify timestamp is not too old (30 seconds)
        try:
            current_time = int(datetime.datetime.now().timestamp())
            if abs(current_time - stamp) > 30:
                self.log_info('Backend request timestamp too old: %d' % stamp)
                return False
        except (ValueError, TypeError) as e:
            self.log_info('Backend invalid timestamp: %s' % e)
            return False

        return verify_hash_with_timestamp(text, bytes.fromhex(hash), stamp)

    def on_message(self, client, userdata, message):
        try:
            if message.topic.startswith(STATUS_TOPIC):
                topic = message.topic[len(STATUS_TOPIC)+1:]
                topic_parts = topic.split("/")
                if len(topic_parts) != 1:
                    self.log_info(f"Invalid MQTT topic: {message.topic}")
                    return
                device = topic_parts[0]
                if device == "space":
                    self.log_info(f"Space status: {data}")
                    return
            try:
                data = message.payload.decode("utf-8")
                data = json.loads(data)
            except:
                # Ignore invalid or missing JSON
                self.log_info(f"Invalid MQTT data: {data}")
                return
            if message.topic.startswith(STATUS_TOPIC):
                # "hal9k/acs/status/main <json>" -> "main <json>"
                userdata.status[device] = data
                self.log_info(f"Updated MQTT status for {device}")
            elif message.topic.startswith(BACKEND_TOPIC):
                # "hal9k/acs/backend/log <json>"
                # "hal9k/acs/backend/slack <json>"
                # "hal9k/acs/backend/unknown_card <json>"
                topic = message.topic[len(BACKEND_TOPIC)+1:]
                topic_parts = topic.split("/")
                if len(topic_parts) != 1:
                    return
                action = topic_parts[0]
                if action == "log":
                    try:
                        self.log_info(f"backend log: {data}")
                        if not self.is_backend_request_valid(data):
                            self.log_info(f"Invalid backend/log request: {data}")
                            return
                        self.log_info(f"backend log: request is valid")
                        device = data["identifier"]
                        if "Granted entry" in data["text"]:
                            if device in FRONTEND_DESC_MAP:
                                self.slack_write(f":unlock: A hacker just entered {FRONTEND_DESC_MAP[device]}")
                            else:
                                self.slack_write(f":unlock: A hacker just entered the unknowns:interrobang:")
                        self.log_info(f"backend log: wrote to Slack")
                        # Log to backend
                        self.log_backend(data["user_id"], data["text"])
                    except Exception as e:
                        self.log_info(f"Exception: {e}")
                elif action == "unknown_card":
                    self.log_info(f"backend unknown_card: {data}")
                    if not self.is_backend_request_valid(data):
                        self.log_info(f"Invalid backend/unknown_card request: {data}")
                        return
                    # Log to backend
                    self.log_unknown_card(data["text"])
                elif action == "slack":
                    self.log_info(f"backend slack: {data}")
                    if not self.is_backend_request_valid(data):
                        self.log_info(f"Invalid backend/slack request: {data}")
                        return
                    msg = data['text']
                    if msg.startswith(":"):
                        # Add identifier after emoji
                        parts = msg.split(":")
                        emoji = f":{parts[1]}:"
                        msg = f"{emoji} ({data['identifier']}) {':'.join(parts[2:])}"
                    else:
                        # Add identifier at start
                        msg = f"({data['identifier']}) {msg}"
                    channel='jeg-står-herude-og-banker-på'
                    if "|" in msg:
                        parts = msg.split("|")
                        channel = parts[1]
                    self.slack_write(msg, channel)
                else:
                    self.log_info(f"backend {action}?")
        except Exception as e:
            self.log_info(f"MQTT exception: {e}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="MQTT")
    parser.add_argument("command")
    parser.add_argument("args", nargs="*")
    args = parser.parse_args()

    mqtt_client = AcsMqtt(None, userdata=None)
    ctx = ssl.create_default_context(cafile=certifi.where())
    mqtt_client.tls_set_context(ctx)
    mqtt_client.connect("mqtt.hal9k.dk", 8883)
    mqtt_client.loop_start()

    if args.command == "slack":
        if len(args.args) != 1:
            print("Wrong number of arguments to 'slack'")
            sys.exit(1)
        msg = args.args[0]
        if msg.startswith(":"):
            # Add identifier after emoji
            parts = msg.split(":")
            emoji = f":{parts[1]}:"
            msg = f"{emoji} (tester) {':'.join(parts[2:])}"
        else:
            # Add identifier at start
            msg = f"(tester) {msg}"
        channel='jeg-står-herude-og-banker-på'
        if "|" in msg:
            parts = msg.split("|")
            channel = parts[1]
        mqtt_client.slack_write(msg, channel)
        time.sleep(5)
