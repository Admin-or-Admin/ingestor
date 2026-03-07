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
            {"msg": "SQL Injection attempt detected in query: SELECT * FROM users WHERE id = 1 OR 1=1", "level": "CRITICAL", "status": 403},
            {"msg": "Cross-Site Scripting (XSS) detected in parameter 'user_id': <script>alert('XSS')</script>", "level": "CRITICAL", "status": 403},
            {"msg": "Multiple failed login attempts for user 'admin' from IP 192.168.1.45", "level": "WARNING", "status": 401},
            {"msg": "Unauthorized access attempt to /api/v1/admin/settings by user 'guest'", "level": "ERROR", "status": 403},
            {"msg": "Potential brute force attack on /login endpoint from 103.45.12.98", "level": "WARNING", "status": 429}
        ]
        
        if random.random() < 0.3:
            event = random.choice(cyber_scenarios)
            message, level, status = event["msg"], event["level"], event["status"]
        else:
            message, level, status = self.fake.sentence(), random.choice(levels), random.choice([200, 201, 400, 401, 404, 500])

        return {
            "@timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
            "log.level": level, "message": message, "service.name": random.choice(services),
            "trace.id": str(uuid.uuid4()), "user.id": self.fake.user_name(),
            "http.response.status_code": status, "process.pid": random.randint(1000, 9999)
        }

    def run(self):
        print(f"🚀 Ingestor: Starting {self.name} adapter...")
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
