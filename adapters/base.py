from abc import ABC, abstractmethod

class BaseAdapter(ABC):
    def __init__(self, name):
        self.name = name

    @abstractmethod
    def run(self):
        """Main loop for the ingestor."""
        pass
