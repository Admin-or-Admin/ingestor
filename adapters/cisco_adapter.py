import re
import socket
import uuid
import os
import time
import random
import threading
from datetime import datetime, timezone
from faker import Faker
from .base import BaseAdapter
from shared.kafka_client import AuroraProducer

class CiscoAdapter(BaseAdapter):
    def __init__(self, name="Cisco"):
        super().__init__(name)
        self.kafka_brokers = [os.getenv("KAFKA_BROKERS", "localhost:29092")]
        self.kafka_topic = os.getenv("KAFKA_TOPIC", "logs.unfiltered")
        self.syslog_port = int(os.getenv("CISCO_SYSLOG_PORT", "1515"))
        self.sim_enabled = os.getenv("CISCO_SIMULATION_ENABLED", "true").lower() == "true"
        self.sim_interval = float(os.getenv("CISCO_SIM_INTERVAL", "1.0"))
        self.fake = Faker()
        
        # Mapping Cisco severity levels (0-7) to log.level
        self.severity_map = {
            "0": "CRITICAL",
            "1": "CRITICAL",
            "2": "CRITICAL",
            "3": "ERROR",
            "4": "WARNING",
            "5": "INFO",
            "6": "INFO",
            "7": "DEBUG"
        }

    def generate_cisco_event(self):
        """Generates a mock Cisco Syslog message."""
        hosts = ["R1-CORE", "R2-EDGE", "SW-DIST-01", "FW-EXTERNAL"]
        facilities = ["LINEPROTO", "SEC", "SYS", "AUTH", "LINK"]
        
        templates = [
            {"msg": "%LINEPROTO-5-UPDOWN: Line protocol on Interface {interface}, changed state to {state}", "facility": "LINEPROTO", "severity": "5", "mnemonic": "UPDOWN"},
            {"msg": "%SEC-6-IPACCESSLOGP: list 101 denied tcp {src_ip}({src_port}) -> {dst_ip}({dst_port}), 1 packet", "facility": "SEC", "severity": "6", "mnemonic": "IPACCESSLOGP"},
            {"msg": "%SYS-5-CONFIG_I: Configured from console by {user} on vty0 ({src_ip})", "facility": "SYS", "severity": "5", "mnemonic": "CONFIG_I"},
            {"msg": "%AUTH-4-LOGIN_FAILED: Login failed for user '{user}' from host {src_ip}", "facility": "AUTH", "severity": "4", "mnemonic": "LOGIN_FAILED"},
            {"msg": "%LINK-3-UPDOWN: Interface {interface}, changed state to down", "facility": "LINK", "severity": "3", "mnemonic": "UPDOWN"}
        ]
        
        event = random.choice(templates)
        src_ip = f"10.0.0.{random.randint(2, 254)}"
        dst_ip = f"192.168.1.{random.randint(2, 254)}"
        interface = random.choice(["GigabitEthernet0/0", "GigabitEthernet0/1", "FastEthernet0/24"])
        
        raw_msg = event["msg"].format(
            interface=interface,
            state=random.choice(["up", "down"]),
            src_ip=src_ip,
            dst_ip=dst_ip,
            src_port=random.randint(1024, 65535),
            dst_port=random.choice([22, 80, 443]),
            user=self.fake.user_name()
        )
        
        hostname = random.choice(hosts)
        pri = (random.randint(1, 20) * 8) + int(event["severity"])
        timestamp = datetime.now().strftime("%b %d %H:%M:%S")
        
        full_syslog = f"<{pri}> {timestamp} {hostname}: {raw_msg}"
        return full_syslog

    def parse_cisco_syslog(self, raw_msg, addr):
        """Parses a raw Cisco Syslog message into a structured log entry."""
        # Example format: <189> Mar 12 12:00:00 R1: %LINEPROTO-5-UPDOWN: Line protocol...
        
        log_entry = {
            "@timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
            "service.name": f"cisco-{addr[0]}",
            "log.level": "INFO",
            "message": raw_msg,
            "trace.id": str(uuid.uuid4()),
            "source.ip": addr[0],
            "event.module": "cisco",
            "event.category": "network"
        }

        # 1. Extract Priority (PRI) and calculate Severity
        pri_match = re.search(r'<(\d+)>', raw_msg)
        if pri_match:
            pri = int(pri_match.group(1))
            severity = pri % 8
            log_entry["log.level"] = self.severity_map.get(str(severity), "INFO")
        
        # 2. Extract Cisco Header: %FACILITY-SEVERITY-MNEMONIC: 
        header_match = re.search(r'%([A-Z0-9_]+)-([0-7])-([A-Z0-9_]+): (.*)', raw_msg)
        if header_match:
            facility = header_match.group(1)
            severity_code = header_match.group(2)
            mnemonic = header_match.group(3)
            message_body = header_match.group(4)
            
            log_entry["log.level"] = self.severity_map.get(severity_code, log_entry["log.level"])
            log_entry["event.code"] = mnemonic
            log_entry["event.provider"] = facility
            log_entry["message"] = message_body.strip()

        # 3. Try to extract hostname (usually before the % sign)
        host_match = re.search(r'([a-zA-Z0-9\-_]+): %', raw_msg)
        if host_match:
            log_entry["service.name"] = host_match.group(1)

        return log_entry

    def run(self):
        print(f"Ingestor: Starting {self.name} adapter...")
        producer = AuroraProducer(self.kafka_brokers)
        producer.ensure_topic(self.kafka_topic)
        
        # Start Syslog in thread
        syslog_thread = threading.Thread(target=self.syslog_server, args=(producer,), daemon=True)
        syslog_thread.start()
        
        try:
            if self.sim_enabled:
                print(f"  [{self.name}] Cisco simulation enabled (interval: {self.sim_interval}s)")
                while True:
                    raw_msg = self.generate_cisco_event()
                    log_entry = self.parse_cisco_syslog(raw_msg, ("127.0.0.1", 0))
                    producer.send_log(self.kafka_topic, log_entry)
                    time.sleep(self.sim_interval)
            else:
                print(f"  [{self.name}] Cisco simulation disabled. Listener only.")
                while True:
                    time.sleep(10)
        except Exception as e:
            print(f"  [{self.name}] Error: {e}")
        finally:
            producer.close()

    def syslog_server(self, producer):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("0.0.0.0", self.syslog_port))
            print(f"  [{self.name}] Cisco Syslog listener active on UDP {self.syslog_port}")
            while True:
                data, addr = sock.recvfrom(8192)
                msg = data.decode('utf-8', errors='ignore').strip()
                
                if not msg:
                    continue
                    
                log_entry = self.parse_cisco_syslog(msg, addr)
                producer.send_log(self.kafka_topic, log_entry)
                
        except Exception as e:
            print(f"  [{self.name}] Syslog Server Error: {e}")
        finally:
            sock.close()
