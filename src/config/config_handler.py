import os
import yaml
from typing import Any

class ConfigHandler:
    _instance = None
    _config = None
    
    # Default configuration sets for the simulation (kept in sync with config.yaml)
    DEFAULT_CONFIG = {
        'simulation': {
            'schedulers': ['static', 'dynamic_adab', 'dynamic_acab'],
            'min_buoys': 20,
            'max_buoys': 140,
            'step_buoys': 20,
            'intervals': [1.0, 0.5, 0.25],
            'duration': 100,
            'num_processes': 10,
            'ideal_channel': False,
            'scenario': 'static',           # Options: static, ramp, random
            'random_variability': 0.10,     # Fraction of total buoys that can change per update in random scenario
            'enable_metrics': True,
            'enable_logging': False,
            'enable_file_logging': False,
            'multihop_mode': 'append',      # Options: none, append, forwarded
            'multihop_modes': ['none', 'append', 'forwarded'],  # Modes swept and compared per run
            'multihop_limit': 1,            # Maximum hops for forwarded mode
            'append_hop_limit': 2,          # Append mode: max hop distance advertised (1 = direct only, 0 = unlimited)
            'pending_queue_limit': 20,      # Maximum number of beacons that can be stored in pending queue
            'forward_density_baseline': 5,  # Expected forwarders per cascade (probabilistic gate)
        },
        'world': {
            'width': 500.0,
            'height': 500.0
        },
        'buoys': {
            'mobile_percentage': 0.4,
            'default_velocity': 15.0,
            'rwp_speed_min': 5.0,           # m/s - lower bound per leg
            'rwp_speed_max': 20.0,          # m/s - upper bound per leg
            'rwp_pause_min': 0.0,           # seconds - minimum pause at waypoint
            'rwp_pause_max': 10.0,          # seconds - maximum pause at waypoint
        },
        'network': {
            'bit_rate': 1000000,
            'speed_of_light': 300000000.0,
            'communication_range_max': 80.0,
            'communication_range_high_prob': 70.0,
            'delivery_prob_high': 0.9,
            'delivery_prob_low': 0.15
        },
        'csma': {
            'slot_time': 0.000020,
            'difs_time': 0.000050,
            'cw': 16
        },
        'scheduler': {
            'static_interval': 1.0,
            'beacon_min_interval': 1.0,
            'beacon_max_interval': 5.0,
            'adab_neighbors_threshold': 15.0,   # ADAB: neighbour count that maps to density factor = 1.0 (full backoff)
            'acab_neighbors_threshold': 10.0,   # ACAB: neighbour count that maps density component to 1.0
            'acab_contact_threshold': 20.0,     # ACAB: seconds since last contact at which contact-recency score decays to 0
            'acab_weights': [0.4, 0.3, 0.3],    # ACAB blend weights: [density, contact, mobility] - must sum to 1.0
        }
    }
    
    def __new__(cls):
        # Singleton pattern to ensure only one instance of ConfigHandler exists
        if cls._instance is None:
            cls._instance = super(ConfigHandler, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        # Load configuration on initialization if it hasn't been loaded yet
        if self._config is None:
            self._load_config()
    
    def _load_config(self):
        # Load configuration from file if it exists
        config_path = 'config.yaml'
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                self._config = yaml.safe_load(f)
        # If config file doesn't exist, create it with default values
        else:
            self._config = self.DEFAULT_CONFIG.copy()
            with open(config_path, 'w') as f:
                yaml.dump(self.DEFAULT_CONFIG, f, default_flow_style=False, sort_keys=False)
    
    # Getter of configuration values
    def get(self, section: str, key: str) -> Any:
        # Special case: neighbor_timeout is calculated as 3 * static_interval
        if section == 'scheduler' and key == 'neighbor_timeout':
            static_interval = self._config.get('scheduler', {}).get('static_interval', 1.0)
            return 3.0 * static_interval
        return self._config.get(section, {}).get(key)

    # Setter to override a configuration value at runtime (in-process only).
    # Used to inject per-simulation parameters (e.g. multihop_mode) that must be
    # honored by behavior code reading directly from the config singleton.
    def set(self, section: str, key: str, value: Any) -> None:
        self._config.setdefault(section, {})[key] = value