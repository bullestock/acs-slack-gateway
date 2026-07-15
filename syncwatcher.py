import json
import logging
import os
import threading
import time
from datetime import datetime

import paho.mqtt.publish as publish
import certifi
import ssl

class SyncWatcher:
    def __init__(self, sync_file, mqtt_user, mqtt_password, interval, logger):
        """
        Initialize the SyncWatcher.
        
        Args:
            sync_file: Path to the file to watch for timestamp changes
            interval: Check interval in seconds (default: 60)
            logger: Logger instance (optional)
        """
        self.sync_file = sync_file
        self.mqtt_user = mqtt_user
        self.mqtt_password = mqtt_password
        self.interval = interval
        self.logger = logger
        self.running = False
        self.thread = None

    def log_info(self, msg):
        if self.logger:
            self.logger.info(msg)

    def get_file_timestamp(self):
        """Get the modification timestamp of the sync file."""
        try:
            if os.path.exists(self.sync_file):
                return os.path.getmtime(self.sync_file)
            else:
                self.log_info(f"Sync file not found: {self.sync_file}")
                return None
        except Exception as e:
            self.log_info(f"Error getting file timestamp: {e}")
            return None

    def publish_status(self):
        """Publish the current sync status to MQTT."""
        try:
            file_timestamp = self.get_file_timestamp()
            # Convert file modification time (seconds since epoch) to ISO format if available
            if file_timestamp is not None:
                timestamp_iso = datetime.fromtimestamp(file_timestamp).isoformat()
            else:
                timestamp_iso = None

            current_time = datetime.now().isoformat()
            
            message = {
                "timestamp": timestamp_iso,
                "last_check": current_time
            }
            
            payload = json.dumps(message)
            publish.single("hal9k/acs/status/sync", payload,
                           hostname="mqtt.hal9k.dk",
                           port=8883,
                           auth={'username': self.mqtt_user, 'password': self.mqtt_password},
                           tls={'tls_version': ssl.PROTOCOL_TLSv1_2, 'ca_certs': certifi.where()},
                           retain=True)
            self.log_info(f"Published sync status: {payload}")
        except Exception as e:
            self.log_info(f"Error publishing status: {e}")

    def _watch_loop(self):
        """Main loop for the watcher thread."""
        while self.running:
            self.publish_status()
            time.sleep(self.interval)

    def start(self):
        """Start the watcher thread."""
        if self.running:
            self.log_info("SyncWatcher is already running")
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._watch_loop, daemon=True)
        self.thread.start()
        self.log_info(f"SyncWatcher started, checking {self.sync_file} every {self.interval}s")

    def stop(self):
        """Stop the watcher thread."""
        if not self.running:
            self.log_info("SyncWatcher is not running")
            return
        
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        self.log_info("SyncWatcher stopped")
