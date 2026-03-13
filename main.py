import os
import sys
import threading
import signal
from dotenv import load_dotenv

from ingestor.adapters.elasticsearch_adapter import ElasticsearchAdapter
from ingestor.adapters.gns3_adapter import GNS3Adapter
from ingestor.adapters.mock_logs_adapter import MockLogsAdapter
from ingestor.adapters.cisco_adapter import CiscoAdapter

# Registry of available adapters
ADAPTER_MAP = {
    "elasticsearch": ElasticsearchAdapter,
    "gns3": GNS3Adapter,
    "mock_logs": MockLogsAdapter,
    "cisco": CiscoAdapter
}

from shared.logger import setup_logger

logger = setup_logger("ingestor")

def main():
    load_dotenv()
    
    # Example: ENABLED_INGESTORS=elasticsearch,gns3
    enabled_names = os.getenv("ENABLED_INGESTORS", "elasticsearch,mock_logs").lower().split(",")
    
    threads = []
    
    print(f"--- Aurora Ingestor Service ---")
    print(f"Enabled adapters: {', '.join(enabled_names)}")
    
    for name in enabled_names:
        name = name.strip()
        if name in ADAPTER_MAP:
            adapter_class = ADAPTER_MAP[name]
            adapter_instance = adapter_class()
            
            # Create a thread for each adapter
            thread = threading.Thread(target=adapter_instance.run, daemon=True)
            thread.start()
            threads.append(thread)
        else:
            print(f"Warning: Adapter '{name}' not found in registry.")

    if not threads:
        print("No adapters started. Check ENABLED_INGESTORS environment variable.")
        return
    
    print(f"All {len(threads)} ingestors are running.")
    
    # Graceful shutdown handling
    def stop_signal_handler(signum, frame):
        print("\nStopping Ingestor service...")
        sys.exit(0)

    signal.signal(signal.SIGINT, stop_signal_handler)
    signal.signal(signal.SIGTERM, stop_signal_handler)

    for thread in threads:
        thread.join()

if __name__ == "__main__":
    main()
