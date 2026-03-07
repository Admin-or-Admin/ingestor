import json
import time
import random
import uuid
import socket
import threading
import os
from datetime import datetime, timezone
from faker import Faker
from netaddr import IPNetwork
from .base import BaseAdapter
from shared.kafka_client import AuroraProducer

class GNS3Adapter(BaseAdapter):
    def __init__(self, name="GNS3"):
        super().__init__(name)
        self.kafka_brokers = [os.getenv("KAFKA_BROKERS", "localhost:29092")]
        self.kafka_topic = os.getenv("KAFKA_TOPIC", "logs.unfiltered")
        self.syslog_port = int(os.getenv("SYSLOG_PORT", "1514"))
        self.sim_interval = float(os.getenv("SIM_INTERVAL", "1.0"))
        self.fake = Faker()

    def generate_network_event(self):
        """Simulates realistic Cisco/GNS3 network events."""
        internal_net = IPNetwork("10.0.0.0/24")
        external_ips = ["192.168.1.50", "8.8.8.8", "1.1.1.1", "172.16.5.2"]
        interfaces = ["GigabitEthernet0/0", "GigabitEthernet0/1", "FastEthernet0/21", "Vlan10"]
        hosts = ["R1", "R2", "SW-CORE", "SW-ACCESS", "FW-01"]
        
        templates = [
            {"msg": "%SEC-6-IPACCESSLOGP: list 101 denied tcp {src_ip}({src_port}) -> {dst_ip}({dst_port}), 1 packet", "type": "security", "level": "WARNING"},
            {"msg": "%LINEPROTO-5-UPDOWN: Line protocol on Interface {interface}, changed state to {state}", "type": "infrastructure", "level": "INFO"},
            {"msg": "%AUTH-4-LOGIN_FAILED: Login failed for user '{user}' from host {src_ip}", "type": "security", "level": "ERROR"},
            {"msg": "%SYS-5-CONFIG_I: Configured from console by {user} on vty0 ({src_ip})", "type": "audit", "level": "INFO"}
        ]
        
        event = random.choice(templates)
        src_ip = random.choice(external_ips) if random.random() > 0.5 else str(random.choice(list(internal_net)))
        dst_ip = str(random.choice(list(internal_net)))
        
        formatted_msg = event["msg"].format(
            src_ip=src_ip, dst_ip=dst_ip,
            src_port=random.randint(1024, 65535),
            dst_port=random.choice([22, 80, 443, 3389, 23]),
            interface=random.choice(interfaces),
            state=random.choice(["up", "down"]),
            user=self.fake.user_name()
        )
        
        return {
            "@timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
            "log.level": event["level"],
            "message": formatted_msg,
            "service.name": random.choice(hosts),
            "trace.id": str(uuid.uuid4()),
            "source.ip": src_ip,
            "destination.ip": dst_ip,
            "event.module": "gns3-simulator",
            "event.category": event["type"]
        }

    def syslog_server(self, producer):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("0.0.0.0", self.syslog_port))
            print(f"  [{self.name}] Syslog listener active on UDP {self.syslog_port}")
            while True:
                data, addr = sock.recvfrom(4096)
                msg = data.decode('utf-8', errors='ignore').strip()
                producer.send_log(self.kafka_topic, {
                    "@timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
                    "log.level": "INFO",
                    "message": msg,
                    "service.name": f"gns3-node-{addr[0]}",
                    "trace.id": str(uuid.uuid4()),
                    "source.ip": addr[0]
                })
        except Exception as e:
            print(f"  [{self.name}] Syslog Error: {e}")

    def run(self):
        print(f"Ingestor: Starting {self.name} adapter...")
        producer = AuroraProducer(self.kafka_brokers)
        producer.ensure_topic(self.kafka_topic)
        
        # Start Syslog in thread
        threading.Thread(target=self.syslog_server, args=(producer,), daemon=True).start()
        
        try:
            while True:
                log_entry = self.generate_network_event()
                producer.send_log(self.kafka_topic, log_entry)
                time.sleep(self.sim_interval)
        except Exception as e:
            print(f"  [{self.name}] Error: {e}")
        finally:
            producer.close()
