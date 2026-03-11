import time
import random
import uuid
import os
from datetime import datetime, timezone
from elasticsearch import Elasticsearch
from faker import Faker
from .base import BaseAdapter

class MockLogsAdapter(BaseAdapter):
    def __init__(self, name="MockLogs"):
        super().__init__(name)
        self.es_hosts = [os.getenv("ELASTIC_HOST", "http://localhost:9200")]
        self.index_name = os.getenv("ELASTIC_INDEX", "mock-logs")
        self.delay = float(os.getenv("MOCK_DELAY", "0.5"))
        self.fake = Faker()
        self.es = Elasticsearch(self.es_hosts)

    def generate_mock_log(self):
        levels = ["INFO", "WARNING", "ERROR", "DEBUG"]
        services = ["auth-service", "payment-gateway", "inventory-manager", "frontend-api", "web-proxy"]
        cyber_scenarios = [
            {"msg": "SQL Injection attempt detected in query: SELECT * FROM users WHERE id = 1 OR 1=1", "level": "CRITICAL", "status": 403, "source": "application"},
            {"msg": "Cross-Site Scripting (XSS) detected in parameter 'user_id': <script>alert('XSS')</script>", "level": "CRITICAL", "status": 403, "source": "application"},
            {"msg": "Multiple failed login attempts for user 'admin' from IP 192.168.1.45", "level": "WARNING", "status": 401, "source": "auth"},
            {"msg": "Unauthorized access attempt to /api/v1/admin/settings by user 'guest'", "level": "ERROR", "status": 403, "source": "application"},
            {"msg": "Potential brute force attack on /login endpoint from 103.45.12.98", "level": "WARNING", "status": 429, "source": "auth"},
            {"msg": "Port scan detected: 50 connection attempts to different ports from 192.168.1.200 in 2 seconds", "level": "HIGH", "status": 403, "source": "firewall"},
            {"msg": "Suspicious process 'whoami' executed by user 'www-data' on frontend-api-v2", "level": "HIGH", "status": 200, "source": "endpoint"},
            {"msg": "Large data transfer detected: 4.5GB sent to unknown IP 45.33.22.11 in 5 minutes", "level": "CRITICAL", "status": 200, "source": "network"},
            {"msg": "Lateral movement attempt: SSH connection from 10.0.0.5 to 10.0.0.12 with user 'root'", "level": "CRITICAL", "status": 401, "source": "auth"},
            {"msg": "DNS query for known malicious domain 'cryptolocker-c2-server.com' from 10.0.0.8", "level": "CRITICAL", "status": 200, "source": "dns"}
        ]
        
        event = random.choice(cyber_scenarios)
        message, level, status, source = event["msg"], event["level"], event["status"], event["source"]

        return {
            "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
            "source": source,
            "log.level": level, "message": message, "service.name": random.choice(services),
            "trace.id": str(uuid.uuid4()), "user.id": self.fake.user_name(),
            "http.response.status_code": status, "process.pid": random.randint(1000, 9999)
        }

    def run(self):
        print(f"Ingestor: Starting {self.name} adapter...")
        while True:
            try:
                log_entry = self.generate_mock_log()
                self.es.index(index=self.index_name, document=log_entry)
                
                # Random delay between 1 and 2 seconds
                wait_time = random.uniform(1.0, 2.0)
                time.sleep(wait_time)
            except Exception as e:
                print(f"  [{self.name}] Error: {e}")
                time.sleep(2)
