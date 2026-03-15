import time
import os
from .base import BaseAdapter
from shared.kafka_client import AuroraProducer, AuroraConsumer
from shared.elastic_client import AuroraElasticClient

class ElasticsearchAdapter(BaseAdapter):
    def __init__(self, name="Elasticsearch"):
        super().__init__(name)
        self.es_hosts = [os.getenv("ELASTIC_HOST", "http://localhost:9200")]
        self.es_index = os.getenv("ELASTIC_INDEX", "mock-logs")
        self.kafka_brokers = [os.getenv("KAFKA_BROKERS", "localhost:29092")]
        self.kafka_topic = os.getenv("KAFKA_TOPIC", "logs.unfiltered")
        self.poll_interval = float(os.getenv("POLL_INTERVAL", "1.0"))

    def run(self):
        print(f"Ingestor: Starting {self.name} adapter...")
        
        es_client = AuroraElasticClient(self.es_hosts)
        producer = AuroraProducer(self.kafka_brokers)
        producer.ensure_topic(self.kafka_topic)
        producer.ensure_topic("actions")
        
        # 1. Ask Ledger for the last saved timestamp
        last_timestamp = self._get_start_timestamp(producer)
        print(f"  [{self.name}] Starting from: {last_timestamp}")
        
        last_sort = None
        
        try:
            while True:
                hits, new_sort = es_client.fetch_new_logs(self.es_index, last_timestamp, last_sort=last_sort)
                
                if hits:
                    for hit in hits:
                        log_data = hit['_source']
                        # Ensure standard fields exist for downstream
                        ts = log_data.get("timestamp") or log_data.get("@timestamp")
                        log_data["timestamp"] = ts
                        if "source" not in log_data:
                            log_data["source"] = "application"
                            
                        producer.send_log(self.kafka_topic, log_data)
                        last_timestamp = ts
                    
                    last_sort = new_sort
                    producer.flush()
                    print(f"  [{self.name}] Published {len(hits)} logs.")
                    
                    # If we got a full batch, try to fetch more immediately without waiting
                    if len(hits) >= 100:
                        continue
                
                time.sleep(self.poll_interval)
        except Exception as e:
            print(f"  [{self.name}] Critical Error: {e}")
        finally:
            producer.close()
            es_client.close()

    def _get_start_timestamp(self, producer):
        """Requests last known timestamp from Ledger via 'actions' topic."""
        import uuid
        requester_id = f"ingestor-{self.name.lower()}-{uuid.uuid4().hex[:8]}"
        
        consumer = AuroraConsumer(
            topics=["actions"],
            group_id=f"init-{requester_id}",
            bootstrap_servers=self.kafka_brokers,
            auto_offset="latest"
        )
        
        try:
            # Join the group and get assignments.
            print(f"  [{self.name}] Connecting to 'actions' topic...")
            # Increased polling to ensure we are joined and have partitions assigned
            for _ in range(10):
                consumer.consumer.poll(timeout_ms=500)
            
            # Small extra wait to be absolutely sure
            time.sleep(1)
            
            # 2. Send request
            payload = {
                "action": "get_last_unfiltered_timestamp",
                "requester": requester_id
            }
            print(f"  [{self.name}] Requesting last timestamp from Ledger (req_id: {requester_id})...")
            producer.send_log("actions", payload, key=f"request-{requester_id}")
            producer.flush()
            
            start_time = time.time()
            timeout = 10.0
            
            while time.time() - start_time < timeout:
                messages = consumer.consumer.poll(timeout_ms=1000)
                if not messages:
                    continue
                    
                for _, msgs in messages.items():
                    for msg in msgs:
                        val = msg.value
                        if (val.get("action") == "response_last_unfiltered_timestamp" and 
                            val.get("requester") == requester_id):
                            ts = val.get("timestamp")
                            print(f"  [{self.name}] Received last timestamp from Ledger: {ts}")
                            return ts
                        else:
                            other_action = val.get("action")
                            other_req = val.get("requester")
                            if other_req != requester_id:
                                print(f"  [{self.name}] Skipping unrelated action: {other_action} from {other_req}")
            
            print(f"  [{self.name}] No Ledger response within {timeout}s. Starting from epoch.")
            return "1970-01-01T00:00:00.000Z"
            
        except Exception as e:
            print(f"  [{self.name}] Initialization error: {e}. Defaulting to epoch.")
            return "1970-01-01T00:00:00.000Z"
        finally:
            consumer.close()
