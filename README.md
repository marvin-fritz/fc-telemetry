# fc-telemetry

Einheitliches JSON-Logging, MongoDB-Zähler und Heartbeats für Kraken, Aladin und webapi.

## Einbau

```python
from fc_telemetry import setup_logging, install_mongo_activity, Heartbeat

setup_logging("kraken", level="INFO", fmt="json")   # vor dem ersten Log
activity = install_mongo_activity()                  # VOR dem ersten MongoClient
hb = Heartbeat("kraken", mongo_uri, activity=activity)
hb.start()                                           # Daemon-Thread, alle 5 s
```

Installation per Tag: `fc-telemetry @ git+https://github.com/marvin-fritz/fc-telemetry.git@v0.1.1`
